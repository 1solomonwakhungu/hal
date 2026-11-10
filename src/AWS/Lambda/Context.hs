{-# LANGUAGE CPP            #-}
{-# LANGUAGE NamedFieldPuns #-}

{-|
Module      : AWS.Lambda.Context
Description : AWS Lambda Context classes and related methods.
Copyright   : (c) Nike, Inc., 2018
License     : BSD3
Maintainer  : nathan.fairhurst@nike.com, fernando.freire@nike.com
Stability   : stable
-}

module AWS.Lambda.Context (
  ClientApplication(..),
  ClientContext(..),
  CognitoIdentity(..),
  LambdaContext(..),
  getRemainingTime,
) where

import           Control.Monad.IO.Class (MonadIO, liftIO)
import           Data.Aeson             (FromJSON (..), Object, ToJSON,
                                         withObject, (.:), (.:!))
#if MIN_VERSION_aeson(2,0,0)
import           Data.Aeson.Key         (Key)
#endif
import           Data.Aeson.Types       (Parser)
import           Data.Map               (Map)
import           Data.Text              (Text)
import           Data.Time.Clock        (DiffTime, UTCTime,
                                         diffUTCTime, getCurrentTime)
import           GHC.Generics           (Generic)

#if !MIN_VERSION_aeson(2,0,0)
type Key = Text
#endif

data ClientApplication = ClientApplication
  { appTitle       :: Text,
    appVersionName :: Text,
    appVersionCode :: Text,
    appPackageName :: Text
  } deriving (Show, Generic, Eq)

instance ToJSON ClientApplication

-- | Accepts the field names used by AWS in the @client@ object of the
-- @Lambda-Runtime-Client-Context@ header (@app_title@, @app_version_name@,
-- @app_version_code@ and @app_package_name@), as well as the field names
-- produced by this type's 'ToJSON' instance. When both spellings of a field
-- are present, the AWS spelling is used.
instance FromJSON ClientApplication where
  parseJSON = withObject "ClientApplication" $ \o ->
    ClientApplication
      <$> fieldWithAlias o "app_title" "appTitle"
      <*> fieldWithAlias o "app_version_name" "appVersionName"
      <*> fieldWithAlias o "app_version_code" "appVersionCode"
      <*> fieldWithAlias o "app_package_name" "appPackageName"

data ClientContext = ClientContext
  { client      :: ClientApplication,
    custom      :: Map Text Text,
    environment :: Map Text Text
  } deriving (Show, Generic, Eq)

instance ToJSON ClientContext

-- | Accepts the @env@ field name used by AWS in the
-- @Lambda-Runtime-Client-Context@ header, as well as the @environment@ field
-- name produced by this type's 'ToJSON' instance. When both are present,
-- @env@ is used.
instance FromJSON ClientContext where
  parseJSON = withObject "ClientContext" $ \o ->
    ClientContext
      <$> o .: "client"
      <*> o .: "custom"
      <*> fieldWithAlias o "env" "environment"

data CognitoIdentity = CognitoIdentity
  { identityId     :: Text
  , identityPoolId :: Text
  } deriving (Show, Generic, Eq)

instance ToJSON CognitoIdentity

-- | Accepts the field names used by AWS in the
-- @Lambda-Runtime-Cognito-Identity@ header (@cognitoIdentityId@ and
-- @cognitoIdentityPoolId@), as well as the field names produced by this
-- type's 'ToJSON' instance. When both spellings of a field are present, the
-- AWS spelling is used.
instance FromJSON CognitoIdentity where
  parseJSON = withObject "CognitoIdentity" $ \o ->
    CognitoIdentity
      <$> fieldWithAlias o "cognitoIdentityId" "identityId"
      <*> fieldWithAlias o "cognitoIdentityPoolId" "identityPoolId"

-- Parses the value stored under the canonical key. The alias is only
-- consulted when the canonical key is absent: a canonical value that is
-- @null@ or of the wrong type is an error, and never falls back to the alias.
fieldWithAlias :: FromJSON a => Object -> Key -> Key -> Parser a
fieldWithAlias obj canonical alias = do
  canonicalValue <- obj .:! canonical
  case canonicalValue of
    Just value -> pure value
    Nothing -> do
      aliasValue <- obj .:! alias
      case aliasValue of
        Just value -> pure value
        Nothing    -> fail ("key " ++ show canonical ++ " not found")

getRemainingTime :: MonadIO m => LambdaContext -> m DiffTime
getRemainingTime LambdaContext { deadline } =
  liftIO $ fmap (realToFrac . diffUTCTime deadline) getCurrentTime

data LambdaContext = LambdaContext
  { functionName       :: Text,
    functionVersion    :: Text,
    functionMemorySize :: Int,
    logGroupName       :: Text,
    logStreamName      :: Text,
    -- The following context values come from headers rather than env vars.
    awsRequestId       :: Text,
    invokedFunctionArn :: Text,
    xRayTraceId        :: Text,
    deadline           :: UTCTime,
    clientContext      :: Maybe ClientContext,
    identity           :: Maybe CognitoIdentity
  } deriving (Show, Generic, Eq)
