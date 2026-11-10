# Fork build support

This fork targets GHC 9.10.3. Its package bounds require `base >=4.20 && <4.21`,
`aeson >=2.2.5.1 && <2.3`, and `scientific >=0.3.10.0 && <0.4`.
The supported plans are the committed Cabal project/freeze and Stack LTS 24.66.
Do not use `allow-newer`, `allow-older`, or another bounds override to restore an
older dependency plan.

This retires the previous GHC/LTS matrix, rolling nightly, the old default and
example resolvers, and Nix channel configurations. It is a compatibility change
for this fork, not a statement about Nike upstream support. Nix support and its
workflow have been removed because no fixed Nix plan was validated.

## Dependency floors

The Haskell Security Response Team database at commit
`9fa5787e33f5bb3d73f2a0db22b59fd10921d25f` records:

- [HSEC-2023-0001](https://github.com/haskell/security-advisories/blob/9fa5787e33f5bb3d73f2a0db22b59fd10921d25f/advisories/published/2023/HSEC-2023-0001.md):
  Aeson hash flooding, fixed in 2.0.1.0.
- [HSEC-2026-0007](https://github.com/haskell/security-advisories/blob/9fa5787e33f5bb3d73f2a0db22b59fd10921d25f/advisories/published/2026/HSEC-2026-0007.md):
  Aeson fixed-point and time conversions with negative exponents, fixed in 2.2.5.1.
- [HSEC-2026-0011](https://github.com/haskell/security-advisories/blob/9fa5787e33f5bb3d73f2a0db22b59fd10921d25f/advisories/published/2026/HSEC-2026-0011.md)
  and [HSEC-2026-0012](https://github.com/haskell/security-advisories/blob/9fa5787e33f5bb3d73f2a0db22b59fd10921d25f/advisories/published/2026/HSEC-2026-0012.md):
  Scientific coefficient encoding and the subsequent extreme-exponent comparison
  regression. Upgrade directly to 0.3.10.0; 0.3.9.0 is not an acceptable floor.

A library's downstream consumers choose their complete dependency graph. These
bounds exclude the listed affected versions; they do not establish that every
possible downstream graph is free of vulnerabilities.

## Reproduce the builds

Use GHC 9.10.3 and Cabal 3.14.1.1. The project pins the Hackage index to
`2026-11-09T00:00:00Z`; the freeze records the effective index timestamp
`2026-11-08T23:20:31Z`, package versions and flags. Keep both
files when reproducing the build. `package.yaml` is authoritative; regenerate
`hal.cabal` using hpack 0.37.0 and commit both together.

```sh
cabal update
cabal install hpack-0.37.0 --ignore-project
hpack --force
cabal build all
cabal test all --test-show-details=direct --test-options="+RTS -N2 -RTS"
cabal check
cabal sdist
```

For Stack, use the fixed snapshot and its committed lock metadata:

```sh
stack --no-docker --system-ghc --no-install-ghc test --jobs 2 --test-arguments="+RTS -N2 -RTS"
stack --no-docker --system-ghc --no-install-ghc ls dependencies json --test > stack-dependencies.json
stack --no-docker --system-ghc --no-install-ghc --stack-yaml examples/stack.yaml build --jobs 2
```

`dist-newstyle/cache/plan.json` is Cabal's complete resolved build plan. Stack's
JSON dependency inventory includes transitive dependencies and test dependencies;
retain it with the snapshot lock and the tool versions. Re-resolve these artifacts
when changing dependencies, rather than assembling a direct-dependency list.

## Validation and limits

Local validation uses Linux aarch64 in Debian bookworm with GHC 9.10.3,
Cabal 3.14.1.1 and Stack 3.3.1. The Cabal plan resolves Aeson 2.2.5.1 and
Scientific 0.3.10.0 and passes all 102 examples: 92 existing examples and 10
bounded dependency regressions. The source distribution passes the same suite.
Stack LTS 24.66 also passes all 102 examples with these security-floor versions,
and all five example executables build. Its generated test inventory contains
126 packages; the example inventory contains 101.
`cabal check` exits successfully but retains existing missing-upper-bounds
warnings for unrelated dependencies.

The regression inputs use exponents +/-1025, coefficients of 4,096 and 16,384
digits, ordinary fixed-point/time conversions, and constant-size comparisons at
extreme exponents. They do not run destructive memory-exhaustion payloads or
prove asymptotic complexity. The existing runtime tests use a synthetic loopback
Runtime API. A test helper unwraps typed asynchronous exceptions before rendering
them so GHC 9.10 backtraces do not invalidate its stop assertion.

The build workflows target Ubuntu 24.04 x86-64 but have not been executed as part
of local validation. Native macOS, Windows, other compiler versions, Nix, live
AWS, deployment image ABI compatibility, and publication are unvalidated. Build
success in the Debian test image does not establish compatibility with a Lambda
execution environment. No credentials or cloud account are needed for the tests.
