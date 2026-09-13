"""The media stream's messages, as typed values in and JSON text out.

The provider writes every number in these messages as a string — sequence numbers, chunk
numbers, timestamps — and audio as base64 μ-law at eight thousand samples a second. That is
settled here, once, so nothing above this module parses a number out of a string or decodes a
payload.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from letmehandle.domain.models.audio import TELEPHONY_NARROWBAND

if TYPE_CHECKING:
    from collections.abc import Mapping

# What the provider says it sends, in its own words. Anything else cannot be converted here.
_PROVIDER_ENCODING: Final = "audio/x-mulaw"

# The track carrying what the call sounds like to the assistant's leg.
INBOUND_TRACK: Final = "inbound"


class MediaProtocolError(Exception):
    """A message that is not the media stream protocol.

    Adapter-internal. It names which part was wrong and never repeats the message, which may
    carry somebody's voice.
    """


@dataclass(frozen=True, slots=True)
class Connected:
    """The socket is open; nothing is known about the call yet."""


@dataclass(frozen=True, slots=True)
class StreamStarted:
    """The stream's first real message: which call, which leg, and in what format."""

    stream_sid: str
    call_sid: str
    tracks: tuple[str, ...]
    parameters: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class MediaReceived:
    """A piece of the call's audio, as the provider's μ-law bytes."""

    track: str
    chunk: int
    timestamp_ms: int
    payload: bytes


@dataclass(frozen=True, slots=True)
class MarkReached:
    """Audio sent before a named mark has finished playing, or was cleared."""

    name: str


@dataclass(frozen=True, slots=True)
class DigitPressed:
    """A key pressed on the keypad of a phone on the call."""

    digit: str


@dataclass(frozen=True, slots=True)
class StreamStopped:
    """The stream has ended. The socket may or may not close after it."""


@dataclass(frozen=True, slots=True)
class UnknownMessage:
    """An event this code does not know. Tolerated: the provider adds them."""

    event: str


type MediaMessage = (
    Connected
    | StreamStarted
    | MediaReceived
    | MarkReached
    | DigitPressed
    | StreamStopped
    | UnknownMessage
)


def parse_message(text: str) -> MediaMessage:
    """One message from one websocket text frame."""
    try:
        message = json.loads(text)
    except json.JSONDecodeError:
        raise MediaProtocolError("a frame is not JSON") from None
    if not isinstance(message, dict):
        raise MediaProtocolError("a frame is not a JSON object")
    event = message.get("event")
    match event:
        case "connected":
            return Connected()
        case "start":
            return _started(_section(message, "start"), message)
        case "media":
            return _media(_section(message, "media"))
        case "mark":
            return MarkReached(_text(_section(message, "mark"), "name"))
        case "dtmf":
            return DigitPressed(_text(_section(message, "dtmf"), "digit"))
        case "stop":
            return StreamStopped()
        case str():
            return UnknownMessage(event)
        case _:
            raise MediaProtocolError("a frame names no event")


def media_message(stream_sid: str, audio: bytes) -> str:
    """Audio for the call, already μ-law at the provider's rate."""
    return _encode(
        {
            "event": "media",
            "streamSid": stream_sid,
            "media": {"payload": base64.b64encode(audio).decode("ascii")},
        }
    )


def clear_message(stream_sid: str) -> str:
    """Drop everything sent to the call and not yet played."""
    return _encode({"event": "clear", "streamSid": stream_sid})


def mark_message(stream_sid: str, name: str) -> str:
    """Ask to be told when everything sent so far has played."""
    return _encode({"event": "mark", "streamSid": stream_sid, "mark": {"name": name}})


def _started(start: Mapping[str, Any], message: Mapping[str, Any]) -> StreamStarted:
    media_format = start.get("mediaFormat")
    if not isinstance(media_format, dict):
        raise MediaProtocolError("the stream declares no audio format")
    encoding = media_format.get("encoding")
    rate = _integer(media_format.get("sampleRate"), "sampleRate")
    channels = _integer(media_format.get("channels"), "channels")
    if (encoding, rate, channels) != (
        _PROVIDER_ENCODING,
        TELEPHONY_NARROWBAND.sample_rate_hz,
        TELEPHONY_NARROWBAND.channels,
    ):
        raise MediaProtocolError("the stream's audio format is not mono μ-law at 8 kHz")
    tracks = start.get("tracks", [])
    parameters = start.get("customParameters", {})
    if not isinstance(tracks, list) or not all(isinstance(each, str) for each in tracks):
        raise MediaProtocolError("the stream's tracks are not a list of names")
    if not isinstance(parameters, dict) or not all(
        isinstance(value, str) for value in parameters.values()
    ):
        raise MediaProtocolError("the stream's parameters are not text")
    stream_sid = start.get("streamSid", message.get("streamSid"))
    if not isinstance(stream_sid, str) or not stream_sid:
        raise MediaProtocolError("the stream has no identifier")
    return StreamStarted(
        stream_sid=stream_sid,
        call_sid=_text(start, "callSid"),
        tracks=tuple(tracks),
        parameters=dict(parameters),
    )


def _media(media: Mapping[str, Any]) -> MediaReceived:
    try:
        payload = base64.b64decode(_text(media, "payload"), validate=True)
    except binascii.Error:
        raise MediaProtocolError("a media payload is not base64") from None
    return MediaReceived(
        track=_text(media, "track"),
        chunk=_integer(media.get("chunk"), "chunk"),
        timestamp_ms=_integer(media.get("timestamp"), "timestamp"),
        payload=payload,
    )


def _section(message: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    section = message.get(name)
    if not isinstance(section, dict):
        raise MediaProtocolError(f"a {name} message has no {name} section")
    return section


def _text(section: Mapping[str, Any], name: str) -> str:
    value = section.get(name)
    if not isinstance(value, str):
        raise MediaProtocolError(f"{name} is missing or is not text")
    return value


def _integer(value: object, name: str) -> int:
    """A number the provider may write as a string, and is documented to."""
    if isinstance(value, bool):
        raise MediaProtocolError(f"{name} is not a number")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise MediaProtocolError(f"{name} is not a number")


def _encode(message: Mapping[str, Any]) -> str:
    return json.dumps(message, separators=(",", ":"))
