module DependencySpec (spec) where

import Control.Exception (evaluate)
import Data.Aeson (eitherDecode, encode)
import qualified Data.ByteString.Lazy.Char8 as LBS
import Data.Either (isLeft)
import Data.Fixed (Pico)
import Data.Scientific (Scientific, scientific)
import Data.Time.Clock (DiffTime, NominalDiffTime)
import System.Timeout (timeout)
import Test.Hspec

-- Bounded regression inputs; these are not resource-exhaustion benchmarks.
spec :: Spec
spec = around_ bounded $ do
  describe "Aeson bounded numeric conversions (HSEC-2026-0007)" $ do
    mapM_ rejectExponent ["1e-1025", "1e1025"]
    it "preserves ordinary fixed-point and time conversions" $ do
      (eitherDecode "1.25" :: Either String Pico) `shouldBe` Right 1.25
      (eitherDecode "1.25" :: Either String NominalDiffTime) `shouldBe` Right 1.25
      (eitherDecode "1.25" :: Either String DiffTime) `shouldBe` Right 1.25
  describe "Scientific comparisons (HSEC-2026-0012)" $ do
    it "compares extreme exponents without overflowing Int arithmetic" $ do
      -- Do not print or add these values: only Eq/Ord is under test.
      let hi = scientific 10 maxBound
          lo = scientific 1 minBound
      evaluate (hi == lo) `shouldReturn` False
      evaluate (compare hi lo) `shouldReturn` GT
      evaluate (compare lo hi) `shouldReturn` LT
      evaluate (compare (negate hi) (negate lo)) `shouldReturn` LT
  describe "Scientific encoding (HSEC-2026-0011)" $ do
    mapM_ coefficientRoundTrip [4096, 16384]
  where
    rejectExponent input = do
      it ("rejects " ++ LBS.unpack input ++ " as Pico") $
        (eitherDecode input :: Either String Pico) `shouldSatisfy` isLeft
      it ("rejects " ++ LBS.unpack input ++ " as NominalDiffTime") $
        (eitherDecode input :: Either String NominalDiffTime) `shouldSatisfy` isLeft
      it ("rejects " ++ LBS.unpack input ++ " as DiffTime") $
        (eitherDecode input :: Either String DiffTime) `shouldSatisfy` isLeft
    coefficientRoundTrip digits =
      it ("round-trips a " ++ show digits ++ "-digit coefficient") $ do
        let bytes = LBS.replicate digits '7'
        case eitherDecode bytes :: Either String Scientific of
          Left err -> expectationFailure err
          Right value -> do
            let encoded = encode value
            evaluate (LBS.length encoded) `shouldReturn` digits
            encoded `shouldBe` bytes
            eitherDecode encoded `shouldBe` Right value
    bounded action = do
      result <- timeout (10 * 1000000) action
      case result of
        Nothing -> expectationFailure "bounded dependency regression timed out"
        Just () -> pure ()
