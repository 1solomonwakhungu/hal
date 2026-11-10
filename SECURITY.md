# Security policy for this fork

This policy covers the source in `1solomonwakhungu/hal`. It does not change Nike's upstream support policy or make the upstream Hackage `hal-1.1` release safe. Consumers must obtain the fork changes and resolve their own application's full dependency plan.

## Build support and remaining findings

The retained compiler is GHC 9.10.3. Cabal 3.14.1.1 uses the committed project and freeze file; Stack uses the fixed LTS 24.66 configuration and lock files, including the examples. The library requires Aeson `>=2.2.5.1 && <2.3`, Scientific `>=0.3.10.0 && <0.4`, and base `>=4.20 && <4.21`. These floors exclude the listed affected Aeson and Scientific versions; they do not clear every dependency or toolchain advisory. The supported plans also select text-iso8601 0.1.1.2. An unfrozen downstream plan can still select affected text-iso8601 0.1.1.1 with Aeson 2.2.5.1; downstream applications must scan their resolved graphs.

Older GHC versions, LTS 13–23, rolling nightly and Nix support have been retired in this fork. Native macOS and Windows, live AWS deployment and deployment ABI compatibility have not been validated. Local Linux aarch64 evidence covers Cabal 3.14.1.1 and Stack 3.3.1, with 102 passing examples on each and five built example executables. CI targets Ubuntu 24.04 x86-64 and uses the release lane's checksum-verified Stack 3.7.1 installer. Real offline release preparation additionally passes all 102 examples with Stack 3.7.1 on Linux aarch64. That hosted x86-64 combination is not an observed local test result.

The complete pinned HSEC scan of the locally resolved plans still reports:

- `base-4.20.2.0`: HSEC-2023-0007, concerning `Numeric.readFloat` on large exponents. The pinned advisory has no fixed base version. A matching installed version does not establish that a handler calls that API with untrusted input.
- `Cabal-3.12.1.0`, bundled with GHC, and `Cabal-3.14.1.1`, used by cabal-install: HSEC-2026-0006, concerning source-header deletion during configuration. The pinned advisory has no fixed version and no machine-readable OS restriction. This is a build-time finding, not an invocation-path finding.

All three scan jobs fail conservatively on applicable version matches. There is no exception list. These remaining findings require a reviewed resolution before claiming a clean security gate. Aeson 2.2.5.1 and Scientific 0.3.10.0 do not match the numeric advisories at this database revision; older versions can expose denial of service depending on the decoded event type and whether a handler re-encodes attacker-derived numbers.

## Advisory coverage

The scanner uses Haskell Security Response Team's database at commit `9fa5787e33f5bb3d73f2a0db22b59fd10921d25f` (2026-11-04), with 39 published records. It verifies the pinned download and complete advisory tree before parsing every published record. Compiler-component records are checked against compiler metadata. Malformed, incomplete or unrecognized inputs fail the job.

The workflow resolves the shipped Cabal plan, Stack library/test inventory and Stack examples inventory on each run. Committed test fixtures are regression inputs only. The database is a fixed snapshot, not a live advisory feed, and supplies no coverage for later advisories until reviewed and updated. See [scanner scope, commands and update procedure](docs/security-scan.md). The scanner does not prove API reachability or cover every possible downstream consumer plan, operating-system package, native library, or tool executable's private build dependencies.

## Reporting and publication

A sensitive reporting channel for this fork has not been established or verified. Do not put secrets, credentials, personal data or an undisclosed exploit in a public issue. Public issues are suitable only for information already safe to publish. This policy offers no private-reporting guarantee or response SLA.

Publication is disabled in the fork's release workflow. Local release preparation and dependency scanning do not authorize a Hackage upload. Hackage account/token controls, MFA, repository branch/tag protection, release environment approvals and other external account controls remain **UNPERFORMED** by this remediation. This document does not claim those protections are configured. Upstream publication and account administration remain with their respective owners.
