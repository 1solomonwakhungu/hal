#!/usr/bin/env python3
"""Credential-free test sink. Writes stdin once to a new file; never uses a network."""
from pathlib import Path
import sys


def main():
    data = sys.stdin.buffer.read(32 * 1024 * 1024 + 1)
    if not data or len(data) > 32 * 1024 * 1024:
        raise SystemExit("invalid synthetic upload size")
    with Path(sys.argv[1]).open("xb") as sink:
        sink.write(data)


if __name__ == "__main__":
    main()
