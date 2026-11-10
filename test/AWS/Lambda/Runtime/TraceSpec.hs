{-# LANGUAGE CPP #-}
module AWS.Lambda.Runtime.TraceSpec (spec) where

import           AWS.Lambda.Context        (LambdaContext (..))
import           AWS.Lambda.Runtime        (ioRuntimeWithContext)
import           Control.Concurrent        (forkIO, killThread, modifyMVar,
                                            modifyMVar_,
                                            newEmptyMVar, newMVar, readMVar,
                                            threadDelay, tryPutMVar)
import           Control.Exception         (bracket, finally)
import           Control.Monad             (forM_, forever, unless, void)
import           Data.Aeson                (Value (String), decode, object,
                                            (.=))
import qualified Data.ByteString           as BS
import qualified Data.ByteString.Char8     as BSC
import qualified Data.ByteString.Lazy      as BSL
import qualified Data.CaseInsensitive      as CI
import qualified Data.Map                  as Map
#if !MIN_VERSION_base(4,11,0)
import           Data.Semigroup            ((<>))
#endif
import           Data.Text                 (Text, pack, unpack)
import           Network.Socket            (Family (AF_INET), Socket,
                                            SockAddr (SockAddrInet),
                                            SocketOption (ReuseAddr),
                                            SocketType (Stream), accept, bind,
                                            close, defaultProtocol,
                                            getSocketName, listen,
                                            setSocketOption, socket,
                                            tupleToHostAddress, withSocketsDo)
import           Network.Socket.ByteString (recv, sendAll)
import           System.Environment        (lookupEnv, setEnv, unsetEnv)
import           System.Timeout            (timeout)
import           Test.Hspec                (Spec, describe, expectationFailure,
                                            it, shouldBe, shouldStartWith)

spec :: Spec
spec =
  describe "with a local Runtime API" $
    it "propagates the trace id of each invocation, including absent ones" $ do
      let invocations =
            [ Invocation "req-1" (Just traceA) "{\"n\":1}"
            , Invocation "req-2" Nothing "{\"n\":2}"
            , Invocation "req-3" (Just traceB) "{\"n\":3}"
            , Invocation "req-4" Nothing "{\"fail\":true}"
            ]

      posts <- withRuntimeApi invocations (ioRuntimeWithContext traceHandler)

      fmap fst posts `shouldBe`
        [ "/2018-06-01/runtime/invocation/req-1/response"
        , "/2018-06-01/runtime/invocation/req-2/response"
        , "/2018-06-01/runtime/invocation/req-3/response"
        , "/2018-06-01/runtime/invocation/req-4/error"
        ]

      fmap (decode . BSL.fromStrict . snd) (take 3 posts) `shouldBe`
        [ Just (observed "req-1" (decodeAscii traceA) (Just (BSC.unpack traceA)))
        , Just (observed "req-2" "" Nothing)
        , Just (observed "req-3" (decodeAscii traceB) (Just (BSC.unpack traceB)))
        ]

      case decode (BSL.fromStrict (snd (posts !! 3))) >>= Map.lookup ("errorMessage" :: Text) of
        Just (String msg) -> unpack msg `shouldStartWith` failureMessage "" Nothing
        other -> expectationFailure $ "Unexpected error body: " <> show other

traceA :: BS.ByteString
traceA = "Root=1-5759e988-bd862e3fe1be46a994272793;Parent=53995c3f42cd8ad8;Sampled=1"

traceB :: BS.ByteString
traceB = "Root=1-67891233-abcdef012345678912345678;Parent=0123456789abcdef;Sampled=0"

decodeAscii :: BS.ByteString -> Text
decodeAscii = pack . BSC.unpack

-- A handler that reports the trace id it sees, both from the context and from
-- the environment, failing when asked to.
traceHandler :: LambdaContext -> Value -> IO (Either String Value)
traceHandler ctx event = do
  traceEnv <- lookupEnv "_X_AMZN_TRACE_ID"
  return $
    if event == object ["fail" .= True]
      then Left (failureMessage (xRayTraceId ctx) traceEnv)
      else Right (observed (awsRequestId ctx) (xRayTraceId ctx) traceEnv)

observed :: Text -> Text -> Maybe String -> Value
observed requestId traceId traceEnv =
  object
    [ "requestId" .= requestId
    , "traceId" .= traceId
    , "traceEnv" .= traceEnv
    ]

failureMessage :: Text -> Maybe String -> String
failureMessage traceId traceEnv =
  "trace id: " <> show traceId <> ", trace env: " <> show traceEnv

-- Local Runtime API

data Invocation = Invocation
  { invocationRequestId :: BS.ByteString
  , invocationTraceId   :: Maybe BS.ByteString
  , invocationBody      :: BS.ByteString
  }

data Request = Request
  { requestMethod :: BS.ByteString
  , requestPath   :: BS.ByteString
  , requestBody   :: BS.ByteString
  }

-- | Serve the given invocations from a Runtime API bound to the loopback
-- interface, run the runtime against it until a result has been posted for
-- each of them, and return the posted results (path and body) in order.
withRuntimeApi :: [Invocation] -> IO () -> IO [(BS.ByteString, BS.ByteString)]
withRuntimeApi invocations runRuntime = withSocketsDo $ do
  pending <- newMVar invocations
  posts <- newMVar []
  done <- newEmptyMVar
  threads <- newMVar []

  let recordPost post = do
        count <- modifyMVar posts $ \ps -> let ps' = ps <> [post] in return (ps', length ps')
        unless (count < length invocations) $ void $ tryPutMVar done ()

      handle req
        | requestMethod req == "GET" && "/runtime/invocation/next" `BS.isSuffixOf` requestPath req = do
            next <- modifyMVar pending $ \is -> return $ case is of
              []       -> ([], Nothing)
              (i : is') -> (is', Just i)
            case next of
              -- No more events, so leave the runtime waiting
              Nothing -> forever $ threadDelay 1000000
              Just i -> return $ httpResponse "200 OK"
                ( [ ("Lambda-Runtime-Aws-Request-Id", invocationRequestId i)
                  , ("Lambda-Runtime-Invoked-Function-Arn", "arn:aws:lambda:us-east-1:123456789012:function:test")
                  , ("Lambda-Runtime-Deadline-Ms", "4102444800000")
                  , ("Content-Type", "application/json")
                  ] <> maybe [] (\t -> [("Lambda-Runtime-Trace-Id", t)]) (invocationTraceId i)
                )
                (invocationBody i)
        | requestMethod req == "POST" = do
            recordPost (requestPath req, requestBody req)
            return $ httpResponse "202 Accepted" [] ""
        | otherwise = return $ httpResponse "404 Not Found" [] ""

      serveConnection conn =
        let loop buffer = do
              mReq <- readRequest conn buffer
              forM_ mReq $ \(req, rest) -> do
                handle req >>= sendAll conn
                loop rest
        in loop BS.empty `finally` close conn

      addThread t = modifyMVar_ threads (return . (t :))

  bracket listenLocal close $ \listener -> do
    port <- getSocketName listener >>= \addr -> case addr of
      SockAddrInet p _ -> return (show p)
      _ -> fail "Unexpected socket address"

    acceptor <- forkIO $ forever $ do
      (conn, _) <- accept listener
      forkIO (serveConnection conn) >>= addThread
    addThread acceptor

    finished <- withLambdaEnvironment ("127.0.0.1:" <> port) $ do
      forkIO runRuntime >>= addThread
      timeout 10000000 (readMVar done)
        `finally` (readMVar threads >>= mapM_ killThread)

    results <- readMVar posts
    case finished of
      Nothing -> expectationFailure $
        "Timed out waiting for the runtime; received: " <> show results
      Just () -> return ()
    return results

listenLocal :: IO Socket
listenLocal = do
  sock <- socket AF_INET Stream defaultProtocol
  setSocketOption sock ReuseAddr 1
  bind sock (SockAddrInet 0 (tupleToHostAddress (127, 0, 0, 1)))
  listen sock 5
  return sock

-- Reads a single HTTP/1.1 request, returning it with any unconsumed input.
readRequest :: Socket -> BS.ByteString -> IO (Maybe (Request, BS.ByteString))
readRequest conn = go
  where
    go buffer =
      let (head', rest) = BS.breakSubstring "\r\n\r\n" buffer
      in if BS.null rest
        then more buffer go
        else do
          let headerLines = BSC.lines (BSC.filter (/= '\r') head')
              (method, path) = case BSC.words (BS.concat (take 1 headerLines)) of
                (m : p : _) -> (m, p)
                _           -> ("", "")
              headers = fmap (fmap (BSC.dropWhile (== ' ') . BS.drop 1) . BSC.break (== ':')) (drop 1 headerLines)
              contentLength = maybe 0 (maybe 0 fst . BSC.readInt) $
                lookup (CI.mk "Content-Length") (fmap (\(k, v) -> (CI.mk k, v)) headers)
          readBody (Request method path) contentLength (BS.drop 4 rest)

    readBody mkReq len buffer
      | BS.length buffer >= len =
          let (body, rest) = BS.splitAt len buffer
          in return (Just (mkReq body, rest))
      | otherwise = more buffer (readBody mkReq len)

    more buffer k = do
      chunk <- recv conn 4096
      if BS.null chunk then return Nothing else k (buffer <> chunk)

httpResponse :: BS.ByteString -> [(BS.ByteString, BS.ByteString)] -> BS.ByteString -> BS.ByteString
httpResponse status headers body =
  BS.concat $
    ["HTTP/1.1 ", status, "\r\n"]
    <> concatMap (\(k, v) -> [k, ": ", v, "\r\n"]) (("Content-Length", BSC.pack (show (BS.length body))) : headers)
    <> ["\r\n", body]

-- | Run an action with the environment a Lambda runtime provides, restoring
-- the previous environment afterwards.
withLambdaEnvironment :: String -> IO a -> IO a
withLambdaEnvironment runtimeApi action =
  bracket (mapM save (fmap fst vars)) (mapM_ restore) $ \_ -> do
    forM_ vars $ \(name, value) -> maybe (unsetEnv name) (setEnv name) value
    action
  where
    vars =
      [ ("AWS_LAMBDA_RUNTIME_API", Just runtimeApi)
      , ("AWS_LAMBDA_FUNCTION_NAME", Just "test")
      , ("AWS_LAMBDA_FUNCTION_VERSION", Just "$LATEST")
      , ("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", Just "128")
      , ("AWS_LAMBDA_LOG_GROUP_NAME", Just "/aws/lambda/test")
      , ("AWS_LAMBDA_LOG_STREAM_NAME", Just "stream")
      , ("_X_AMZN_TRACE_ID", Nothing)
      ]
    save name = (,) name <$> lookupEnv name
    restore (name, value) = maybe (unsetEnv name) (setEnv name) value
