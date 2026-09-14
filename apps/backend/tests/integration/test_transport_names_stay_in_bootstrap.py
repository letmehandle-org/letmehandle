"""No module outside bootstrap, configuration and its own adapter names a call transport."""

from __future__ import annotations

from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "src" / "letmehandle"

TRANSPORT_NAMES = ("twilio", "telnyx", "plivo", "vonage", "android", "android_native")

# Each transport's own packages, including the sign-in code adapters that use its API client.
ADAPTERS = {
    "twilio": (
        "adapters/transport/twilio/",
        "adapters/otp/twilio_sms.py",
        "adapters/otp/twilio_verify.py",
    ),
    "android": ("adapters/transport/android_native/",),
    "android_native": ("adapters/transport/android_native/",),
}

# Where every transport name is allowed: bootstrap and the configuration it reads.
ALWAYS_ALLOWED = ("bootstrap.py", "config/settings.py", "config/telephony_lines.py")

# Push routing by device platform: the notification port, the push adapter and the device schema.
EXEMPT = {
    ("domain/ports/notification.py", "android"),
    ("adapters/notification/fcm/provider.py", "android"),
    ("api/schemas.py", "android"),
}


def mentions(name: str, line: str) -> bool:
    """Whether a line names the transport, without word boundaries, so identifiers are caught."""
    return name in line.lower()


def sources() -> list[Path]:
    return sorted(SOURCE.rglob("*.py"))


def test_there_are_sources_to_check() -> None:
    assert len(sources()) > 50


@pytest.mark.parametrize("name", TRANSPORT_NAMES)
def test_a_transport_is_named_only_where_it_is_chosen_or_implemented(name: str) -> None:
    offenders = []
    for path in sources():
        relative = path.relative_to(SOURCE).as_posix()
        if (
            relative in ALWAYS_ALLOWED
            or relative.startswith(ADAPTERS.get(name, ()))
            or (relative, name) in EXEMPT
        ):
            continue
        offenders += [
            f"{relative}:{number}"
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if mentions(name, line)
        ]
    assert not offenders, (
        f"{name!r} is named outside bootstrap and its own adapter at {', '.join(offenders)}. "
        "Core code asks what the transport can do, never which one it is."
    )


def test_the_check_would_catch_a_violation() -> None:
    assert mentions("twilio", "if transport.name == 'Twilio':")
    # A name joined to other words is still the name: a class, a builder, a constant.
    assert mentions("twilio", "class TwilioCallTransport(CallTransport):")
    assert mentions("twilio", "router = build_twilio_router(transport)")
    assert mentions("twilio", "if kind is TWILIO_STREAMING:")
    assert mentions("android_native", "TelephonyProviderName.android_native")
    assert not mentions("twilio", "if transport.capabilities.can_bridge_human:")
