# Release preparation

Hackage publication is disabled. Tag pushes and manual runs only prepare a checked
source distribution and retain it as a GitHub Actions artifact for seven days.
There is no Hackage uploader, publication job, publication switch, or secret
reference in this workflow. A tag cannot enable publication. Pull requests that
change the release workflow, installer or scripts run the hermetic guard tests;
full preparation runs only for a tag push or manual dispatch.

## Dependency plan and accepted tags

Preparation uses the repository's `stack.yaml` and its lock file, when present.
It does not select a second resolver or override dependency bounds. Integrate and
validate the supported dependency plan before preparing a release; this workflow
alone does not fix old Aeson or Scientific versions. The release scripts require
Python 3.11 or newer, GHC 9.10.3 and Stack 3.7.1 on Linux.
Preparation uses the installed compiler and disables implicit compiler installation. The installation action supports
Linux x86_64.

The only accepted tag is `v` followed by the identical version in `package.yaml`
and `hal.cabal`. Versions have two through four decimal components without leading
zeroes. For version `1.1`, only `v1.1` passes: `1.1`, `v1.1.0`, and prerelease
suffixes fail. Manual dispatch accepts a proposed tag for preparation; it does not
assert that a Git tag exists. The broad `v*` push filter is narrowed by this exact
script check. Input is passed as an environment variable, never interpolated into
shell code.

## One artifact through all gates

From a clean checkout with an isolated Stack home and no publication credentials:

```sh
python3 -m unittest discover -s test/release -v
python3 scripts/release/prepare.py prepare --tag v1.1 --output /tmp/hal-release-new
python3 scripts/release/prepare.py verify --tag v1.1 --output /tmp/hal-release-new
python3 scripts/release/prepare.py exercise-upload --tag v1.1 \
  --output /tmp/hal-release-new --sink /tmp/hal-synthetic-upload.tar.gz
```

Both the preparation output and synthetic sink must be new paths. The script:

1. Checks both manifest versions against the tag.
2. Runs `stack sdist` once with Cabal's package checks enabled and PVP revisions
   disabled. It requires exactly one correctly named archive.
3. Reads that archive into memory, rejects unsafe members, and unpacks those bytes
   in a fresh directory. It copies the repository Stack plan and lock there and
   checks that Stack resolves the local `hal` package to that directory.
4. Builds `hal:test:hal-test`, then explicitly runs that test suite. Any failing
   command aborts preparation. There is no receipt for a failed run.
5. Rejects archive replacement during validation, then retains the original
   bytes, their SHA-256, the completed gates, tag, version, and plan hashes.

The output includes the sdist, `receipt.json`, and copies of the Stack plan and
available lock file. `verify` rejects a changed archive, symlink, missing gate,
wrong tag, or unexpected artifact path. `exercise-upload` reads and verifies the
archive once and sends those in-memory bytes over stdin to the fixed local
`synthetic_uploader.py`. It never invokes a network uploader, runs a caller-supplied
command, or rebuilds a distribution. Replacing the pathname after that read cannot
change the bytes sent to the sink.

The receipt is local validation evidence, not a signature or an attestation.
An actor able to rewrite the scripts and receipt controls this trust boundary.
A byte-identical rebuild is indistinguishable from the retained file; any changed
bytes fail verification. The script does not claim reproducible builds or defend
against a compromised runner. Tests use synthetic packages and injected Stack
failures; they do not replace the Haskell suite on the supported dependency plan.

## Download and action provenance

The installer fetches both the versioned archive and the publisher's checksum
from the Stack GitHub release over HTTPS. It requires the published filename and
the reviewed digest below, then hashes the archive before opening it. Missing,
changed, malformed, oversized, or truncated input cannot reach installation.
It extracts only the expected regular executable and installs it atomically.
A digest computed from an untrusted download alone is not used as authentication.

- [Stack 3.7.1 release](https://github.com/commercialhaskell/stack/releases/tag/v3.7.1)
- [Linux x86_64 archive](https://github.com/commercialhaskell/stack/releases/download/v3.7.1/stack-3.7.1-linux-x86_64.tar.gz)
- [Published SHA-256](https://github.com/commercialhaskell/stack/releases/download/v3.7.1/stack-3.7.1-linux-x86_64.tar.gz.sha256)
- Archive SHA-256: `b6df9168d471d917d955ee80553562ca2b0b3b1aa61cd1256199406c2d8c4eb4`
- Stack annotated tag object: `26a6b92080d52bf48929fcb5c5a93df6da82ff9f`, pointing to
  [commit cfae313](https://github.com/commercialhaskell/stack/commit/cfae313a06ea9f7278e0ac55a58341fa0440f1aa).
- [checkout v4.2.2](https://github.com/actions/checkout/tree/11bd71901bbe5b1630ceea73d27597364c9af683)
  is pinned to `11bd71901bbe5b1630ceea73d27597364c9af683`.
- [haskell-actions/setup v2.12.1](https://github.com/haskell-actions/setup/tree/0f8e8c99d88aeb3fbfd523f1ef2c6f762d10d64d)
  is pinned to `0f8e8c99d88aeb3fbfd523f1ef2c6f762d10d64d` for GHC 9.10.3.
- [upload-artifact v4.6.2](https://github.com/actions/upload-artifact/tree/ea165f8d65b6e75b540449e92b4886f43607fa02)
  is pinned to `ea165f8d65b6e75b540449e92b4886f43607fa02`.

The release checksum and action tag-to-commit mappings were checked against
upstream on 2026-11-10. This trusts the upstream release account and HTTPS at
review time; no detached-signature or reproducible-build verification is claimed.
Updating Stack requires reviewing its new published checksum and changing both
version and digest in the installer. The workflow gives `GITHUB_TOKEN` only
`contents: read`, disables checkout credential persistence, and does not restore
shared build caches.

## Local validation

The 34 hermetic guard tests pass in network-disabled containers. A real
preparation run on Linux aarch64 with GHC 9.10.3, Stack 3.7.1 and LTS 24.66
created one sdist, checked it, and passed all 102 Haskell examples from its
unpacked contents. Verification and the synthetic uploader consumed the same
SHA-256-identified bytes. That run supplied no account credentials and had no
network access. The x86_64 installer archive was checked against the publisher's
checksum; a full x86_64 release preparation has not been observed locally.

## External controls, unperformed

No account, token, tag protection, environment, repository setting, or Hackage
publication was changed or exercised for this work. Actual Hackage token scopes,
package ownership, expiry and revocation capabilities remain unverified. Protected
tags and protected release environments are not established by this patch.

Future publication needs a separate reviewed change and explicit authorization.
It must verify the actual account's package-limited capabilities, configure tag
and environment protections with required review, and give publication credentials
only to a separate upload job. Build, test, package checks, and source-controlled
build hooks must never receive those credentials. That job must consume the exact
validated sdist bytes using an approved provenance boundary; running `stack upload .`
or regenerating the sdist would lose that guarantee. None of this is enabled here.
