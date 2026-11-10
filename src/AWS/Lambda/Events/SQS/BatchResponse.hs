{-|
Module      : AWS.Lambda.Events.SQS.BatchResponse
Description : Partial batch responses for SQS event source mappings.
License     : BSD3
Maintainer  : nathan.fairhurst@nike.com, fernando.freire@nike.com
Stability   : stable

A Lambda function that consumes an SQS queue can report which messages in a
batch it failed to process. Lambda then makes only those messages visible in
the queue again, instead of retrying the whole batch. AWS calls this a
partial batch response. See the
<https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-errorhandling.html#services-sqs-batchfailurereporting AWS documentation>.

This module only provides the response value. Processing the messages, and
deciding which of them failed, is left to the handler.

= Enabling partial batch responses

Lambda reads the response only when the event source mapping lists
@ReportBatchItemFailures@ in its @FunctionResponseTypes@, for example with

> aws lambda update-event-source-mapping \
>   --uuid <mapping-uuid> \
>   --function-response-types ReportBatchItemFailures

= Example

@
module Main where

import AWS.Lambda.Events.SQS               (Records (..), SQSEvent (..))
import AWS.Lambda.Events.SQS.BatchResponse (BatchItemFailure (..),
                                            BatchResponse (..))
import AWS.Lambda.Runtime                  (mRuntime)
import Data.Maybe                          (catMaybes)

-- Returns 'Left' when the message could not be processed.
processMessage :: SQSEvent -> IO (Either String ())
processMessage = ...

handler :: Records -> IO BatchResponse
handler (Records messages) = do
  failures <- traverse reportFailure messages
  pure (BatchResponse (catMaybes failures))
  where
    reportFailure message = do
      result <- processMessage message
      pure $ case result of
        Left _   -> Just (BatchItemFailure (messageId message))
        Right () -> Nothing

main :: IO ()
main = mRuntime handler
@

A complete version lives in the @examples/sqsbatch@ directory of the
repository.

= Things to keep in mind

* If the handler throws an exception or otherwise reports an invocation
  error (for example a 'Left' returned to 'AWS.Lambda.Runtime.ioRuntime' or
  'AWS.Lambda.Runtime.fallibleRuntime'),
  Lambda treats the whole batch as failed and every message becomes visible
  again. Failures that should affect only one message have to be turned into
  a 'BatchItemFailure' by the handler.

* An empty 'batchItemFailures' list means the whole batch succeeded.

* Each 'itemIdentifier' must be the 'AWS.Lambda.Events.SQS.messageId' of a
  message from the same batch. Lambda treats the whole batch as failed when an
  identifier is empty or does not match a message in the batch.

* For a FIFO queue, stop processing at the first failure and return that
  message together with all unprocessed messages. This helps preserve
  the ordering of messages in the queue.
-}
module AWS.Lambda.Events.SQS.BatchResponse (
  BatchResponse (..),
  BatchItemFailure (..)
) where

import           Data.Aeson   (ToJSON (..), object, pairs, (.=))
import           Data.Text    (Text)
import           GHC.Generics (Generic)

-- | The value a handler returns to report a partial batch failure.
--
-- Encodes as
--
-- > {"batchItemFailures":[{"itemIdentifier":"id2"},{"itemIdentifier":"id4"}]}
--
-- An empty list encodes as @{"batchItemFailures":[]}@.
newtype BatchResponse = BatchResponse {
  batchItemFailures :: [BatchItemFailure]
} deriving (Show, Eq, Generic)

instance ToJSON BatchResponse where
  toJSON (BatchResponse failures) =
    object [ "batchItemFailures" .= failures ]
  toEncoding (BatchResponse failures) =
    pairs ( "batchItemFailures" .= failures )

-- | A message that the handler failed to process. 'itemIdentifier' is the
-- message's 'AWS.Lambda.Events.SQS.messageId'.
newtype BatchItemFailure = BatchItemFailure {
  itemIdentifier :: Text
} deriving (Show, Eq, Generic)

instance ToJSON BatchItemFailure where
  toJSON (BatchItemFailure identifier) =
    object [ "itemIdentifier" .= identifier ]
  toEncoding (BatchItemFailure identifier) =
    pairs ( "itemIdentifier" .= identifier )
