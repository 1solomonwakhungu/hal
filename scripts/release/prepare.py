#!/usr/bin/env python3
"""Prepare one sdist with the repository Stack plan. No Hackage upload support."""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

MAX_ARCHIVE = 32 * 1024 * 1024
MAX_TAR = 128 * 1024 * 1024
VERSION = r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){1,3}"
GATES = ["package-check", "archive-build", "archive-test"]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def version(text):
    matches = re.findall(r"^version:[ \t]*(\S+)[ \t]*$", text, re.MULTILINE | re.IGNORECASE)
    if len(matches) != 1 or not re.fullmatch(VERSION, matches[0]):
        raise ValueError("expected exactly one canonical package version")
    return matches[0]


def require_tag(tag, package_version):
    if tag != "v" + package_version or not re.fullmatch("v" + VERSION, tag):
        raise ValueError("tag must be exactly v followed by the package version")


def read_regular(path, limit):
    # Open once without following a symlink; the returned bytes are the upload input.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("expected a bounded regular file")
        data = handle.read(limit + 1)
    if not data or len(data) > limit:
        raise ValueError("missing or oversized file")
    return data


def unpack(data, destination, package_version):
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
        raw = compressed.read(MAX_TAR + 1)
    if len(raw) > MAX_TAR or len(raw) % 512 or raw[-1024:] != bytes(1024):
        raise ValueError("invalid or truncated sdist")
    root = "hal-" + package_version
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as package:
        members = package.getmembers()
        for member in members:
            path = PurePosixPath(member.name)
            if (path.is_absolute() or ".." in path.parts or not path.parts
                    or path.parts[0] != root or str(path) in seen
                    or not (member.isfile() or member.isdir())):
                raise ValueError("unsafe or duplicate sdist member")
            seen.add(str(path))
        for member in members:
            target = destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as handle:
                    shutil.copyfileobj(package.extractfile(member), handle)
    extracted = destination / root
    if version((extracted / "hal.cabal").read_text()) != package_version:
        raise ValueError("sdist package version differs from source")
    return extracted


def stack(arguments, cwd, capture=False):
    result = subprocess.run(["stack", "--stack-yaml", str(cwd / "stack.yaml"),
                             "--no-docker", "--no-nix", "--system-ghc",
                             "--no-install-ghc", "--jobs=2", *arguments],
                            cwd=cwd, check=True, text=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout


def require_local_package(extracted):
    selected = stack(["query", "locals", "hal", "path"], extracted, capture=True).strip()
    # Stack emits a YAML scalar, ordinarily a plain absolute path. Accept JSON
    # double quoting too; reject other serializations rather than guessing.
    if selected.startswith('"'):
        selected = json.loads(selected)
    if Path(selected).resolve() != extracted.resolve():
        raise ValueError("Stack plan does not select the unpacked hal package")


def prepare(source, output, tag):
    source, output = source.resolve(), output.absolute()
    package_version = version((source / "hal.cabal").read_text())
    require_tag(tag, package_version)
    if version((source / "package.yaml").read_text()) != package_version:
        raise ValueError("package.yaml and hal.cabal versions differ")
    # Output must be fresh: a failed run cannot leave an old successful receipt.
    output.mkdir(parents=True, exist_ok=False)
    plan = read_regular(source / "stack.yaml", 1024 * 1024)
    lock_path = source / "stack.yaml.lock"
    with tempfile.TemporaryDirectory(prefix="hal-release-", dir=output.parent) as directory:
        work = Path(directory)
        tar_dir = work / "sdist"
        tar_dir.mkdir()
        # Stack runs Cabal's package checks by default. Never use --ignore-check.
        # Explicit bounds mode prevents creation of a separate Cabal revision.
        stack(["sdist", ".", "--pvp-bounds=none", "--tar-dir", str(tar_dir)], source)
        lock = read_regular(lock_path, 1024 * 1024) if lock_path.exists() else None
        archives = list(tar_dir.iterdir())
        if len(archives) != 1 or archives[0].name != f"hal-{package_version}.tar.gz":
            raise ValueError("expected exactly one named sdist and no revision")
        data = read_regular(archives[0], MAX_ARCHIVE)
        extracted = unpack(data, work / "unpacked", package_version)
        # Use the checked-in dependency plan, never a release-specific resolver.
        (extracted / "stack.yaml").write_bytes(plan)
        if lock is not None:
            (extracted / "stack.yaml.lock").write_bytes(lock)
        require_local_package(extracted)
        stack(["build", "hal:test:hal-test", "--test", "--no-run-tests"], extracted)
        stack(["test", "hal:test:hal-test", "--rerun-tests"], extracted)
        if read_regular(archives[0], MAX_ARCHIVE) != data:
            raise ValueError("sdist replaced during validation")
        if read_regular(source / "stack.yaml", 1024 * 1024) != plan:
            raise ValueError("dependency plan changed during validation")
        if lock is not None and read_regular(lock_path, 1024 * 1024) != lock:
            raise ValueError("dependency lock changed during validation")
        # Store the very bytes unpacked for validation, with no second sdist run.
        artifact = output / archives[0].name
        artifact.write_bytes(data)
        (output / "stack.yaml").write_bytes(plan)
        if lock is not None:
            (output / "stack.yaml.lock").write_bytes(lock)
        receipt = {"schema": 1, "tag": tag, "version": package_version,
                   "artifact": artifact.name, "sha256": digest(data), "gates": GATES,
                   "stack_yaml_sha256": digest(plan),
                   "stack_lock_sha256": digest(lock) if lock is not None else None,
                   "publication": "disabled"}
        (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        artifact.chmod(0o444)
    return receipt


def verified_artifact(output, tag):
    receipt = json.loads(read_regular(output / "receipt.json", 16384))
    if (receipt.get("schema") != 1 or receipt.get("gates") != GATES
            or receipt.get("publication") != "disabled"):
        raise ValueError("missing release preparation gates")
    require_tag(tag, receipt["version"])
    if receipt["tag"] != tag or receipt["artifact"] != f"hal-{receipt['version']}.tar.gz":
        raise ValueError("receipt tag or filename mismatch")
    data = read_regular(output / receipt["artifact"], MAX_ARCHIVE)
    if digest(data) != receipt["sha256"]:
        raise ValueError("artifact changed after validation")
    return data


def exercise_upload(output, tag, sink):
    data = verified_artifact(output, tag)
    # Fixed local sink, no configurable command and no network uploader.
    subprocess.run([sys.executable, str(Path(__file__).with_name("synthetic_uploader.py")),
                    str(sink)], input=data, check=True)
    if read_regular(sink, MAX_ARCHIVE) != data:
        raise ValueError("synthetic uploader did not consume the verified bytes")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "verify", "exercise-upload"])
    parser.add_argument("--source", type=Path, default=Path.cwd())
    parser.add_argument("--tag", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sink", type=Path)
    args = parser.parse_args()
    if args.command == "prepare":
        print(json.dumps(prepare(args.source, args.output, args.tag), indent=2))
    elif args.command == "verify":
        print(digest(verified_artifact(args.output, args.tag)))
    elif args.sink is None:
        parser.error("exercise-upload requires --sink")
    else:
        exercise_upload(args.output, args.tag, args.sink)


if __name__ == "__main__":
    main()
