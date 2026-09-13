"""Removes sensitive fields, numbers and credentials from every log line; outlines exceptions."""

from __future__ import annotations

import re
import sys
import traceback
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

    from structlog.typing import EventDict, WrappedLogger

REDACTED: Final = "[redacted]"

# Sensitive field names, matched without case or separators at any depth.
SENSITIVE_FIELDS: Final = frozenset(
    {
        "accesstoken",
        "apikey",
        "authorization",
        "caller",
        "callerlabel",
        "callername",
        "callernumber",
        "displayname",
        "established",
        "label",
        "message",
        "number",
        "password",
        "phone",
        "phonenumber",
        "prompt",
        "refreshtoken",
        "said",
        "secret",
        "signature",
        "summary",
        "text",
        "token",
        "transcript",
        "utterance",
    }
)

# Anything shaped like an international number; a masked number does not match.
_PHONE_NUMBER: Final = re.compile(r"\+[1-9][0-9]{6,14}\b")
# A signed token: three base64url segments, the first a JSON header.
_SIGNED_TOKEN: Final = re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+")
_BEARER: Final = re.compile(r"(?i)\bbearer\s+\S+")

# The deepest nesting followed, so a self-referencing value cannot hang logging.
_MAX_DEPTH: Final = 8


def scrub_text(text: str) -> str:
    """`text` with every number and credential shaped value replaced."""
    text = _PHONE_NUMBER.sub(REDACTED, text)
    text = _SIGNED_TOKEN.sub(REDACTED, text)
    return _BEARER.sub(f"Bearer {REDACTED}", text)


def _is_sensitive(name: object) -> bool:
    return isinstance(name, str) and re.sub(r"[\s_\-.]", "", name).lower() in SENSITIVE_FIELDS


def _scrub(value: object, depth: int) -> object:
    if isinstance(value, str):
        return scrub_text(value)
    if depth >= _MAX_DEPTH:
        return REDACTED
    if isinstance(value, dict):
        return _scrub_mapping(value, depth + 1)
    if isinstance(value, list | tuple | set | frozenset):
        return [_scrub(item, depth + 1) for item in value]
    if isinstance(value, bool | int | float) or value is None:
        return value
    # Other values are rendered here, so their text is scrubbed too.
    return scrub_text(str(value))


def _scrub_mapping(mapping: Mapping[Any, object], depth: int) -> dict[Any, object]:
    return {
        key: REDACTED if _is_sensitive(key) else _scrub(item, depth)
        for key, item in mapping.items()
    }


def exception_outline(exception: BaseException) -> dict[str, object]:
    """An exception as its type, the types behind it and where it was raised, never what it said."""
    causes: list[BaseException] = []
    current = exception.__cause__ or exception.__context__
    while current is not None and current not in causes:
        causes.append(current)
        current = current.__cause__ or current.__context__
    return {
        "type": type(exception).__name__,
        "causes": [type(cause).__name__ for cause in causes],
        "frames": [
            f"{frame.filename.rsplit('/', 1)[-1]}:{frame.lineno} {frame.name}"
            for frame in traceback.extract_tb(exception.__traceback__)
        ],
    }


def outline_exception(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    """Replace `exc_info` with the exception's outline. Stands where a traceback renderer would."""
    info = event_dict.pop("exc_info", None)
    if info is None or info is False:
        return event_dict
    exception: BaseException | None
    if isinstance(info, BaseException):
        exception = info
    elif isinstance(info, tuple):
        exception = info[1]
    else:
        exception = sys.exc_info()[1]
    if exception is not None:
        event_dict["exception"] = exception_outline(exception)
    return event_dict


def scrub_event(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    """Remove every sensitive field and value from one line, the last thing before it is written."""
    return _scrub_mapping(event_dict, 0)
