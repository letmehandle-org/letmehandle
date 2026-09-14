#!/usr/bin/env python3
# ruff: noqa: T201 - a terminal tool whose output is the point
"""Writes a `.env` that runs the whole local stack with no paid account."""

from __future__ import annotations

import argparse
import base64
import os
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / ".env.example"
TARGET = ROOT / ".env"


def generated_values() -> dict[str, str]:
    """A fresh value for every variable the stack cannot start without and must not share."""
    transcript_key = base64.urlsafe_b64encode(os.urandom(32)).decode()
    return {
        "AUTH_SIGNING_KEY": secrets.token_urlsafe(48),
        "TRANSCRIPT_ENCRYPTION_KEYS": f"local:{transcript_key}",
    }


def fill(example: str, values: dict[str, str]) -> str:
    """The example with each named variable's empty assignment filled; fails when one is missing."""
    filled = example
    for name, value in values.items():
        pattern = re.compile(rf"^{re.escape(name)}=$", re.MULTILINE)
        filled, count = pattern.subn(f"{name}={value}", filled)
        if count != 1:
            raise SystemExit(f"{EXAMPLE.name} has no empty {name}= line to fill")
    return filled


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="replace an existing .env")
    arguments = parser.parse_args()

    if TARGET.exists() and not arguments.force:
        # An existing configuration is kept, with its own keys and providers.
        print(f"{TARGET.name} already exists; left as it is. Pass --force to replace it.")
        return 0
    TARGET.write_text(fill(EXAMPLE.read_text(), generated_values()))
    # Owner-only: the file now holds two keys.
    TARGET.chmod(0o600)
    print(f"wrote {TARGET.name}: mock sign-in, example voices, fresh keys, no paid provider")
    return 0


if __name__ == "__main__":
    sys.exit(main())
