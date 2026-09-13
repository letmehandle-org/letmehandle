"""Reading the fields of a JSON protocol event, refusing a known one that lacks what it needs."""

from __future__ import annotations

import base64
import binascii
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

type Event = Mapping[str, Any]


class MalformedEventError(Exception):
    """A recognised event missing a field it cannot do without; unrecognised events are ignored."""

    def __init__(self, event_type: str, field: str) -> None:
        super().__init__(f"{event_type} arrived without a usable {field}")
        self.event_type = event_type
        self.field = field


def mapping(event: Event, field: str, event_type: str) -> Event:
    """The object at `field`."""
    value = event.get(field)
    if not isinstance(value, dict):
        raise MalformedEventError(event_type, field)
    return value


def text(event: Event, field: str, event_type: str, *, allow_empty: bool = False) -> str:
    """The string at `field`, empty only when `allow_empty`."""
    value = event.get(field)
    if not isinstance(value, str) or (not allow_empty and not value):
        raise MalformedEventError(event_type, field)
    return value


def integer(event: Event, field: str, event_type: str) -> int:
    """The integer at `field`."""
    value = as_integer(event.get(field))
    if value is None:
        raise MalformedEventError(event_type, field)
    return value


def as_integer(value: object) -> int | None:
    """`value` as an integer, never a boolean, or `None`."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def as_number(value: object) -> float | None:
    """`value` as a number, never a boolean, or `None`."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def base64_audio(event: Event, field: str, event_type: str, *, allow_empty: bool = False) -> bytes:
    """The audio encoded as base64 at `field`."""
    encoded = text(event, field, event_type, allow_empty=allow_empty)
    try:
        return base64.b64decode(encoded, validate=True)
    except binascii.Error as error:
        raise MalformedEventError(event_type, field) from error


def encode_audio(audio: bytes) -> str:
    """Audio as the base64 text every protocol carries it in."""
    return base64.b64encode(audio).decode("ascii")
