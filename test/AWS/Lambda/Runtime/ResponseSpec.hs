module AWS.Lambda.Runtime.ResponseSpec (spec) where

import           AWS.Lambda.Runtime              (pureRuntime)
import           AWS.Lambda.Runtime.EncodingSpec (Call (..), Expected (..),
                                                  Invocation (..),
                                                  ResponseBehavior (..),
                                                  invocation, runAgainstFakeApi,
                                                  runAgainstFakeApiExpectingStop,
                                                  shouldMatchCalls)
import           Data.Aeson                      (Value)
import           Test.Hspec                      (Spec, describe,
                                                  expectationFailure, it)

spec :: Spec
spec =
  describe "response delivery" $ do
    it "reports HTTP 413 as an invocation error and continues serving invocations" $ do
      let handler :: Value -> String
          handler _ = "ok"
      calls <- runAgainstFakeApi
        [ (invocation "req-1" "{}") { responseBehavior = RejectResponse "413 Payload Too Large" }
        , invocation "req-2" "{}"
        ]
        (pureRuntime handler)
      calls `shouldMatchCalls`
        [ ExpectNext
        , ExpectSuccess "req-1" "\"ok\""
        , ExpectFailure "req-1" "Payload Too Large"
        , ExpectNext
        , ExpectSuccess "req-2" "\"ok\""
        , ExpectNext
        ]

    it "stops the runtime instead of posting an invocation error on HTTP 500" $ do
      let handler :: Value -> String
          handler _ = "ok"
      calls <- runAgainstFakeApiExpectingStop
        (Just "Unexpected Runtime Error: Could not post handler result.")
        [ (invocation "req-1" "{}") { responseBehavior = RejectResponse "500 Internal Server Error" }
        , invocation "req-2" "{}"
        ]
        (pureRuntime handler)
      calls `shouldMatchCalls`
        [ ExpectNext
        , ExpectSuccess "req-1" "\"ok\""
        ]

    it "stops the runtime instead of posting an invocation error on other 4xx responses" $ do
      let handler :: Value -> String
          handler _ = "ok"
      calls <- runAgainstFakeApiExpectingStop
        (Just "Unexpected Runtime Error: Could not post handler result.")
        [ (invocation "req-1" "{}") { responseBehavior = RejectResponse "400 Bad Request" }
        , invocation "req-2" "{}"
        ]
        (pureRuntime handler)
      calls `shouldMatchCalls`
        [ ExpectNext
        , ExpectSuccess "req-1" "\"ok\""
        ]

    it "stops the runtime instead of posting an invocation error on response transport failure" $ do
      let handler :: Value -> String
          handler _ = "ok"
      calls <- runAgainstFakeApiExpectingStop
        (Just "HttpExceptionRequest")
        [ (invocation "req-1" "{}") { responseBehavior = DropResponseConnection }
        , invocation "req-2" "{}"
        ]
        (pureRuntime handler)
      case calls of
        Next : attempts@(_ : _) | all isReq1Success attempts -> return ()
        _ ->
          expectationFailure $
            "expected only Next followed by response attempts for req-1, but received "
              ++ show calls
  where
    isReq1Success (Success "req-1" "\"ok\"") = True
    isReq1Success _                          = False
