#!/usr/bin/env python3
"""Writes a `.env` that runs the whole local stack with no paid account."""

# `.env.example` documents every variable and carries no value that could be anybody's secret
# (D-021). Copied as it is, the backend refuses to start: signing a token needs a key, and a key
# written into a tracked file is a key every clone shares. This fills in the two keys with fresh
# random values and leaves everything else as the example has it — the mock sign-in provider, the
# example voices, and no speech service, model, call transport or push credentials.
#
# What that runs: the API, sign-in with the development code, preferences, voices and call
# history. What it does not: a phone call. Every provider a call needs is left unset, and each
# one's page under docs/providers/ says what to set.
#
#   python3 scripts/create_sample_env.py            write .env, refusing to replace one
#   python3 scripts/create_sample_env.py --force    replace it

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
    """The example with each named variable's empty assignment given its value.

    Only an assignment with nothing after the equals sign is filled, so a value somebody already
    put in the example is never overwritten, and a variable the example stopped listing fails
    loudly instead of being silently skipped.
    """
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
        print(f"{TARGET.name} already exists; left as it is. Pass --force to replace it.")
        return 1
    TARGET.write_text(fill(EXAMPLE.read_text(), generated_values()))
    # Owner-only: the file now holds two keys.
    TARGET.chmod(0o600)
    print(f"wrote {TARGET.name}: mock sign-in, example voices, fresh keys, no paid provider")
    return 0


if __name__ == "__main__":
    sys.exit(main())
