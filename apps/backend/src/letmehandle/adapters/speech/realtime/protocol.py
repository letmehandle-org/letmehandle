"""The OpenAI Realtime-compatible protocol, as data.

No I/O. Outbound, domain intentions become protocol events; inbound, protocol events become the
signals the session acts on. Everything the session knows about the wire passes through here, so
a dialect difference is one edit in one file.

What the protocol is, as far as this adapter uses it. JSON events over one connection, each
with a `type`.

Sent:
- `session.update` configures the session. The current shape nests everything under
  `session`: `type` is `realtime` for speech to speech, `instructions` is the system context,
  and `audio.input` / `audio.output` each carry a `format` of `{type, rate}`. `audio/pcm` is
  16-bit little-endian mono at 24 kHz, the only rate it accepts; `audio/pcmu` and `audio/pcma`
  are G.711 at 8 kHz. Turn detection (`audio.input.turn_detection`, `server_vad`) and input
  transcription (`audio.input.transcription`) live beside the input format, and the voice at
  `audio.output.voice`. It can be sent again at any time to change anything except the voice.
- `input_audio_buffer.append` carries base64 audio in `audio`. Under server VAD the service
  decides where a turn ends; the client never commits.
- `response.cancel` stops the response in progress. Sent with no `response_id` it cancels
  whatever is in progress, and answers with an error when nothing is.
- `conversation.item.truncate` tells the service how much of an assistant item was heard:
  `item_id`, `content_index` and `audio_end_ms`. The service discards the unheard audio and the
  transcript that went with it, so the model does not believe it said what nobody heard.
- `conversation.item.create` inserts a `message` item with a `role` and `content`, which is how
  a reconnected session is told what was already said: `input_text` for the user's words and
  `output_text` for the assistant's.

Received:
- `input_audio_buffer.speech_started` / `speech_stopped`: server VAD heard the caller begin
  or stop. Barge-in is built on the first.
- `response.created` and `response.done` bracket a response, each carrying `response.id`;
  `response.done` also carries `response.status`, which is `cancelled` for one that was stopped.
- `response.output_audio.delta`: base64 audio in `delta`, with `response_id`, `item_id` and
  `content_index`.
- `response.output_audio_transcript.delta` / `.done`: the assistant's words, in `delta` and
  then the settled `transcript`.
- `conversation.item.input_audio_transcription.delta` / `.completed`: the caller's words, in
  `delta` and then `transcript`, when input transcription is configured.
- `error`: `error.type`, `error.code` and `error.message`. The connection stays open; a failure
  that ends it arrives as the connection closing.

Dialects. The earlier, beta form of the protocol named the output events
`response.audio.delta` and `response.audio_transcript.delta` / `.done`; servers built against it
still send those, so both spellings are accepted. Only the current shape is sent: the beta
`session.update` put formats and voice at the top of `session`, and a server that still wants
that is a server this adapter does not claim to speak to. Anything unrecognised is ignored,
because a compatible server is entitled to send events this adapter has no use for.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final

from letmehandle.domain.models.audio import AudioEncoding, AudioFormat

if TYPE_CHECKING:
    from collections.abc import Mapping

# The one linear format the protocol speaks. Everything else is converted to and from this.
WIRE_FORMAT: Final = AudioFormat(AudioEncoding.PCM_S16LE, 24_000)

# The error a cancel earns when the response had already finished. Expected, because a cancel is
# sent whenever there might be a response and the service is the only one who knows for certain.
NOTHING_TO_CANCEL: Final = "response_cancel_not_active"

type Event = Mapping[str, Any]


class Speaker(StrEnum):
    """Who said a settled turn."""

    CALLER = "caller"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class Turn:
    """Something said and settled, kept so that a reconnect can remind the model of it."""

    speaker: Speaker
    text: str


class MalformedEventError(Exception):
    """An event this adapter recognises, missing something it cannot do without.

    Adapter-internal. Distinct from an unrecognised event, which is ignored: a known event with a
    missing field means the server and this adapter disagree about the protocol, and that is worth
    counting rather than a `KeyError` from somewhere in the middle of a conversation.
    """

    def __init__(self, event_type: str, field: str) -> None:
        super().__init__(f"{event_type} arrived without a usable {field}")
        self.event_type = event_type
        self.field = field


# ---------------------------------------------------------------------------------- inbound


@dataclass(frozen=True, slots=True)
class CallerStartedSpeaking:
    """Server VAD heard the caller begin."""


@dataclass(frozen=True, slots=True)
class CallerStoppedSpeaking:
    """Server VAD heard the caller stop."""


@dataclass(frozen=True, slots=True)
class ResponseStarted:
    """The model began a response."""

    response_id: str


@dataclass(frozen=True, slots=True)
class ResponseFinished:
    """A response ended, for whatever reason `status` gives."""

    response_id: str
    status: str


@dataclass(frozen=True, slots=True)
class AudioDelta:
    """A piece of the model's voice, in `WIRE_FORMAT`."""

    response_id: str
    item_id: str
    content_index: int
    audio: bytes


@dataclass(frozen=True, slots=True)
class TranscriptDelta:
    """Some words, not yet settled."""

    speaker: Speaker
    text: str


@dataclass(frozen=True, slots=True)
class TranscriptSettled:
    """The words of a finished utterance."""

    speaker: Speaker
    text: str


@dataclass(frozen=True, slots=True)
class ServiceError:
    """The service refused something.

    Only the code is kept, and not every error has one. The message is dropped because a service
    is free to quote the request back, and the request may be somebody's words.
    """

    code: str | None


type Inbound = (
    CallerStartedSpeaking
    | CallerStoppedSpeaking
    | ResponseStarted
    | ResponseFinished
    | AudioDelta
    | TranscriptDelta
    | TranscriptSettled
    | ServiceError
)

_AUDIO_DELTAS: Final = frozenset({"response.output_audio.delta", "response.audio.delta"})
_ASSISTANT_DELTAS: Final = frozenset(
    {"response.output_audio_transcript.delta", "response.audio_transcript.delta"}
)
_ASSISTANT_SETTLED: Final = frozenset(
    {"response.output_audio_transcript.done", "response.audio_transcript.done"}
)


def parse(event: Event) -> Inbound | None:
    """What an inbound event means, or `None` for one this adapter has no use for.

    Raises `MalformedEventError` for a recognised event missing a field it needs.
    """
    event_type = event.get("type")
    match event_type:
        case "input_audio_buffer.speech_started":
            return CallerStartedSpeaking()
        case "input_audio_buffer.speech_stopped":
            return CallerStoppedSpeaking()
        case "response.created":
            return ResponseStarted(_text(_mapping(event, "response", event_type), "id", event_type))
        case "response.done":
            response = _mapping(event, "response", event_type)
            return ResponseFinished(
                _text(response, "id", event_type), _text(response, "status", event_type)
            )
        case "conversation.item.input_audio_transcription.delta":
            return _delta(Speaker.CALLER, event, event_type)
        case "conversation.item.input_audio_transcription.completed":
            return _settled(Speaker.CALLER, event, event_type)
        case "error":
            code = _mapping(event, "error", event_type).get("code")
            return ServiceError(code if isinstance(code, str) else None)
        case str() if event_type in _AUDIO_DELTAS:
            return _audio(event, event_type)
        case str() if event_type in _ASSISTANT_DELTAS:
            return _delta(Speaker.ASSISTANT, event, event_type)
        case str() if event_type in _ASSISTANT_SETTLED:
            return _settled(Speaker.ASSISTANT, event, event_type)
        case _:
            return None


def _audio(event: Event, event_type: str) -> AudioDelta:
    try:
        audio = base64.b64decode(_text(event, "delta", event_type), validate=True)
    except binascii.Error as error:
        raise MalformedEventError(event_type, "delta") from error
    content_index = event.get("content_index")
    if not isinstance(content_index, int) or isinstance(content_index, bool):
        raise MalformedEventError(event_type, "content_index")
    return AudioDelta(
        response_id=_text(event, "response_id", event_type),
        item_id=_text(event, "item_id", event_type),
        content_index=content_index,
        audio=audio,
    )


def _delta(speaker: Speaker, event: Event, event_type: str) -> TranscriptDelta | None:
    text = _text(event, "delta", event_type, allow_empty=True)
    # A fragment of whitespace is a real thing for a service to send and nothing for a caller.
    return TranscriptDelta(speaker, text) if text.strip() else None


def _settled(speaker: Speaker, event: Event, event_type: str) -> TranscriptSettled | None:
    text = _text(event, "transcript", event_type, allow_empty=True)
    # An utterance recognised as nothing — a cough, a door — settles as an empty transcript.
    return TranscriptSettled(speaker, text) if text.strip() else None


def _mapping(event: Event, field: str, event_type: str) -> Event:
    value = event.get(field)
    if not isinstance(value, dict):
        raise MalformedEventError(event_type, field)
    return value


def _text(event: Event, field: str, event_type: str, *, allow_empty: bool = False) -> str:
    value = event.get(field)
    if not isinstance(value, str) or (not allow_empty and not value):
        raise MalformedEventError(event_type, field)
    return value


# ---------------------------------------------------------------------------------- outbound


def configure_session(
    *,
    instructions: str,
    voice_id: str,
    language: str,
    transcription_model: str | None,
) -> dict[str, Any]:
    """Everything a session needs to begin, or to begin again after a reconnect.

    Input transcription is only requested when a model for it is named: which transcription
    models a server offers is the server's business, and asking for one it does not have is an
    error on every session.
    """
    audio_input: dict[str, Any] = {
        "format": _wire_format(),
        "turn_detection": {"type": "server_vad"},
    }
    if transcription_model is not None:
        audio_input["transcription"] = {"model": transcription_model, "language": language}
    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "instructions": instructions,
            "output_modalities": ["audio"],
            "audio": {
                "input": audio_input,
                "output": {"format": _wire_format(), "voice": voice_id},
            },
        },
    }


def update_instructions(instructions: str) -> dict[str, Any]:
    """New system context for a session already running."""
    return {"type": "session.update", "session": {"type": "realtime", "instructions": instructions}}


def append_audio(audio: bytes) -> dict[str, Any]:
    """Caller audio, already in `WIRE_FORMAT`."""
    return {"type": "input_audio_buffer.append", "audio": base64.b64encode(audio).decode("ascii")}


def cancel_response() -> dict[str, Any]:
    """Stop whatever response is in progress."""
    return {"type": "response.cancel"}


def truncate(*, item_id: str, content_index: int, audio_end_ms: int) -> dict[str, Any]:
    """Tell the service how much of an item was heard."""
    return {
        "type": "conversation.item.truncate",
        "item_id": item_id,
        "content_index": content_index,
        "audio_end_ms": audio_end_ms,
    }


def restore_turn(turn: Turn) -> dict[str, Any]:
    """A settled turn, replayed into a new connection's conversation."""
    if turn.speaker is Speaker.CALLER:
        role, content_type = "user", "input_text"
    else:
        role, content_type = "assistant", "output_text"
    return {
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": role,
            "content": [{"type": content_type, "text": turn.text}],
        },
    }


def _wire_format() -> dict[str, Any]:
    return {"type": "audio/pcm", "rate": WIRE_FORMAT.sample_rate_hz}
