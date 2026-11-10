{-# LANGUAGE QuasiQuotes #-}

module AWS.Lambda.Context.Spec where

import           AWS.Lambda.Context   (ClientApplication (..),
                                       ClientContext (..),
                                       CognitoIdentity (..))
import           Data.Aeson           (Value, eitherDecode, encode, toJSON)
import           Data.ByteString.Lazy (ByteString)
import           Data.Either          (isLeft)
import qualified Data.Map             as M
import           Test.Hspec           (Expectation, Spec, describe, shouldBe,
                                       shouldContain, shouldSatisfy, specify)
import           Text.RawString.QQ    (r)

spec :: Spec
spec = do
  describe "ClientApplication" $ do
    specify "decodes the AWS field names" $
      decodeApplication [r|
        { "installation_id": "install"
        , "app_title": "title"
        , "app_version_name": "versionName"
        , "app_version_code": "versionCode"
        , "app_package_name": "packageName"
        }|] `shouldBe` Right application

    specify "decodes the legacy field names" $
      decodeApplication [r|
        { "appTitle": "title"
        , "appVersionName": "versionName"
        , "appVersionCode": "versionCode"
        , "appPackageName": "packageName"
        }|] `shouldBe` Right application

    specify "decodes a mix of AWS and legacy field names" $
      decodeApplication [r|
        { "app_title": "title"
        , "appVersionName": "versionName"
        , "app_version_code": "versionCode"
        , "appPackageName": "packageName"
        }|] `shouldBe` Right application

    specify "decodes both spellings when they agree" $
      decodeApplication [r|
        { "app_title": "title", "appTitle": "title"
        , "app_version_name": "versionName", "appVersionName": "versionName"
        , "app_version_code": "versionCode", "appVersionCode": "versionCode"
        , "app_package_name": "packageName", "appPackageName": "packageName"
        }|] `shouldBe` Right application

    specify "prefers the AWS field names when both spellings disagree" $
      decodeApplication [r|
        { "app_title": "title", "appTitle": "other"
        , "app_version_name": "versionName", "appVersionName": "other"
        , "app_version_code": "versionCode", "appVersionCode": "other"
        , "app_package_name": "packageName", "appPackageName": "other"
        }|] `shouldBe` Right application

    specify "ignores unknown fields" $
      decodeApplication [r|
        { "app_title": "title"
        , "app_version_name": "versionName"
        , "app_version_code": "versionCode"
        , "app_package_name": "packageName"
        , "unknown": { "nested": [1, 2, 3] }
        }|] `shouldBe` Right application

    specify "does not fall back to the legacy name when the AWS value is null" $
      decodeApplication [r|
        { "app_title": null, "appTitle": "title"
        , "app_version_name": "versionName"
        , "app_version_code": "versionCode"
        , "app_package_name": "packageName"
        }|] `shouldSatisfy` isLeft

    specify "does not fall back to the legacy name when the AWS value has the wrong type" $
      decodeApplication [r|
        { "app_title": 1, "appTitle": "title"
        , "app_version_name": "versionName"
        , "app_version_code": "versionCode"
        , "app_package_name": "packageName"
        }|] `shouldSatisfy` isLeft

    specify "rejects a null legacy value" $
      decodeApplication [r|
        { "appTitle": null
        , "appVersionName": "versionName"
        , "appVersionCode": "versionCode"
        , "appPackageName": "packageName"
        }|] `shouldSatisfy` isLeft

    specify "rejects a legacy value with the wrong type" $
      decodeApplication [r|
        { "appTitle": "title"
        , "appVersionName": "versionName"
        , "appVersionCode": 42
        , "appPackageName": "packageName"
        }|] `shouldSatisfy` isLeft

    specify "requires every field" $
      decodeApplication [r|
        { "app_version_name": "versionName"
        , "app_version_code": "versionCode"
        , "app_package_name": "packageName"
        }|] `shouldFailWith` "app_title"

    specify "rejects values that are not objects" $
      decodeApplication [r|["title"]|] `shouldSatisfy` isLeft

    specify "encodes with the legacy field names" $
      toJSON application `shouldBeJson` [r|
        { "appTitle": "title"
        , "appVersionName": "versionName"
        , "appVersionCode": "versionCode"
        , "appPackageName": "packageName"
        }|]

    specify "round trips through its own encoding" $
      decodeApplication (encode application) `shouldBe` Right application

  describe "ClientContext" $ do
    specify "decodes the AWS field names" $
      decodeClientContext [r|
        { "client":
            { "installation_id": "install"
            , "app_title": "title"
            , "app_version_name": "versionName"
            , "app_version_code": "versionCode"
            , "app_package_name": "packageName"
            }
        , "custom": { "key1": "value1" }
        , "env": { "key2": "value2" }
        }|] `shouldBe` Right clientContext

    specify "decodes the legacy field names" $
      decodeClientContext [r|
        { "client":
            { "appTitle": "title"
            , "appVersionName": "versionName"
            , "appVersionCode": "versionCode"
            , "appPackageName": "packageName"
            }
        , "custom": { "key1": "value1" }
        , "environment": { "key2": "value2" }
        }|] `shouldBe` Right clientContext

    specify "decodes both spellings when they agree" $
      decodeClientContext [r|
        { "client": { "app_title": "title", "appVersionName": "versionName"
                    , "app_version_code": "versionCode", "appPackageName": "packageName" }
        , "custom": { "key1": "value1" }
        , "env": { "key2": "value2" }
        , "environment": { "key2": "value2" }
        }|] `shouldBe` Right clientContext

    specify "prefers env when env and environment disagree" $
      decodeClientContext [r|
        { "client": { "app_title": "title", "app_version_name": "versionName"
                    , "app_version_code": "versionCode", "app_package_name": "packageName" }
        , "custom": { "key1": "value1" }
        , "env": { "key2": "value2" }
        , "environment": { "other": "other" }
        }|] `shouldBe` Right clientContext

    specify "ignores unknown fields" $
      decodeClientContext [r|
        { "client": { "app_title": "title", "app_version_name": "versionName"
                    , "app_version_code": "versionCode", "app_package_name": "packageName" }
        , "custom": { "key1": "value1" }
        , "env": { "key2": "value2" }
        , "unknown": true
        }|] `shouldBe` Right clientContext

    specify "does not fall back to environment when env is null" $
      decodeClientContext [r|
        { "client": { "app_title": "title", "app_version_name": "versionName"
                    , "app_version_code": "versionCode", "app_package_name": "packageName" }
        , "custom": { "key1": "value1" }
        , "env": null
        , "environment": { "key2": "value2" }
        }|] `shouldSatisfy` isLeft

    specify "does not fall back to environment when env has the wrong type" $
      decodeClientContext [r|
        { "client": { "app_title": "title", "app_version_name": "versionName"
                    , "app_version_code": "versionCode", "app_package_name": "packageName" }
        , "custom": { "key1": "value1" }
        , "env": "key2=value2"
        , "environment": { "key2": "value2" }
        }|] `shouldSatisfy` isLeft

    specify "requires env or environment" $
      decodeClientContext [r|
        { "client": { "app_title": "title", "app_version_name": "versionName"
                    , "app_version_code": "versionCode", "app_package_name": "packageName" }
        , "custom": { "key1": "value1" }
        }|] `shouldFailWith` "env"

    specify "requires custom" $
      decodeClientContext [r|
        { "client": { "app_title": "title", "app_version_name": "versionName"
                    , "app_version_code": "versionCode", "app_package_name": "packageName" }
        , "env": { "key2": "value2" }
        }|] `shouldFailWith` "custom"

    specify "requires client" $
      decodeClientContext [r|
        { "custom": { "key1": "value1" }
        , "env": { "key2": "value2" }
        }|] `shouldFailWith` "client"

    specify "rejects a null custom value" $
      decodeClientContext [r|
        { "client": { "app_title": "title", "app_version_name": "versionName"
                    , "app_version_code": "versionCode", "app_package_name": "packageName" }
        , "custom": null
        , "env": { "key2": "value2" }
        }|] `shouldSatisfy` isLeft

    specify "rejects an invalid client" $
      decodeClientContext [r|
        { "client": { "app_title": "title" }
        , "custom": { "key1": "value1" }
        , "env": { "key2": "value2" }
        }|] `shouldSatisfy` isLeft

    specify "encodes with the legacy field names" $
      toJSON clientContext `shouldBeJson` [r|
        { "client":
            { "appTitle": "title"
            , "appVersionName": "versionName"
            , "appVersionCode": "versionCode"
            , "appPackageName": "packageName"
            }
        , "custom": { "key1": "value1" }
        , "environment": { "key2": "value2" }
        }|]

    specify "round trips through its own encoding" $
      decodeClientContext (encode clientContext) `shouldBe` Right clientContext

  describe "CognitoIdentity" $ do
    specify "decodes the AWS field names" $
      decodeIdentity [r|
        { "cognitoIdentityId": "identityId"
        , "cognitoIdentityPoolId": "identityPoolId"
        }|] `shouldBe` Right identity

    specify "decodes the legacy field names" $
      decodeIdentity [r|
        { "identityId": "identityId"
        , "identityPoolId": "identityPoolId"
        }|] `shouldBe` Right identity

    specify "decodes a mix of AWS and legacy field names" $
      decodeIdentity [r|
        { "cognitoIdentityId": "identityId"
        , "identityPoolId": "identityPoolId"
        }|] `shouldBe` Right identity

    specify "decodes both spellings when they agree" $
      decodeIdentity [r|
        { "cognitoIdentityId": "identityId", "identityId": "identityId"
        , "cognitoIdentityPoolId": "identityPoolId", "identityPoolId": "identityPoolId"
        }|] `shouldBe` Right identity

    specify "prefers the AWS field names when both spellings disagree" $
      decodeIdentity [r|
        { "cognitoIdentityId": "identityId", "identityId": "other"
        , "cognitoIdentityPoolId": "identityPoolId", "identityPoolId": "other"
        }|] `shouldBe` Right identity

    specify "ignores unknown fields" $
      decodeIdentity [r|
        { "cognitoIdentityId": "identityId"
        , "cognitoIdentityPoolId": "identityPoolId"
        , "unknown": null
        }|] `shouldBe` Right identity

    specify "does not fall back to the legacy name when the AWS value is null" $
      decodeIdentity [r|
        { "cognitoIdentityId": null, "identityId": "identityId"
        , "cognitoIdentityPoolId": "identityPoolId"
        }|] `shouldSatisfy` isLeft

    specify "does not fall back to the legacy name when the AWS value has the wrong type" $
      decodeIdentity [r|
        { "cognitoIdentityId": "identityId"
        , "cognitoIdentityPoolId": ["identityPoolId"], "identityPoolId": "identityPoolId"
        }|] `shouldSatisfy` isLeft

    specify "rejects a null legacy value" $
      decodeIdentity [r|
        { "identityId": "identityId"
        , "identityPoolId": null
        }|] `shouldSatisfy` isLeft

    specify "requires every field" $
      decodeIdentity [r|
        { "cognitoIdentityId": "identityId"
        }|] `shouldFailWith` "cognitoIdentityPoolId"

    specify "rejects values that are not objects" $
      decodeIdentity [r|"identityId"|] `shouldSatisfy` isLeft

    specify "encodes with the legacy field names" $
      toJSON identity `shouldBeJson` [r|
        { "identityId": "identityId"
        , "identityPoolId": "identityPoolId"
        }|]

    specify "round trips through its own encoding" $
      decodeIdentity (encode identity) `shouldBe` Right identity

application :: ClientApplication
application =
  ClientApplication
    { appTitle = "title"
    , appVersionName = "versionName"
    , appVersionCode = "versionCode"
    , appPackageName = "packageName"
    }

clientContext :: ClientContext
clientContext =
  ClientContext
    { client = application
    , custom = M.singleton "key1" "value1"
    , environment = M.singleton "key2" "value2"
    }

identity :: CognitoIdentity
identity =
  CognitoIdentity
    { identityId = "identityId"
    , identityPoolId = "identityPoolId"
    }

decodeApplication :: ByteString -> Either String ClientApplication
decodeApplication = eitherDecode

decodeClientContext :: ByteString -> Either String ClientContext
decodeClientContext = eitherDecode

decodeIdentity :: ByteString -> Either String CognitoIdentity
decodeIdentity = eitherDecode

-- Compares against a literal JSON document, independent of key order.
shouldBeJson :: Value -> ByteString -> Expectation
shouldBeJson actual expected = Right actual `shouldBe` eitherDecode expected

-- Asserts that decoding failed with a message mentioning the given text.
shouldFailWith :: Show a => Either String a -> String -> Expectation
shouldFailWith result expected = do
  result `shouldSatisfy` isLeft
  either id (const "") result `shouldContain` expected
