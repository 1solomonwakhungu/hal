#!/usr/bin/env python3
"""Install only the reviewed Stack release, after upstream checksum verification."""
import gzip
import hashlib
import io
import os
from pathlib import Path
import platform
import re
import tarfile
import tempfile
import urllib.request

VERSION = "3.7.1"
ARCHIVE = f"stack-{VERSION}-linux-x86_64.tar.gz"
URL = f"https://github.com/commercialhaskell/stack/releases/download/v{VERSION}/{ARCHIVE}"
# Published by the Stack release, independently fetched and reviewed; see docs/release.md.
SHA256 = "b6df9168d471d917d955ee80553562ca2b0b3b1aa61cd1256199406c2d8c4eb4"
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_TAR = 256 * 1024 * 1024


def download(url, limit):
    request = urllib.request.Request(url, headers={"User-Agent": "hal-release-preparation"})
    with urllib.request.urlopen(request, timeout=60) as response:
        if not response.url.startswith("https://"):
            raise ValueError("download redirected away from HTTPS")
        data = response.read(limit + 1)
    if not data or len(data) > limit:
        raise ValueError("missing or oversized download")
    return data


def verified_binary(archive, checksum):
    # Require the upstream filename and pinned digest, not a self-computed checksum.
    pattern = rf"{SHA256} [ *]{re.escape(ARCHIVE)}\n?"
    if re.fullmatch(pattern, checksum.decode("ascii")) is None:
        raise ValueError("upstream checksum missing, malformed, or changed")
    if not archive or len(archive) > MAX_ARCHIVE:
        raise ValueError("missing or oversized archive")
    if hashlib.sha256(archive).hexdigest() != SHA256:
        raise ValueError("Stack archive checksum mismatch")
    with gzip.GzipFile(fileobj=io.BytesIO(archive)) as compressed:
        raw = compressed.read(MAX_TAR + 1)
    if len(raw) > MAX_TAR or len(raw) % 512 or raw[-1024:] != bytes(1024):
        raise ValueError("invalid or truncated Stack tar archive")
    target = f"stack-{VERSION}-linux-x86_64/stack"
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as package:
        matches = [m for m in package.getmembers() if m.name == target]
        if len(matches) != 1 or not matches[0].isfile():
            raise ValueError("Stack executable missing or not a regular file")
        binary = package.extractfile(matches[0]).read()
    if not binary:
        raise ValueError("empty Stack executable")
    return binary


def install(destination, fetch=download):
    checksum = fetch(URL + ".sha256", 4096)
    archive = fetch(URL, MAX_ARCHIVE)
    binary = verified_binary(archive, checksum)
    # No filesystem installation occurs until both checksum and archive pass.
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination, delete=False) as staged:
        staged_path = Path(staged.name)
        try:
            staged.write(binary)
            staged.flush()
            os.fchmod(staged.fileno(), 0o755)
            os.replace(staged_path, destination / "stack")
        finally:
            staged_path.unlink(missing_ok=True)


def main():
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise SystemExit("this Stack installer supports Linux x86_64 only")
    destination = Path.home() / ".local/bin"
    install(destination)
    with open(os.environ["GITHUB_PATH"], "a", encoding="utf-8") as path_file:
        path_file.write(str(destination) + "\n")


if __name__ == "__main__":
    main()
