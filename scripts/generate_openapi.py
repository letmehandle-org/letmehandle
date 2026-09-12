#!/usr/bin/env python3
"""Write the backend's OpenAPI schema to disk.

The mobile app's types are generated from this, so that the two cannot drift: a backend change
that alters the wire format changes this file, the generated types change with it, and the app
stops compiling rather than failing at run time in somebody's hand.

`make verify` regenerates and fails on a difference, which is what makes that promise hold.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "apps" / "backend"
OUTPUT = ROOT / "packages" / "api-client" / "openapi.json"

sys.path.insert(0, str(BACKEND / "src"))


def main() -> int:
    # Imported after the path is set, and with settings that need no database: generating a
    # schema must not require a running deployment.
    from letmehandle.config.settings import Environment, Settings
    from letmehandle.main import create_app

    app = create_app(
        Settings(
            app_env=Environment.TEST,
            log_level="critical",
            auth_signing_key="a-key-used-only-to-build-the-schema-never-to-sign",  # noqa: S106
            # Required configuration, and irrelevant to the schema: which voices a deployment
            # offers changes the catalogue a client is sent, not the shape of it.
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
