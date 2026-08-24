#!/usr/bin/env python3
"""Verify immutable project inputs without displaying sensitive contents."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fire_inspect.acceptance import AssetIntegrityError, verify_authoritative_assets


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--include-sensitive",
        action="store_true",
        help="also hash the locally held acceptance videos",
    )
    args = parser.parse_args()
    repository_root = Path(__file__).resolve().parents[1]
    try:
        verified = verify_authoritative_assets(
            repository_root, include_sensitive=args.include_sensitive
        )
    except AssetIntegrityError as exc:
        print(f"authoritative asset verification failed: {exc}", file=sys.stderr)
        return 1
    for relative_path in verified:
        print(f"ok  {relative_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
