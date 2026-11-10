# Pinned HSEC scan

Run with Python 3.11 or later. The scanner has no Python package dependencies. Its database pin is `scripts/security/hsec-pin.json`; reviewed plan graph shapes are in `scripts/security/plan-shapes.json`.

## Provenance and scope

The source is [haskell/security-advisories](https://github.com/haskell/security-advisories), pinned to [9fa5787e33f5bb3d73f2a0db22b59fd10921d25f](https://github.com/haskell/security-advisories/commit/9fa5787e33f5bb3d73f2a0db22b59fd10921d25f), committed November 4, 2026. The pin was checked against the upstream GitHub commit and nontruncated recursive tree APIs, and every advisory archive member was compared with that tree's Git blob ID and file mode. The pin records the upstream commit tree, full advisory tree, archive SHA-256 and each published record's SHA-256. This is provenance through the upstream HTTPS endpoint and content identity, not a claim of a signed release or independent publisher attestation.

The fetch command checks the complete archive checksum, exact advisory-tree membership, modes and Git blob hashes, then checks every published record's SHA-256. It never extracts archive paths or links. Only verified published bytes are written. The scan rechecks exact directory membership and per-file hashes. Missing, extra, changed or symlinked records fail. The database contains 39 published records, five empty reserved placeholders, alias symlinks and two `.gitkeep` files. Reserved placeholders contain no advisory data. Alias links index published records and are verified but not scanned twice. All these entries are covered by the tree verification. The published directory is authoritative: relying only on package aliases would miss records at this pin.

The schema and matching rules were checked against the pinned [README](https://github.com/haskell/security-advisories/blob/9fa5787e33f5bb3d73f2a0db22b59fd10921d25f/README.md), [example advisory](https://github.com/haskell/security-advisories/blob/9fa5787e33f5bb3d73f2a0db22b59fd10921d25f/EXAMPLE_ADVISORY.md) and [core matching implementation](https://github.com/haskell/security-advisories/blob/9fa5787e33f5bb3d73f2a0db22b59fd10921d25f/code/hsec-core/src/Security/Advisories/Core/Advisory.hs). Upstream `hsec-tools` offers advisory validation and queries. This bounded adapter additionally validates the two resolver formats and the fork's reviewed graph. It is not a general replacement for HSEC tooling.

Versions are numeric component lists, compared lexicographically as Cabal versions, without zero padding. Ranges include `introduced` and exclude `fixed`; an absent fix is open-ended. Multiple ranges form a union. Package and compiler-component records are both checked. All compiler suite components use the selected GHC version. The scan also includes cabal-install and its Cabal library from Cabal plan metadata, plus the GHC-bundled Cabal library reported by `ghc-pkg` for every plan. It does not reconstruct the internal dependency graph of the Stack/cabal-install executables or scan their native libraries.

OS and architecture tokens must belong to the pinned upstream [platform parser](https://github.com/haskell/security-advisories/blob/9fa5787e33f5bb3d73f2a0db22b59fd10921d25f/code/hsec-tools/src/Security/Advisories/Format.hs#L326) and [architecture vocabulary](https://github.com/haskell/security-advisories/blob/9fa5787e33f5bb3d73f2a0db22b59fd10921d25f/code/hsec-core/src/Security/Advisories/Core/Advisory.hs#L138); unknown tokens fail validation before matching. Constraints determine platform applicability; version matches on other platforms remain in the output as non-applicable. API and declaration constraints are validated and reported, but never used to suppress a package match. Prose-only conditions cannot be decided from an inventory. CVSS values are validated for the forms used at this pin, preserved upstream and never used as a severity threshold. Unknown fields, repository semantics, version syntax and unsupported schemas require a scanner update instead of being silently ignored.

## Input trust and completeness

Inputs must come from the resolver commands in `.github/workflows/security.yaml`, run against the current shipped project, freeze and lock files. They are not attestations that arbitrary uploaded JSON describes a real build. A forged inventory and a compromised resolver can lie about versions. Neither a graph nor a checksum establishes that those versions were installed. CI uses resolver output immediately after successful builds, in the same job and workspace; it never uses regression fixtures as its scan target.

Cabal's complete `install-plan` includes configured units, pre-existing compiler libraries, nested legacy component maps and executable/build-tool dependencies. The expected local components are `hal:lib` and `hal:test:hal-test`. Stack JSON contains packages and dependency edges, not per-component identities. Its test inventory must include the test dependencies; the examples inventory must contain both local roots, `hal` and `examples`. CI explicitly builds the test component and all five named example executables before obtaining those inventories. Component coverage for Stack relies on those successful commands; it cannot be inferred solely from `ls dependencies` JSON.

Every edge must resolve, IDs/names must be unique, versions must parse, required fields must exist, and local roots must agree. The scanner then compares the complete normalized graph with the reviewed support contract, including all 136 Cabal units, 126 Stack test packages and 101 Stack examples packages. Paths, platform-dependent unit hashes and versions are excluded from graph identity; versions are scanned separately. Removing even a leaf unit or an edge fails. The graph is deliberately strict: legitimate dependency, component or platform graph changes need review and regeneration. Unknown platform or compiler metadata fails. Linux aarch64 and x86-64 are accepted targets. The [hosted x86-64 run](https://github.com/1solomonwakhungu/hal/actions/runs/42916905085) built and validated all three inventories, then each scan failed on the advisory matches documented below. Fresh Stack 3.7.1 inventories on Linux aarch64 satisfy the same graph checks as the original Stack 3.3.1 fixtures.

## Commands and results

Resolve/build with the commands in the workflow. Then, for a local Linux aarch64 Cabal plan:

```sh
python3 scripts/security/scan.py fetch /tmp/hal-hsec
HSEC_DATABASE=/tmp/hal-hsec python3 -m unittest discover -s test/security -v
python3 scripts/security/scan.py scan --database /tmp/hal-hsec \
  --kind cabal --plan dist-newstyle/cache/plan.json \
  --compiler "$(ghc --numeric-version)" --os linux --arch aarch64 \
  --bundled-cabal "$(ghc-pkg field Cabal version --simple-output)"
```

Use a fresh destination for `fetch`. For offline verification, pass `--archive /path/to/pinned-archive.tar.gz` to `fetch`; the same checks apply. Use `--kind stack-test` with `stack ls dependencies json --test`, or `--kind stack-examples` with the examples configuration's inventory. Match `--arch` to the actual compiler platform (`x86_64` on the hosted runner). Never substitute an assumed compiler version when generating a scan.

Exit codes are 0 for no applicable matches, 1 for one or more applicable advisory matches, and 2 for validation failure. Download/resolver/build failures also fail CI. No `continue-on-error` or suppression list is used. The matrix disables fail-fast so one plan's findings do not cancel the other scans.

The saved aarch64 plans match HSEC-2023-0007 for base 4.20.2.0. Toolchain metadata additionally matches HSEC-2026-0006 for bundled Cabal 3.12.1.0, and Cabal's plan reports Cabal library 3.14.1.1 as another match. Each scan exits 1. These open-ended matches are retained even though reachability is conditional. No GHC-component advisory matches GHC 9.10.3 at this pin.

## Updating the snapshot or plans

There is no automatic freshness claim or scheduled database refresh. A rerun against the same pin cannot find advisories published afterward.

1. Select and review a new immutable commit in the official repository. Read its commit metadata and full recursive Git tree from the upstream API; reject a truncated tree response. Record the date, commit ID and root tree ID. Download the commit archive from the official codeload URL.
2. Independently compare every `advisories/` file or symlink with the upstream tree (Git blob hash and mode), enumerate all published records and inspect any reserved/alias/layout changes. Read the schema and core matcher changes. Do not regenerate hashes from an unverified local directory.
3. Update `REVISION` and `hsec-pin.json` together, including the archive SHA-256, every advisory-tree entry and published-record digest/count. Update schema handling and tests for new semantics; do not delete unsupported records to make the parser pass. GitHub archive compression changes may change the archive digest even at the same commit: reverify the full upstream tree before accepting replacement bytes.
4. Run the offline tests against the newly verified database. The existing suite checks every published record and every introduced/fixed boundary, compiler ranges/gaps, platform conditions, conditional APIs, affected Aeson/Scientific versions, malformed records, and removal of every plan unit and dependency edge. Review any changed expected findings.
5. Rebuild all three inventories from the current supported configurations, with tests enabled and examples built. If a graph legitimately changes, compare the complete before/after resolver output and regenerate `plan-shapes.json` using `cabal_plan(document)[1]` or `stack_plan(document, kind)[1]` from `scan.py`. Update the actual resolver regression fixtures and counts together. Never regenerate shapes automatically in CI: that would remove the completeness check.
6. Run all scans and inspect every match, including toolchain/compiler records. Update this document and `SECURITY.md` with observed support and remaining limits. Obtain independent review of the final combined source and observe the hosted runs before reporting a clean gate.

The workflow action pins were verified through their upstream commit endpoints: [checkout v4.2.2](https://github.com/actions/checkout/commit/11bd71901bbe5b1630ceea73d27597364c9af683) and [haskell-actions/setup v2.12.1](https://github.com/haskell-actions/setup/commit/0f8e8c99d88aeb3fbfd523f1ef2c6f762d10d64d). The workflow grants only `contents: read`, disables persisted checkout credentials, uses no publication secrets and performs no uploads.
