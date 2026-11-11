#!/usr/bin/env python3
"""Install a pinned Linux Cabal executable into a new task-owned directory."""
import argparse
import hashlib
import lzma
import os
from pathlib import Path
import platform
import stat
import tarfile
import tempfile
from typing import NamedTuple
import urllib.parse
import urllib.request

VERSION = "3.18.2.0"
RELEASE_URL = f"https://github.com/haskell/cabal/releases/download/cabal-install-v{VERSION}"
MAX_ARCHIVE = 16 * 1024 * 1024
MAX_TAR = 96 * 1024 * 1024
MAX_BINARY = 96 * 1024 * 1024
MAX_PLAN = 1024 * 1024
MAX_LZMA_MEMORY = 64 * 1024 * 1024


class Release(NamedTuple):
    archive: str
    archive_sha256: str
    binary_sha256: str


# Archive pins agree with the full upstream SHA256SUMS and release API digests.
# Executable pins were computed from the verified archives, without executing them.
# https://github.com/haskell/cabal/releases/tag/cabal-install-v3.18.2.0
RELEASES = {
    "aarch64": Release(
        "cabal-install-3.18.2.0-aarch64-linux-unknown.tar.xz",
        "e4d940131ab0b78943a4b5ce64a9ef5acc452ea849df197787eaeecb1c3bf276",
        "5b2eebf0cf0c3f0b5f38cd3b1b6dedf81fa064671d09002b319cec0983392e74",
    ),
    "x86_64": Release(
        "cabal-install-3.18.2.0-x86_64-linux-unknown.tar.xz",
        "b4f4c83135da77cfc18ccd6056398ca714a2dad86d5f635621ffdda8f9afe587",
        "9686ccc04aab211fcd02e09470cdf1806e1e1f31918ecfb3aca96698964c80a0",
    ),
}


def release_for_host():
    if platform.system() != "Linux" or platform.machine() not in RELEASES:
        raise ValueError("this Cabal installer supports Linux x86_64 and aarch64 only")
    return RELEASES[platform.machine()]


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        url = urllib.parse.urlsplit(newurl)
        if url.scheme != "https" or url.username or url.password:
            raise ValueError("Cabal download redirected away from unauthenticated HTTPS")
        return super().redirect_request(request, fp, code, message, headers, newurl)


def download(url, limit):
    request = urllib.request.Request(url, headers={"User-Agent": "hal-cabal-installer"})
    opener = urllib.request.build_opener(HTTPSRedirect())
    with opener.open(request, timeout=60) as response:
        data = response.read(limit + 1)
    if not data or len(data) > limit:
        raise ValueError("missing or oversized Cabal download")
    return data


def verified_binary(archive, release):
    if not archive or len(archive) > MAX_ARCHIVE:
        raise ValueError("missing or oversized Cabal archive")
    if hashlib.sha256(archive).hexdigest() != release.archive_sha256:
        raise ValueError("Cabal archive checksum mismatch")
    # No decompression or tar parsing occurs before the immutable byte pin passes.
    compressed = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=MAX_LZMA_MEMORY)
    raw = compressed.decompress(archive, max_length=MAX_TAR + 1)
    if len(raw) > MAX_TAR or not compressed.eof or compressed.unused_data:
        raise ValueError("oversized, truncated, or trailing Cabal XZ data")
    if len(raw) % 512:
        raise ValueError("invalid Cabal tar length")
    offset = 0
    members = set()
    binary = None
    while offset + 512 <= len(raw):
        header = raw[offset:offset + 512]
        if header == bytes(512):
            if len(raw) - offset < 1024 or any(raw[offset:]):
                raise ValueError("invalid Cabal tar terminator")
            break
        member = tarfile.TarInfo.frombuf(header, "utf-8", "strict")
        # Read only the observed plain tar layout. Reject links, devices, sparse
        # entries, PAX overrides and GNU extension records before reading payloads.
        if member.type != tarfile.REGTYPE or member.linkname or member.sparse is not None:
            raise ValueError("Cabal tar member is not a plain regular file")
        if member.name not in {"cabal", "plan.json"} or member.name in members:
            raise ValueError("unexpected or duplicate Cabal tar member")
        limit = MAX_BINARY if member.name == "cabal" else MAX_PLAN
        if not 0 < member.size <= limit:
            raise ValueError("empty or oversized Cabal tar member")
        start = offset + 512
        end = start + member.size
        offset = start + ((member.size + 511) // 512) * 512
        if offset > len(raw):
            raise ValueError("truncated Cabal tar member")
        if member.name == "cabal":
            binary = raw[start:end]
        members.add(member.name)
    else:
        raise ValueError("missing Cabal tar terminator")
    if members != {"cabal", "plan.json"}:
        raise ValueError("Cabal archive must contain cabal and plan.json")
    if hashlib.sha256(binary).hexdigest() != release.binary_sha256:
        raise ValueError("Cabal executable checksum mismatch")
    return binary


def new_destination(destination):
    destination = Path(destination)
    if not destination.is_absolute() or any(c in str(destination) for c in "\r\n"):
        raise ValueError("Cabal destination must be an absolute single-line path")
    parent = destination.parent.resolve(strict=True)
    info = parent.stat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o022):
        raise ValueError("Cabal destination parent must be a task-owned directory")
    destination = parent / destination.name
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Cabal destination must be a new directory")
    return destination


def install(destination, fetch=download):
    release = release_for_host()
    destination = new_destination(destination)
    archive = fetch(f"{RELEASE_URL}/{release.archive}", MAX_ARCHIVE)
    binary = verified_binary(archive, release)
    # Create the exclusive destination only after both byte pins pass. Never
    # replace an existing tool directory or follow an existing destination link.
    destination.mkdir(mode=0o700)
    staged_path = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination, delete=False) as staged:
            staged_path = Path(staged.name)
            staged.write(binary)
            staged.flush()
            os.fchmod(staged.fileno(), 0o755)
            os.fsync(staged.fileno())
        os.replace(staged_path, destination / "cabal")
    except BaseException:
        if staged_path is not None:
            staged_path.unlink(missing_ok=True)
        destination.rmdir()
        raise
    return destination / "cabal"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", required=True, help="new directory in a task-owned parent")
    args = parser.parse_args()
    install(args.destination)


if __name__ == "__main__":
    main()
