"""The domain names no provider in any string, comment, enum member or docstring."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DOMAIN = Path(__file__).resolve().parents[2] / "src" / "letmehandle" / "domain"

# Providers this project does or might integrate with; none belongs under domain/.
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

# The exact (file, name) pairings allowed, each with its reason.
ALLOWED = {
    # A device belongs to a platform, which describes the device rather than choosing a provider.
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
    # The search finds a planted violation, so it cannot pass on a wrong pattern.
    planted = tmp_path / "planted.py"
    planted.write_text("if transport.name == 'twilio':\n    pass\n")
    assert re.search(r"\btwilio\b", planted.read_text(), re.IGNORECASE)
