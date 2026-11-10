{-# LANGUAGE QuasiQuotes #-}

module AWS.Lambda.Events.SQS.BatchResponse.Spec where

import           AWS.Lambda.Events.SQS               (Records (..),
                                                      SQSEvent (..))
import           AWS.Lambda.Events.SQS.BatchResponse
import           Data.Aeson                          (ToJSON, Value, decode,
                                                      eitherDecode, encode,
                                                      object, toJSON, (.=))
import           Data.ByteString.Lazy                (ByteString)
import           Data.Text                           (Text)
import qualified Data.Text.Lazy                      as TL
import qualified Data.Text.Lazy.Encoding             as TLE
import           Hedgehog                            (forAll, (===))
import qualified Hedgehog.Gen                        as Gen
import qualified Hedgehog.Range                      as Range
import           Test.Hspec                          (Spec, describe,
                                                      shouldBe, specify)
import           Test.Hspec.Hedgehog                 (hedgehog)
import           Text.RawString.QQ                   (r)

spec :: Spec
spec = do
  describe "encoding" $ do
    specify "no failures encode as an empty list" $
      shouldEncodeAs (BatchResponse []) "{\"batchItemFailures\":[]}"

    specify "one failure" $
      shouldEncodeAs
        (BatchResponse [BatchItemFailure "id2"])
        "{\"batchItemFailures\":[{\"itemIdentifier\":\"id2\"}]}"

    specify "multiple failures keep their order" $
      shouldEncodeAs
        (BatchResponse [BatchItemFailure "id4", BatchItemFailure "id2"])
        "{\"batchItemFailures\":[{\"itemIdentifier\":\"id4\"},{\"itemIdentifier\":\"id2\"}]}"

    specify "unicode identifiers are written as UTF-8" $
      shouldEncodeAs
        (BatchResponse
          [ BatchItemFailure "caf\233"
          , BatchItemFailure "\26085\26412"
          , BatchItemFailure "\128512"
          ])
        (utf8 "{\"batchItemFailures\":[{\"itemIdentifier\":\"caf\233\"},{\"itemIdentifier\":\"\26085\26412\"},{\"itemIdentifier\":\"\128512\"}]}")

    specify "a single failure encodes on its own" $
      shouldEncodeAs (BatchItemFailure "id2") "{\"itemIdentifier\":\"id2\"}"

    specify "toJSON and toEncoding agree" $
      hedgehog $ do
        identifiers <- forAll $
          Gen.list (Range.linear 0 20) (Gen.text (Range.linear 0 40) Gen.unicode)
        let response = BatchResponse (BatchItemFailure <$> identifiers)
        encode response === encode (toJSON response)
        decode (encode response) === Just (expectedValue identifiers)

  describe "responding to an SQS batch" $
    specify "reports only the failed messages of a decoded batch" $ do
      let failed = ["22222222-2222-2222-2222-222222222222"]
      fmap (failuresFor failed) (eitherDecode threeMessages)
        `shouldBe` Right
          (BatchResponse
            [BatchItemFailure "22222222-2222-2222-2222-222222222222"])
      fmap (encode . failuresFor failed) (eitherDecode threeMessages)
        `shouldBe` Right
          "{\"batchItemFailures\":[{\"itemIdentifier\":\"22222222-2222-2222-2222-222222222222\"}]}"

-- | Checks the exact bytes of 'encode', and that 'toJSON' produces the same
-- value as parsing those bytes.
shouldEncodeAs :: ToJSON a => a -> ByteString -> IO ()
shouldEncodeAs value expected = do
  encode value `shouldBe` expected
  encode (toJSON value) `shouldBe` expected
  Just (toJSON value) `shouldBe` (decode expected :: Maybe Value)

utf8 :: TL.Text -> ByteString
utf8 = TLE.encodeUtf8

expectedValue :: [Text] -> Value
expectedValue identifiers =
  object
    [ "batchItemFailures" .=
        [ object [ "itemIdentifier" .= identifier ] | identifier <- identifiers ]
    ]

failuresFor :: [Text] -> Records -> BatchResponse
failuresFor failed (Records messages) =
  BatchResponse
    [ BatchItemFailure (messageId message)
    | message <- messages
    , messageId message `elem` failed
    ]

threeMessages :: ByteString
threeMessages = [r|
{
  "Records": [
    {
      "messageId": "11111111-1111-1111-1111-111111111111",
      "receiptHandle": "handle-1",
      "body": "first",
      "attributes": {
        "ApproximateReceiveCount": "1",
        "SentTimestamp": "1523232000000",
        "SenderId": "123456789012",
        "ApproximateFirstReceiveTimestamp": "1523232000001"
      },
      "messageAttributes": {},
      "md5OfBody": "8b04d5e3775d298e78455efc5ca404d5",
      "eventSource": "aws:sqs",
      "eventSourceARN": "arn:aws:sqs:us-east-1:123456789012:queue1",
      "awsRegion": "us-east-1"
    },
    {
      "messageId": "22222222-2222-2222-2222-222222222222",
      "receiptHandle": "handle-2",
      "body": "second",
      "attributes": {
        "ApproximateReceiveCount": "1",
        "SentTimestamp": "1523232000000",
        "SenderId": "123456789012",
        "ApproximateFirstReceiveTimestamp": "1523232000001"
      },
      "messageAttributes": {},
      "md5OfBody": "a9f0e61a137d86aa9db53465e0801612",
      "eventSource": "aws:sqs",
      "eventSourceARN": "arn:aws:sqs:us-east-1:123456789012:queue1",
      "awsRegion": "us-east-1"
    },
    {
      "messageId": "33333333-3333-3333-3333-333333333333",
      "receiptHandle": "handle-3",
      "body": "third",
      "attributes": {
        "ApproximateReceiveCount": "1",
        "SentTimestamp": "1523232000000",
        "SenderId": "123456789012",
        "ApproximateFirstReceiveTimestamp": "1523232000001"
      },
      "messageAttributes": {},
      "md5OfBody": "dd5c8bf51558ffcbe5007071908e9524",
      "eventSource": "aws:sqs",
      "eventSourceARN": "arn:aws:sqs:us-east-1:123456789012:queue1",
      "awsRegion": "us-east-1"
    }
  ]
}
|]
