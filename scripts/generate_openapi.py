#!/usr/bin/env python3
# ruff: noqa: T201 - a terminal tool whose output is the point
"""Write the backend's OpenAPI schema, from which the mobile app's types are generated."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "apps" / "backend"
OUTPUT = ROOT / "packages" / "api-client" / "openapi.json"

sys.path.insert(0, str(BACKEND / "src"))


def main() -> int:
    # Imported after the path is set; the settings need no database.
    from letmehandle.config.settings import Environment, Settings
    from letmehandle.main import create_app

    app = create_app(
        Settings(
            app_env=Environment.TEST,
            log_level="critical",
            auth_signing_key="a-key-used-only-to-build-the-schema-never-to-sign",
            # A voice catalogue is required, and does not change the schema.
            speech_voices="schema-voice:A voice used only to build the schema:en",
            speech_default_voice="schema-voice",
        )
    )
    schema = app.openapi()

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
