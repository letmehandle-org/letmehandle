"""No module outside bootstrap names the call transport it is running on.

The domain check proves the domain is clean. This one covers everything else — the HTTP layer,
the application layer, the entry point — because a transport name in `api/` or `application/`
is the same branch as one in the domain, only easier to write. A transport may name itself
inside its own adapter package; configuration may name the choices it reads; bootstrap names
them because choosing is its job. Nothing else may.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "src" / "letmehandle"

TRANSPORT_NAMES = ("twilio", "telnyx", "plivo", "vonage", "android")

# Where a transport's name belongs.
ALWAYS_ALLOWED = ("bootstrap.py", "config/settings.py")

# The notification port routes a push by device platform, which is describing a device rather
# than branching on a transport. The domain check records the same exemption with its reason.
EXEMPT = {("domain/ports/notification.py", "android")}


def sources() -> list[Path]:
    return sorted(SOURCE.rglob("*.py"))


def test_there_are_sources_to_check() -> None:
    assert len(sources()) > 50


@pytest.mark.parametrize("name", TRANSPORT_NAMES)
def test_a_transport_is_named_only_where_it_is_chosen_or_implemented(name: str) -> None:
    pattern = re.compile(rf"\b{name}\b", re.IGNORECASE)
    offenders = []
    for path in sources():
        relative = path.relative_to(SOURCE).as_posix()
        if (
            relative in ALWAYS_ALLOWED
            or relative.startswith(f"adapters/transport/{name}")
            or (relative, name) in EXEMPT
        ):
            continue
        offenders += [
            f"{relative}:{number}"
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if pattern.search(line)
        ]
    assert not offenders, (
        f"{name!r} is named outside bootstrap and its own adapter at {', '.join(offenders)}. "
        "Core code asks what the transport can do, never which one it is."
    )


def test_the_check_would_catch_a_violation() -> None:
    pattern = re.compile(r"\btwilio\b", re.IGNORECASE)
    assert pattern.search("if transport.name == 'Twilio':")
    assert not pattern.search("twilioesque")
