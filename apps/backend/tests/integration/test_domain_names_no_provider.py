"""The domain must not know who is providing anything.

import-linter already proves the domain imports no vendor package. That is necessary and not
sufficient: a vendor can be named in a string, a comment, an enum member or a docstring, and a
branch written on one of those is exactly the coupling the import rule exists to prevent.

This is the rule most likely to be broken by convenience — one small `if` for one small special
case — and the one that costs the most to undo, because by then several of them exist.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DOMAIN = Path(__file__).resolve().parents[2] / "src" / "letmehandle" / "domain"

# Concrete providers this project does or might integrate with. None of these belongs anywhere
# under domain/: a provider is chosen in bootstrap and reached through a port.
PROVIDER_NAMES = (
    "twilio",
    "telnyx",
    "plivo",
    "vonage",
    "nexmo",
    "bedrock",
    "nova",
    "openai",
    "anthropic",
    "elevenlabs",
    "cartesia",
    "deepgram",
    "cognito",
    "firebase",
    "fcm",
    "apns",
    "asterisk",
    "freeswitch",
    "telecom",
)

# Words that would mean the domain is reasoning about a platform rather than a capability.
PLATFORM_NAMES = ("android", "ios", "iphone", "callkit", "callscreeningservice")

# Two deliberate exceptions, each stated with its reason rather than quietly excluded. A new
# violation still fails; only these exact pairings are allowed, so widening the exemption is a
# visible edit to this list.
ALLOWED = {
    # The wire format is called this. Naming it is describing a protocol that many services
    # implement, which is the opposite of depending on one of them: it is what lets a
    # self-hoster point the product at their own server without a code change.
    ("ports/llm.py", "openai"),
    # A device genuinely belongs to a platform, and a push has to be routed to the service
    # that can reach it. That is not the domain branching on who is providing something; it
    # is the domain describing what a device is. The notification port's own capability model
    # decides what can be sent, not this.
    ("ports/notification.py", "ios"),
    ("ports/notification.py", "android"),
    ("ports/notification.py", "apns"),
    ("ports/notification.py", "fcm"),
}


def domain_sources() -> list[Path]:
    return sorted(path for path in DOMAIN.rglob("*.py"))


def test_there_are_domain_sources_to_check() -> None:
    # A guard against this whole file passing because the glob stopped matching.
    assert len(domain_sources()) > 10


@pytest.mark.parametrize("forbidden", PROVIDER_NAMES + PLATFORM_NAMES)
def test_no_domain_module_names_a_provider_or_a_platform(forbidden: str) -> None:
    pattern = re.compile(rf"\b{forbidden}\b", re.IGNORECASE)
    offenders = [
        f"{path.relative_to(DOMAIN)}:{number}"
        for path in domain_sources()
        if (str(path.relative_to(DOMAIN)), forbidden) not in ALLOWED
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert not offenders, (
        f"{forbidden!r} appears in the domain layer at {', '.join(offenders)}. "
        f"The domain asks what capabilities are available, never who is providing them. "
        f"If something genuinely differs between providers, it is a capability."
    )


def test_the_check_would_catch_a_violation(tmp_path: Path) -> None:
    # A test that has never been seen to fail is a test nobody has checked. This proves the
    # search finds what it is looking for rather than passing because the pattern is wrong.
    planted = tmp_path / "planted.py"
    planted.write_text("if transport.name == 'twilio':\n    pass\n")
    assert re.search(r"\btwilio\b", planted.read_text(), re.IGNORECASE)
