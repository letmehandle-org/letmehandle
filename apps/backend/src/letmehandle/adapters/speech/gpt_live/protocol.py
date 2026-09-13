"""The GPT-Live session protocol, as data.

No I/O. Outbound, domain intentions become protocol events; inbound, protocol events become the
signals the session acts on. Everything the session knows about the wire passes through here.

What the protocol is, as far as this adapter uses it. JSON events over one websocket to
`/v1/live/sessions`, with no query parameters and the key as an `Authorization: Bearer` header.
Every event has a `type`.

Sent:
- `session.start` must be the first message. Its `session` carries `model`, `instructions` (the
  system context), `input` (earlier text messages, up to 128 of them and 8,192 tokens, as `message`
  items with a `role` and one text part), `audio.format` and `audio.output.voice`, and
  `delegation`. The format is one of `audio/pcm` at 24000 or 16000 Hz (16-bit little-endian mono),
  `audio/pcmu` or `audio/pcma` at 8000 Hz, and applies to both directions for the whole session.
  None of these can be changed once the session has started.
- `session.input_audio.append` carries base64 audio in `audio`, with no acknowledgment. Audio is
  streamed continuously, silence included: the service decides when to listen and when to speak.
- `session.instructions.append`, `session.thinking.append` and `session.commentary.append` feed
  text into the running model: trusted instructions, which may interrupt speech in progress; quiet
  context; and something to say aloud, paraphrased. Each takes a plain-string `content` of up to
  500 tokens and a required `delegation_id`, null for the whole session. Each is acknowledged by the
  same name ending `appended`, carrying the `client_event_id` of the `event_id` it was sent with.
- `session.close` asks for the session to be finalised.

Received:
- `session.started`: the session is ready. Nothing but `session.start` may be sent before it.
- `session.output_audio.delta`: base64 audio in `delta`, in the session's format. The service sends
  it at about the pace it plays, silence included, and there is no event for the end of speech.
- `session.input_transcript.delta` / `session.output_transcript.delta`: the caller's and the
  assistant's words as fragments in `delta`, each with `start_ms` and `end_ms` on the session's own
  timeline. There is no event that settles a turn; grouping fragments is the client's business.
- `session.delegation.created`: the model asked the client for help, naming it by `delegation.id`.
  The event carries no task text.
- `session.usage.updated`: cumulative voice seconds so far. Not read.
- `session.closed`: the session is final, with `reason` (`close_requested`, `expired`, `content`,
  `remote_hangup` or `connection_lost`) and `usage.seconds`, the voice time it is billed for.
- `error`: `error.type`, `error.code` (possibly null) and `error.client_event_id` when the error
  refuses a command. Moderation may cut the assistant's current speech off with an error and go on.

There is no event that stops the model speaking and none that says the caller began to. The model
listens while it speaks and yields when talked over, which is the protocol's barge-in; a client can
only stop playing what arrives. Anything unrecognised is ignored, because the service is entitled
to add events.

Sources: the GPT-Live WebSocket, session management, delegation and prompting guides, as published
at platform.openai.com.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from letmehandle.adapters.speech.session_support.history import Speaker
from letmehandle.domain.models.audio import AudioEncoding, AudioFormat

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from letmehandle.adapters.speech.session_support.history import Turn

type Event = Mapping[str, Any]

# The formats the protocol can carry, as it names them, and the one it speaks unless told.
_WIRE_NAMES: Final = {
    AudioFormat(AudioEncoding.PCM_S16LE, 24_000): "audio/pcm",
    AudioFormat(AudioEncoding.PCM_S16LE, 16_000): "audio/pcm",
    AudioFormat(AudioEncoding.MULAW, 8_000): "audio/pcmu",
    AudioFormat(AudioEncoding.ALAW, 8_000): "audio/pcma",
}
DEFAULT_WIRE_FORMAT: Final = AudioFormat(AudioEncoding.PCM_S16LE, 24_000)

# The session is closed because what was said broke the service's content policy. The one reason
# for a close that a replacement session would meet again.
CONTENT_CLOSE: Final = "content"


def wire_format_for(input_format: AudioFormat) -> AudioFormat:
    """The format a session opened for `input_format` speaks: the same one, wherever it can be.

    A phone line's G.711 audio then passes through in both directions without being converted,
    and a wideband source is not resampled on its way in.
    """
    return input_format if input_format in _WIRE_NAMES else DEFAULT_WIRE_FORMAT


class MalformedEventError(Exception):
    """An event this adapter recognises, missing something it cannot do without.

    Adapter-internal. Distinct from an unrecognised event, which is ignored: a known event with a
    missing field means the service and this adapter disagree about the protocol, and that is worth
    counting rather than a `KeyError` in the middle of a conversation.
    """

    def __init__(self, event_type: str, field: str) -> None:
        super().__init__(f"{event_type} arrived without a usable {field}")
        self.event_type = event_type
        self.field = field


# ---------------------------------------------------------------------------------- inbound


@dataclass(frozen=True, slots=True)
class SessionStarted:
    """The service accepted the session."""


@dataclass(frozen=True, slots=True)
class OutputAudio:
    """A piece of the assistant's voice, or of the silence between its words, in the wire format."""

    audio: bytes


@dataclass(frozen=True, slots=True)
class TranscriptFragment:
    """Some words, and where on the session's timeline they were said."""

    speaker: Speaker
    text: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class DelegationRequested:
    """The model asked this end for help with something."""

    delegation_id: str


@dataclass(frozen=True, slots=True)
class SessionClosed:
    """The session is final, and why. `seconds` is the voice time used, when the service said."""

    reason: str
    seconds: float | None


@dataclass(frozen=True, slots=True)
class ServiceError:
    """The service refused something.

    Only the code and type are kept. The message is dropped because a service is free to quote the
    request back, and the request may be somebody's words.
    """

    code: str | None
    error_type: str | None


type Inbound = (
    SessionStarted
    | OutputAudio
    | TranscriptFragment
    | DelegationRequested
    | SessionClosed
    | ServiceError
)

_TRANSCRIPTS: Final = {
    "session.input_transcript.delta": Speaker.CALLER,
    "session.output_transcript.delta": Speaker.ASSISTANT,
}


def parse(event: Event) -> Inbound | None:
    """What an inbound event means, or `None` for one this adapter has no use for.

    Raises `MalformedEventError` for a recognised event missing a field it needs.
    """
    event_type = event.get("type")
    match event_type:
        case "session.started":
            return SessionStarted()
        case "session.output_audio.delta":
            try:
                audio = base64.b64decode(_text(event, "delta", event_type), validate=True)
            except binascii.Error as error:
                raise MalformedEventError(event_type, "delta") from error
            return OutputAudio(audio)
        case "session.delegation.created":
            delegation = _mapping(event, "delegation", event_type)
            return DelegationRequested(_text(delegation, "id", event_type))
        case "session.closed":
            usage = event.get("usage")
            seconds = _number(usage.get("seconds")) if isinstance(usage, dict) else None
            reason = event.get("reason")
            return SessionClosed(reason if isinstance(reason, str) else "", seconds)
        case "error":
            # Counted whatever shape it takes: a refusal is worth seeing even when undescribed.
            refusal: object = event.get("error")
            body: Mapping[str, object] = refusal if isinstance(refusal, dict) else {}
            code, kind = body.get("code"), body.get("type")
            return ServiceError(
                code if isinstance(code, str) else None, kind if isinstance(kind, str) else None
            )
        case str() if event_type in _TRANSCRIPTS:
            text = _text(event, "delta", event_type, allow_empty=True)
            if not text.strip():
                # A fragment of whitespace is a real thing for a service to send and nothing for
                # a caller.
                return None
            return TranscriptFragment(
                _TRANSCRIPTS[event_type],
                text,
                _milliseconds(event, "start_ms", event_type),
                _milliseconds(event, "end_ms", event_type),
            )
        case _:
            return None


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


def _milliseconds(event: Event, field: str, event_type: str) -> int:
    value = _number(event.get(field))
    if value is None:
        raise MalformedEventError(event_type, field)
    return int(value)


def _number(value: object) -> float | None:
    # A boolean is an integer to Python and never a timestamp to anybody else.
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


# ---------------------------------------------------------------------------------- outbound


def start_session(
    *,
    model: str,
    instructions: str,
    voice_id: str,
    wire_format: AudioFormat,
    history: Sequence[Turn],
) -> dict[str, Any]:
    """The event that opens a session: always client delegation, so the model runs no tools."""
    session: dict[str, Any] = {
        "model": model,
        "instructions": instructions,
        "audio": {
            "format": {"type": _WIRE_NAMES[wire_format], "rate": wire_format.sample_rate_hz},
            "output": {"voice": voice_id},
        },
        "delegation": {"type": "client"},
    }
    if history:
        session["input"] = [_message(turn) for turn in history]
    return {"type": "session.start", "session": session}


def append_audio(audio: bytes) -> dict[str, Any]:
    """Caller audio, already in the session's wire format."""
    return {
        "type": "session.input_audio.append",
        "audio": base64.b64encode(audio).decode("ascii"),
    }


def append_instructions(content: str) -> dict[str, Any]:
    """Trusted instructions for the whole session, which may interrupt speech in progress."""
    return {"type": "session.instructions.append", "delegation_id": None, "content": content}


def append_commentary(content: str) -> dict[str, Any]:
    """Something for the model to say aloud, in its own words."""
    return {"type": "session.commentary.append", "delegation_id": None, "content": content}


def append_thinking(content: str, *, delegation_id: str | None = None) -> dict[str, Any]:
    """Quiet context, for one delegation or the whole session, which the model is not asked to say.

    Unlike instructions, it does not interrupt speech in progress.
    """
    return {"type": "session.thinking.append", "delegation_id": delegation_id, "content": content}


def close_session() -> dict[str, Any]:
    """Ask for the session to be finalised."""
    return {"type": "session.close"}


def _message(turn: Turn) -> dict[str, Any]:
    if turn.speaker is Speaker.CALLER:
        role, content_type = "user", "input_text"
    else:
        role, content_type = "assistant", "output_text"
    return {"type": "message", "role": role, "content": [{"type": content_type, "text": turn.text}]}
