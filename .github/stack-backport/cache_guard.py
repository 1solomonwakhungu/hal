#!/usr/bin/env python3
"""Validate an exclusively owned setup cache before Stack can execute it.

Receipts must be made only after a cold, controlled preparation. This is an
integrity check, not protection against a concurrent writer or arbitrary code.
"""
import hashlib
import json
from pathlib import Path


def digest(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"expected regular artifact: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(root):
    root = Path(root)
    if root.is_symlink():
        raise ValueError("setup root is a symlink")
    result = {}
    if root.exists():
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"symlink in setup cache: {path}")
            if path.is_file():
                result[str(path.relative_to(root))] = digest(path)
    return result


def verify(root, receipt, identities):
    data = json.loads(Path(receipt).read_text())
    if data["identities"] != identities:
        raise ValueError("source, compiler or library identity changed")
    if data["setup_cache"] != snapshot(root):
        raise ValueError("setup cache changed; reject before invoking Stack")
