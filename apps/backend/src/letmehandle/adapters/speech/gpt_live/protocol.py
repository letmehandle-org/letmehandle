"""The GPT-Live session protocol as data: events in, signals out, no I/O."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from letmehandle.adapters.speech.session_support.fields import (
    MalformedEventError,
    as_number,
    base64_audio,
    encode_audio,
    mapping,
    text,
)
from letmehandle.adapters.speech.session_support.history import Speaker
from letmehandle.domain.models.audio import AudioEncoding, AudioFormat

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from letmehandle.adapters.speech.session_support.fields import Event
    from letmehandle.adapters.speech.session_support.history import Turn

# The formats the protocol can carry, as it names them, and the one it speaks unless told.
_WIRE_NAMES: Final = {
    AudioFormat(AudioEncoding.PCM_S16LE, 24_000): "audio/pcm",
    AudioFormat(AudioEncoding.PCM_S16LE, 16_000): "audio/pcm",
    AudioFormat(AudioEncoding.MULAW, 8_000): "audio/pcmu",
    AudioFormat(AudioEncoding.ALAW, 8_000): "audio/pcma",
}
DEFAULT_WIRE_FORMAT: Final = AudioFormat(AudioEncoding.PCM_S16LE, 24_000)

# The close reason for content policy, the one a replacement session would meet again.
CONTENT_CLOSE: Final = "content"


def wire_format_for(input_format: AudioFormat) -> AudioFormat:
    """The format a session opened for `input_format` speaks: the same one wherever it can be."""
    return input_format if input_format in _WIRE_NAMES else DEFAULT_WIRE_FORMAT


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
    """The service refused something; only code and type are kept, as a message may quote words."""

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
    """What an inbound event means, `None` when unused; raises `MalformedEventError`."""
    event_type = event.get("type")
    match event_type:
        case "session.started":
            return SessionStarted()
        case "session.output_audio.delta":
            return OutputAudio(base64_audio(event, "delta", event_type))
        case "session.delegation.created":
            delegation = mapping(event, "delegation", event_type)
            return DelegationRequested(text(delegation, "id", event_type))
        case "session.closed":
            usage = event.get("usage")
            seconds = as_number(usage.get("seconds")) if isinstance(usage, dict) else None
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
            words = text(event, "delta", event_type, allow_empty=True)
            if not words.strip():
                # A fragment of whitespace is nothing for a caller.
                return None
            return TranscriptFragment(
                _TRANSCRIPTS[event_type],
                words,
                _milliseconds(event, "start_ms", event_type),
                _milliseconds(event, "end_ms", event_type),
            )
        case _:
            return None


def _milliseconds(event: Event, field: str, event_type: str) -> int:
    value = as_number(event.get(field))
    if value is None:
        raise MalformedEventError(event_type, field)
    return int(value)


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
        "audio": encode_audio(audio),
    }


def append_instructions(content: str) -> dict[str, Any]:
    """Trusted instructions for the whole session, which may interrupt speech in progress."""
    return {"type": "session.instructions.append", "delegation_id": None, "content": content}


def append_commentary(content: str) -> dict[str, Any]:
    """Something for the model to say aloud, in its own words."""
    return {"type": "session.commentary.append", "delegation_id": None, "content": content}


def append_thinking(content: str, *, delegation_id: str | None = None) -> dict[str, Any]:
    """Quiet context for one delegation or the whole session, which does not interrupt speech."""
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
