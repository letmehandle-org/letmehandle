"""No module outside bootstrap names the call transport it is running on.

The domain check proves the domain is clean. This one covers everything else — the HTTP layer,
the application layer, the entry point — because a transport name in `api/` or `application/`
is the same branch as one in the domain, only easier to write. A transport may name itself
inside its own adapter package; configuration may name the choices it reads; bootstrap names
them because choosing is its job. Nothing else may.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "src" / "letmehandle"

TRANSPORT_NAMES = ("twilio", "telnyx", "plivo", "vonage", "android", "android_native")

# Each transport's own package, the one place besides bootstrap and configuration it may be named.
# A name with no package here has no adapter yet, so it belongs nowhere else at all.
ADAPTERS = {
    "twilio": "adapters/transport/twilio/",
    "android": "adapters/transport/android_native/",
    "android_native": "adapters/transport/android_native/",
}

# Where a transport's name belongs.
ALWAYS_ALLOWED = ("bootstrap.py", "config/settings.py")

# The notification port routes a push by device platform, which is describing a device rather
# than branching on a transport. The domain check records the same exemption with its reason.
EXEMPT = {("domain/ports/notification.py", "android")}


def mentions(name: str, line: str) -> bool:
    """Whether a line names the transport, however it is joined to the words around it.

    Deliberately no word boundaries. Identifiers are where a name hides — a class, a builder, a
    constant all run it into other words — and a boundary on either side lets every one of them
    through.
    """
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
            or (name in ADAPTERS and relative.startswith(ADAPTERS[name]))
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
