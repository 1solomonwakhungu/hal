module Main where

-- Reports SQS messages that could not be processed as a partial batch
-- response, so that only those messages are retried.
--
-- The event source mapping must have ReportBatchItemFailures enabled in its
-- FunctionResponseTypes. If the handler throws instead of returning, Lambda
-- retries every message in the batch.
--
-- This handler suits a standard queue. For a FIFO queue, stop at the first
-- failure and report that message and every message after it.

import           AWS.Lambda.Events.SQS               (Records (..),
                                                      SQSEvent (..))
import           AWS.Lambda.Events.SQS.BatchResponse (BatchItemFailure (..),
                                                      BatchResponse (..))
import           AWS.Lambda.Runtime                  (mRuntime)
import           Data.Maybe                          (catMaybes)
import qualified Data.Text                           as Text
import           System.IO                           (hPutStrLn, stderr)
import           Text.Read                           (readMaybe)

-- Each message body is expected to hold a whole number.
processMessage :: SQSEvent -> IO (Either String ())
processMessage message =
  case readMaybe (Text.unpack (body message)) :: Maybe Integer of
    Nothing -> pure (Left "body is not a whole number")
    Just n  -> Right <$> putStrLn ("processed " ++ show n)

handler :: Records -> IO BatchResponse
handler (Records messages) = do
  failures <- traverse reportFailure messages
  pure (BatchResponse (catMaybes failures))
  where
    reportFailure message = do
      result <- processMessage message
      case result of
        Right () -> pure Nothing
        Left err -> do
          hPutStrLn stderr (Text.unpack (messageId message) ++ ": " ++ err)
          pure (Just (BatchItemFailure (messageId message)))

main :: IO ()
main = mRuntime handler
