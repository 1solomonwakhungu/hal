{-# LANGUAGE CPP #-}
module AWS.Lambda.Runtime.EncodingSpec where

import           AWS.Lambda.Runtime        (fallibleRuntime, ioRuntime,
                                            pureRuntime)
import           Control.Concurrent        (ThreadId, forkIO, killThread,
                                            threadDelay)
import           Control.Concurrent.MVar   (MVar, modifyMVar, modifyMVar_,
                                            newEmptyMVar, newMVar, readMVar,
                                            takeMVar, tryPutMVar)
import           Control.Exception         (AsyncException (ThreadKilled),
                                            SomeException, bracket,
                                            displayException, finally,
                                            throw, throwIO, try)
import           Control.Monad             (forever, unless, void)
import           Data.Aeson                (ToJSON (..), Value,
                                            eitherDecode, object, pairs,
                                            withObject, (.:), (.=))
import           Data.Aeson.Types          (parseEither)
import qualified Data.ByteString           as BS
import qualified Data.ByteString.Char8     as BSC
import qualified Data.ByteString.Lazy      as BSL
import           Data.Char                 (toLower)
import           Data.List                 (isInfixOf)
#if !MIN_VERSION_base(4,11,0)
import           Data.Semigroup            ((<>))
#endif
import           Data.Text                 (Text)
import           Network.Socket            (Family (AF_INET), SockAddr (..),
                                            Socket, SocketOption (ReuseAddr),
                                            SocketType (Stream), accept, bind,
                                            close, defaultProtocol, listen,
                                            setSocketOption, socket,
                                            socketPort, tupleToHostAddress,
                                            withSocketsDo)
import           Network.Socket.ByteString (recv, sendAll)
import           System.Environment        (lookupEnv, setEnv, unsetEnv)
import           System.Timeout            (timeout)
import           Test.Hspec                (Expectation, Spec, describe,
                                            expectationFailure, it)
import           Text.Read                 (readMaybe)

spec :: Spec
spec =
  describe "response encoding" $ do
    it "reports an exception in a lazily evaluated result as an invocation error" $ do
      let handler :: Value -> String
          handler event =
            if event == explode
              then "prefix " ++ error "lazy result exploded"
              else "ok"
      calls <- runAgainstFakeApi
        [invocation "req-1" "{\"explode\":true}", invocation "req-2" "{}"]
        (pureRuntime handler)
      calls `shouldMatchCalls`
        [ ExpectNext
        , ExpectFailure "req-1" "lazy result exploded"
        , ExpectNext
        , ExpectSuccess "req-2" "\"ok\""
        , ExpectNext
        ]

    it "reports an exception from a ToJSON instance as an invocation error" $ do
      let handler :: Value -> IO (Either String Reply)
          handler event =
            pure $ Right $ if event == explode then Exploding else Fine
      calls <- runAgainstFakeApi
        [invocation "req-1" "{\"explode\":true}", invocation "req-2" "{}"]
        (ioRuntime handler)
      calls `shouldMatchCalls`
        [ ExpectNext
        , ExpectFailure "req-1" "reply encoding exploded"
        , ExpectNext
        , ExpectSuccess "req-2" "\"fine\""
        , ExpectNext
        ]

    it "propagates asynchronous encoding exceptions without posting an error" $ do
      let handler :: Value -> Reply
          handler _ = Cancelled
      calls <- runAgainstFakeApiExpectingStop (Just "thread killed")
        [invocation "req-1" "{}"] (pureRuntime handler)
      calls `shouldMatchCalls` [ExpectNext]

    it "does not evaluate parts of the result that are not encoded" $ do
      let handler :: Value -> Partial
          handler _ = Partial
            { shown = "visible"
            , hidden = error "hidden field was evaluated"
            }
      calls <- runAgainstFakeApi
        [invocation "req-1" "{}", invocation "req-2" "{}"]
        (pureRuntime handler)
      calls `shouldMatchCalls`
        [ ExpectNext
        , ExpectSuccess "req-1" "{\"shown\":\"visible\"}"
        , ExpectNext
        , ExpectSuccess "req-2" "{\"shown\":\"visible\"}"
        , ExpectNext
        ]

    it "still reports exceptions thrown by an IO handler" $ do
      let handler :: Value -> IO (Either String String)
          handler event =
            if event == explode
              then throwIO (userError "io handler failed")
              else pure (Right "ok")
      calls <- runAgainstFakeApi
        [invocation "req-1" "{\"explode\":true}", invocation "req-2" "{}"]
        (ioRuntime handler)
      calls `shouldMatchCalls`
        [ ExpectNext
        , ExpectFailure "req-1" "io handler failed"
        , ExpectNext
        , ExpectSuccess "req-2" "\"ok\""
        , ExpectNext
        ]

    it "still reports an explicit Left as an invocation error" $ do
      let handler :: Value -> Either String String
          handler event =
            if event == explode
              then Left "explicit failure"
              else Right "ok"
      calls <- runAgainstFakeApi
        [invocation "req-1" "{\"explode\":true}", invocation "req-2" "{}"]
        (fallibleRuntime handler)
      calls `shouldMatchCalls`
        [ ExpectNext
        , ExpectFailure "req-1" "explicit failure"
        , ExpectNext
        , ExpectSuccess "req-2" "\"ok\""
        , ExpectNext
        ]

    it "still reports input that cannot be parsed as an invocation error" $ do
      let handler :: [Int] -> Int
          handler = sum
      calls <- runAgainstFakeApi
        [invocation "req-1" "{\"not\":\"a list\"}", invocation "req-2" "[1,2,3]"]
        (pureRuntime handler)
      calls `shouldMatchCalls`
        [ ExpectNext
        , ExpectFailure "req-1" ""
        , ExpectNext
        , ExpectSuccess "req-2" "6"
        , ExpectNext
        ]

    it "keeps the existing handling of a response the Runtime API rejects" $ do
      let handler :: Value -> String
          handler _ = "ok"
      calls <- runAgainstFakeApi
        [ (invocation "req-1" "{}") { rejectResponse = True }
        , invocation "req-2" "{}"
        ]
        (pureRuntime handler)
      calls `shouldMatchCalls`
        [ ExpectNext
        , ExpectSuccess "req-1" "\"ok\""
        , ExpectFailure "req-1" "Could not post handler result"
        , ExpectNext
        , ExpectSuccess "req-2" "\"ok\""
        , ExpectNext
        ]

explode :: Value
explode = object ["explode" .= True]

-- | A result whose encoding fails for one of its constructors.
data Reply = Fine | Exploding | Cancelled

instance ToJSON Reply where
  toJSON Fine      = toJSON ("fine" :: Text)
  toJSON Exploding = error "reply encoding exploded"
  toJSON Cancelled = throw ThreadKilled
  toEncoding Fine      = toEncoding ("fine" :: Text)
  toEncoding Exploding = error "reply encoding exploded"
  toEncoding Cancelled = throw ThreadKilled

-- | A result with a field that its 'ToJSON' instance never writes out.
data Partial = Partial
  { shown  :: Text
  , hidden :: Text
  }

instance ToJSON Partial where
  toJSON p = object ["shown" .= shown p]
  toEncoding p = pairs ("shown" .= shown p)

-- Fake Runtime API

data Invocation = Invocation
  { invocationId   :: BS.ByteString
  , invocationBody :: BS.ByteString
  , rejectResponse :: Bool
  }

invocation :: BS.ByteString -> BS.ByteString -> Invocation
invocation reqId body = Invocation reqId body False

-- | A request received by the fake Runtime API.
data Call
  = Next
  | Success BS.ByteString BS.ByteString
  | Failure BS.ByteString String
  | InitFailure String
  | Unexpected BS.ByteString BS.ByteString
  deriving (Show)

data Expected
  = ExpectNext
  | ExpectSuccess BS.ByteString BS.ByteString
  -- | The request id, and a fragment the error message must contain.
  | ExpectFailure BS.ByteString String
  deriving (Show)

matches :: Expected -> Call -> Bool
matches ExpectNext Next = True
matches (ExpectSuccess reqId body) (Success reqId' body') =
  reqId == reqId' && body == body'
matches (ExpectFailure reqId fragment) (Failure reqId' message) =
  reqId == reqId' && fragment `isInfixOf` message
matches _ _ = False

shouldMatchCalls :: [Call] -> [Expected] -> Expectation
shouldMatchCalls calls expected =
  unless (length calls == length expected && and (zipWith matches expected calls)) $
    expectationFailure $
      "expected the Runtime API to receive " ++ show expected
        ++ " but it received " ++ show calls

data Outcome = Drained | RuntimeStopped String

-- | Run a runtime against a local fake of the Lambda Runtime API, serving the
-- given invocations in order.  Returns every request the runtime made once it
-- asks for an invocation after the last one.
runAgainstFakeApi :: [Invocation] -> IO () -> IO [Call]
runAgainstFakeApi = runAgainstFakeApiExpectingStop Nothing

runAgainstFakeApiExpectingStop :: Maybe String -> [Invocation] -> IO () -> IO [Call]
runAgainstFakeApiExpectingStop expectedStop invocations runtime = withLambdaEnvironment $ withSocketsDo $ do
  pending <- newMVar invocations
  calls <- newMVar []
  outcome <- newEmptyMVar
  workers <- newMVar []
  bracket listenOnLoopback close $ \listener -> do
    port <- socketPort listener
    setEnv "AWS_LAMBDA_RUNTIME_API" ("127.0.0.1:" ++ show (fromIntegral port :: Int))
    setEnv "AWS_LAMBDA_FUNCTION_NAME" "test-function"
    setEnv "AWS_LAMBDA_FUNCTION_VERSION" "$LATEST"
    setEnv "AWS_LAMBDA_FUNCTION_MEMORY_SIZE" "128"
    setEnv "AWS_LAMBDA_LOG_GROUP_NAME" "test-log-group"
    setEnv "AWS_LAMBDA_LOG_STREAM_NAME" "test-log-stream"

    let rejected = [invocationId inv | inv <- invocations, rejectResponse inv]
    acceptor <- forkIO $ acceptConnections listener workers $
      serveConnection rejected pending calls outcome
    runner <- forkIO $ do
      result <- try runtime
      void $ tryPutMVar outcome $ RuntimeStopped $
        either (displayException :: SomeException -> String)
               (const "the runtime returned")
               result

    let cleanup = do
          killThread runner
          killThread acceptor
          mapM_ killThread =<< readMVar workers
    finished <- timeout 20000000 (takeMVar outcome) `finally` cleanup

    recorded <- reverse <$> readMVar calls
    case finished of
      Just Drained | expectedStop == Nothing -> return recorded
      Just (RuntimeStopped reason) | expectedStop == Just reason -> return recorded
      Just Drained -> do
        expectationFailure "the runtime continued after an asynchronous exception"
        return recorded
      Just (RuntimeStopped reason) -> do
        expectationFailure $
          "the runtime stopped (" ++ reason ++ ") after the Runtime API received "
            ++ show recorded
        return recorded
      Nothing -> do
        expectationFailure $
          "timed out after the Runtime API received " ++ show recorded
        return recorded

listenOnLoopback :: IO Socket
listenOnLoopback = do
  sock <- socket AF_INET Stream defaultProtocol
  setSocketOption sock ReuseAddr 1
  bind sock (SockAddrInet 0 (tupleToHostAddress (127, 0, 0, 1)))
  listen sock 8
  return sock

acceptConnections :: Socket -> MVar [ThreadId] -> (Socket -> IO ()) -> IO ()
acceptConnections listener workers serve = forever $ do
  (conn, _) <- accept listener
  worker <- forkIO (serve conn `finally` close conn)
  modifyMVar_ workers (return . (worker :))

serveConnection
  :: [BS.ByteString]
  -> MVar [Invocation]
  -> MVar [Call]
  -> MVar Outcome
  -> Socket
  -> IO ()
serveConnection rejected pending calls outcome conn = loop BS.empty
  where
    loop buffered = do
      request <- readRequest conn buffered
      case request of
        Nothing -> return ()
        Just (method, target, body, rest) -> do
          let call = toCall method target body
          modifyMVar_ calls (return . (call :))
          case call of
            Next -> do
              next <- modifyMVar pending $ \queue -> return $ case queue of
                []         -> ([], Nothing)
                (x : more) -> (more, Just x)
              case next of
                Nothing -> do
                  -- Leave the runtime waiting, as the real API does when
                  -- there is no work, until the test stops it.
                  void $ tryPutMVar outcome Drained
                  forever $ threadDelay 1000000
                Just inv -> do
                  sendResponse conn "200 OK" (invocationHeaders (invocationId inv))
                    (invocationBody inv)
                  loop rest
            Success reqId _ -> do
              if reqId `elem` rejected
                then sendResponse conn "500 Internal Server Error" [] "{}"
                else sendResponse conn "202 Accepted" [] "{\"status\":\"OK\"}"
              loop rest
            _ -> do
              sendResponse conn "202 Accepted" [] "{\"status\":\"OK\"}"
              loop rest

-- | Read one HTTP request, returning its method, target, body and any bytes
-- received after it.  Returns 'Nothing' once the client closes the connection.
readRequest
  :: Socket
  -> BS.ByteString
  -> IO (Maybe (BS.ByteString, BS.ByteString, BS.ByteString, BS.ByteString))
readRequest conn = readHead
  where
    readHead buffered =
      let (headPart, rest) = BS.breakSubstring "\r\n\r\n" buffered
      in if BS.null rest
        then receiveMore buffered readHead
        else
          let headerLines = map stripCR (BSC.lines headPart)
              afterHead = BS.drop 4 rest
          in case headerLines of
            (requestLine : headers) | [method, target, _] <- BSC.words requestLine ->
              readBody method target (contentLength headers) afterHead
            _ -> return Nothing

    readBody method target len buffered
      | BS.length buffered >= len =
          let (body, rest) = BS.splitAt len buffered
          in return (Just (method, target, body, rest))
      | otherwise = receiveMore buffered (readBody method target len)

    receiveMore buffered continue = do
      chunk <- recv conn 4096
      if BS.null chunk
        then return Nothing
        else continue (buffered <> chunk)

    stripCR line
      | not (BS.null line) && BSC.last line == '\r' = BS.init line
      | otherwise = line

    contentLength headers =
      case [ value | (name, value) <- map splitHeader headers
                   , BSC.map toLower name == "content-length" ] of
        (value : _) -> maybe 0 id (readMaybe (BSC.unpack value))
        []          -> 0

    splitHeader header =
      let (name, value) = BSC.break (== ':') header
      in (name, BS.drop 1 value)

sendResponse :: Socket -> BS.ByteString -> [(BS.ByteString, BS.ByteString)] -> BS.ByteString -> IO ()
sendResponse conn status headers body =
  sendAll conn $ BS.concat $
    [ "HTTP/1.1 ", status, "\r\n"
    , "Content-Type: application/json\r\n"
    , "Content-Length: ", BSC.pack (show (BS.length body)), "\r\n"
    ]
    ++ concatMap (\(name, value) -> [name, ": ", value, "\r\n"]) headers
    ++ ["\r\n", body]

invocationHeaders :: BS.ByteString -> [(BS.ByteString, BS.ByteString)]
invocationHeaders reqId =
  [ ("Lambda-Runtime-Aws-Request-Id", reqId)
  , ("Lambda-Runtime-Deadline-Ms", "4102444800000")
  , ("Lambda-Runtime-Invoked-Function-Arn", "arn:aws:lambda:us-east-1:000000000000:function:test-function")
  , ("Lambda-Runtime-Trace-Id", "Root=1-00000000-000000000000000000000000;Sampled=0")
  ]

toCall :: BS.ByteString -> BS.ByteString -> BS.ByteString -> Call
toCall method target body =
  case (method, BSC.split '/' (BSC.dropWhile (== '/') target)) of
    ("GET", ["2018-06-01", "runtime", "invocation", "next"]) ->
      Next
    ("POST", ["2018-06-01", "runtime", "invocation", reqId, "response"]) ->
      Success reqId body
    ("POST", ["2018-06-01", "runtime", "invocation", reqId, "error"]) ->
      Failure reqId (errorMessageOf body)
    ("POST", ["2018-06-01", "runtime", "init", "error"]) ->
      InitFailure (errorMessageOf body)
    _ ->
      Unexpected method target

errorMessageOf :: BS.ByteString -> String
errorMessageOf body =
  either ("undecodable error body: " ++) id $ do
    value <- eitherDecode (BSL.fromStrict body)
    parseEither (withObject "LambdaError" (.: "errorMessage")) value

-- The runtime writes the trace variable as well as reading its configuration.
-- Restore all of them so these examples do not affect later tests.
withLambdaEnvironment :: IO a -> IO a
withLambdaEnvironment action =
  bracket (mapM save names) (mapM_ restore) (const action)
  where
    names =
      [ "AWS_LAMBDA_RUNTIME_API"
      , "AWS_LAMBDA_FUNCTION_NAME"
      , "AWS_LAMBDA_FUNCTION_VERSION"
      , "AWS_LAMBDA_FUNCTION_MEMORY_SIZE"
      , "AWS_LAMBDA_LOG_GROUP_NAME"
      , "AWS_LAMBDA_LOG_STREAM_NAME"
      , "_X_AMZN_TRACE_ID"
      ]
    save name = (,) name <$> lookupEnv name
    restore (name, value) = maybe (unsetEnv name) (setEnv name) value
