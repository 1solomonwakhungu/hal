# Stack setup library backport candidate

This candidate keeps GHC 9.10.3 and Stack 3.7.1 and builds Cabal 3.12.1.0 from GHC's pinned Cabal revision `260ecdc3d848782d4df49e629cb0a5dc9e96ca9e`. It adapts the deletion-removal, warning and comment change from [Cabal PR 11733](https://github.com/haskell/cabal/pull/11733), patch commit `98668b285ed221bb385f5205b9ffd922acf3642d`. The older FilePath APIs, `baseDir lbi`, and legal notices remain. Archive, preimage, postimage and patch hashes are in `.github/stack-backport/provenance.json`.

Preparation resolves the installed compiler from GHC's reported library directory and checks the installation's own launcher, so a ghcup shim does not become the copied prefix. Preparation uses the separately verified Cabal 3.18.2 client and its in-library Simple setup. It copies the compiler prefix, replaces only the private Cabal registration, and rejects a baseline with registered consumers of the old unit. Original compiler files and its global database remain untouched. The new unit is `Cabal-3.12.1.0-inplace`; its registration's `abi: inplace` is a placeholder, not a computed ABI certificate. Artifact hashes, module interface hashes and setup link maps supply the relevant identities.

The launcher selects this prefix, hashes its prepared artifacts and checks compiler symlinks before each invocation, and uses a Stack root derived from the preparation receipt and verified Stack binary. It rejects changed or unreceipted setup caches before execution. The qualified source export uses real directories: directory symlinks, including nested projects and cache ancestors, are rejected. Logging overrides are also rejected, and every new or changed setup-cache entry must have an observed executable hash before enrollment. Setup compilation preserves stdout/stderr and records the real command, linker map and finished executable. The compiler wrapper checks the selected Cabal archive against the preparation receipt before returning a successful setup compilation to Stack. The launcher binds successful setup invocations to retained executable hashes before authorizing new cache entries. Failed commands do not authorize new cache entries. This assumes exclusive ownership of the prepared directory; it is not a sandbox against a concurrent writer.

The CI adapter performs an affected deletion control on disposable headers, patched cold and warm warning/preservation tests, same-version cache injection/rejection, and a fresh setup rebuild before adding the launcher to PATH. Rejected caches are retained inside the owned test output. It never resets a user's Stack root.

For manual offline preparation, provide the verified source archive and client:

```sh
python3 .github/stack-backport/prepare.py \
  --work /absolute/new-owned-directory \
  --compiler-prefix /absolute/ghc-9.10.3-prefix \
  --archive /absolute/cabal-git.tar.gz \
  --client /absolute/verified-cabal-3.18.2.0
python3 test/stack_backport/probe.py \
  --work /absolute/new-owned-directory \
  --stack /absolute/verified-stack-3.7.1 \
  --original /absolute/ghc-9.10.3-prefix \
  --output /absolute/new-probe-directory
python3 .github/stack-backport/run.py \
  --work /absolute/new-owned-directory \
  --stack /absolute/verified-stack-3.7.1 -- test --jobs=2
```

The complete build and test boundary is the frozen HAL graph, its five examples, Network's Configure setup, and release preparation with the synthetic sink. Future dependency revisions, Custom setup dependencies, compiler layouts or command overrides need new qualification. Linux ARM64 was exercised locally; x86-64 has pinned tools and portable scripts but requires fork CI. No Windows or macOS compiler route is qualified.

The workflow uses `--dry-run --prefetch` to retrieve public package inputs before execution. In the pinned Stack source, prefetch runs before the dry-run branch in [Stack.Build](https://github.com/commercialhaskell/stack/blob/cfae313a06ea9f7278e0ac55a58341fa0440f1aa/src/Stack/Build.hs#L207-L214). Builds, tests and the synthetic release sink run in a separate Linux network namespace with loopback enabled for the mock Runtime API. Build subprocess environments omit account credentials and use the owned home and cache. CI retains preparation identities, original command logs, setup maps/executables, route receipts and the verified sdist. The `qualify.py` entry point fails if Network's real Configure execution is absent. Its invocation count includes the earlier header-control executions retained in the same prepared directory.

Stack's released binary remains unchanged. Its embedded hpack is 0.38.1; its source release graph uses GHC 9.8.4 and bounds Cabal below 3.12. That prebuilt graph is distinct from the external setups compiled by GHC 9.10.3, the fixed bootstrap client, and the separate Cabal lane's external hpack 0.37.0. This candidate does not rebuild or claim to remediate Stack's embedded Cabal, ghc-pkg's embedded code, or arbitrary external setups.

The raw open-ended HSEC Cabal match remains. No fixed advisory range or policy exception is introduced. The base/readFloat issue and arbitrary downstream handlers remain outside this mitigation. Release publication stays disabled.
