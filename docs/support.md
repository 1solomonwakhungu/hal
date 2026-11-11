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

Use GHC 9.10.3 and the verified Linux Cabal 3.18.2.0 executable. The project pins the Hackage index to
`2026-11-09T00:00:00Z`; the freeze records the effective index timestamp
`2026-11-08T23:20:31Z`, package versions and flags. Keep both
files when reproducing the build. `package.yaml` is authoritative; regenerate
`hal.cabal` using hpack 0.37.0 and commit both together.

The Cabal workflow uses `.github/actions/install_cabal` to check the release
archive SHA-256 before decompression and the executable SHA-256 before use. Both
Linux aarch64 and x86-64 artifacts are pinned. The installer writes to a new
explicit directory; it does not replace host tools.

The reproducible qualification requires a full checkout, Python 3.9 or later,
GHC 9.10.3, and that verified executable first on `PATH`:

```sh
python3 .github/actions/install_cabal/install.py --destination /absolute/owned/new-bin
export PATH=/absolute/owned/new-bin:$PATH
python3 .github/cabal-lane/run.py prepare --work /absolute/owned/new-qualification
python3 .github/cabal-lane/run.py build --work /absolute/owned/new-qualification
```

The parent directories must already exist. The installer destination and
qualification directory must be new. Preparation fetches the secure Hackage
index and verifies the pinned source archives and package revisions. Builds use
an isolated home, Cabal configuration and initially empty package/setup store;
only `PATH` is inherited into subprocesses. Local qualification disables container
networking during builds and tests. Hosted qualification runs in a fresh Linux
runner with the same isolated subprocess environment.

`.github/cabal-lane/inputs.json` pins the complete resolved graphs, source hashes,
revision hashes, flags and dependency edges, including build tools and fixture
setup dependencies. The separate `.github/cabal-lane/hpack` project freezes
hpack 0.37.0's bootstrap graph. The run regenerates both `hal.cabal` and
`examples/examples.cabal` and fails if either differs from the committed file.
The example project is constructed from the root project and identical freeze;
all five executables must build. The HAL checkout and its source distribution
must each pass the 102-example suite. Logs, original plans, normalized inventories,
tool identities, manifest bytes, and execution-route receipts remain under the
qualification directory.

Cabal 3.18.2.0 rejects `--offline` for cached repository sources that still need
building. The local no-network build therefore uses Docker's network isolation
with preverified cached archives, without that client flag. No bounds overrides
or changed source hashes are accepted.

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
Cabal 3.18.2.0 for the Cabal lane. The separate prior Stack validation used
Stack 3.3.1; the release workflow pins Stack 3.7.1. The Cabal plan resolves Aeson 2.2.5.1 and
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

The Cabal workflow targets Ubuntu 24.04 x86-64; its hosted checks are a separate
qualification from the local Debian ARM64 results. Native macOS, Windows, other compiler versions, Nix, live
AWS, deployment image ABI compatibility, and publication are unvalidated. Build
success in the Debian test image does not establish compatibility with a Lambda
execution environment. No credentials or cloud account are needed for the tests.

## Cabal setup boundary and remaining advisories

[Cabal change 11733](https://github.com/haskell/cabal/pull/11733), included in
[3.18.2.0](https://github.com/haskell/cabal/tree/45f982c12ef78ea8ec5beb43cb8c71c8e8f237c1),
replaces duplicate-header deletion with a warning. The qualified Cabal lane uses
that client's embedded Cabal 3.18.2.0 for the pinned Simple and Configure package
builds, including `network-3.2.12.0` and the bootstrap's `unix-time-0.6.0`.
The input guard rejects changes to the resolved graphs and rejects Custom or
external setup routes in these production build plans. Changing a client,
compiler, platform, package revision, dependency, flag, build tool or setup route
requires new qualification and reviewed input pins.

The duplicate-header fixture separately compiles an explicit Cabal 3.18.2.0
setup library and requires its warning to occur while preserving both headers.
Three affected controls deliberately delete only disposable synthetic headers:
the bundled 3.12.1.0 library, new-client v1 configuration, and new-client Custom
setup constrained to 3.12.1.0. v1 commands, arbitrary Custom/Hooks packages,
setup-library overrides, and arbitrary downstream projects are outside this
Cabal lane's supported build procedure.

GHC 9.10.3 still bundles Cabal 3.12.1.0. hpack 0.37.0 requires `Cabal <3.13` and
resolves that version while using package syntax, parsing and rendering modules; its bootstrap
package builds use the new client's embedded setup library. The old library
remains in the raw bootstrap inventory. The reviewed hpack source imports Cabal
syntax/parsing modules; this observation does not qualify arbitrary programs
linked against the old library.

Stack and release preparation continue to use the compiler's affected Cabal
3.12.1.0 setup library. Their existing successful builds do not remedy that
exposure. They have not been changed by this Cabal-only work. Publication remains
disabled.

The pinned [HSEC-2026-0006 record](https://github.com/haskell/security-advisories/blob/9fa5787e33f5bb3d73f2a0db22b59fd10921d25f/advisories/published/2026/HSEC-2026-0006.md)
has an open-ended affected range, so even 3.18.2.0 still matches it. Raw matches
are retained without changing advisory metadata or suppressing findings.
[HSEC-2023-0007](https://github.com/haskell/security-advisories/blob/9fa5787e33f5bb3d73f2a0db22b59fd10921d25f/advisories/published/2023/HSEC-2023-0007.md)
also remains applicable to reachable `base` numeric paths, including arbitrary
user `FromJSON` implementations using `readFloat`. This Cabal lane introduces no
numeric cap or handler exemption. The qualification makes no Windows or macOS
claim and no claim that the toolchain is free of advisory matches.
