"""The OpenAI Realtime-compatible protocol as data, reading beta event names too: no I/O."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from letmehandle.adapters.speech.session_support.fields import (
    base64_audio,
    encode_audio,
    integer,
    mapping,
    text,
)
from letmehandle.adapters.speech.session_support.history import Speaker, Turn
from letmehandle.domain.models.audio import AudioEncoding, AudioFormat

if TYPE_CHECKING:
    from letmehandle.adapters.speech.session_support.fields import Event

# The one linear format the protocol speaks. Everything else is converted to and from this.
WIRE_FORMAT: Final = AudioFormat(AudioEncoding.PCM_S16LE, 24_000)

# The error a cancel earns when no response was in progress, which is expected.
NOTHING_TO_CANCEL: Final = "response_cancel_not_active"

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
    """The service refused something; only the code is kept, since a message may quote words."""

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
    """What an inbound event means, `None` when unused; raises `MalformedEventError`."""
    event_type = event.get("type")
    match event_type:
        case "input_audio_buffer.speech_started":
            return CallerStartedSpeaking()
        case "input_audio_buffer.speech_stopped":
            return CallerStoppedSpeaking()
        case "response.created":
            return ResponseStarted(text(mapping(event, "response", event_type), "id", event_type))
        case "response.done":
            response = mapping(event, "response", event_type)
            return ResponseFinished(
                text(response, "id", event_type), text(response, "status", event_type)
            )
        case "conversation.item.input_audio_transcription.delta":
            return _delta(Speaker.CALLER, event, event_type)
        case "conversation.item.input_audio_transcription.completed":
            return _settled(Speaker.CALLER, event, event_type)
        case "error":
            code = mapping(event, "error", event_type).get("code")
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
    audio = base64_audio(event, "delta", event_type)
    return AudioDelta(
        response_id=text(event, "response_id", event_type),
        item_id=text(event, "item_id", event_type),
        content_index=integer(event, "content_index", event_type),
        audio=audio,
    )


def _delta(speaker: Speaker, event: Event, event_type: str) -> TranscriptDelta | None:
    words = text(event, "delta", event_type, allow_empty=True)
    # A fragment of whitespace is a real thing for a service to send and nothing for a caller.
    return TranscriptDelta(speaker, words) if words.strip() else None


def _settled(speaker: Speaker, event: Event, event_type: str) -> TranscriptSettled | None:
    words = text(event, "transcript", event_type, allow_empty=True)
    # An utterance recognised as nothing — a cough, a door — settles as an empty transcript.
    return TranscriptSettled(speaker, words) if words.strip() else None


# ---------------------------------------------------------------------------------- outbound


def configure_session(
    *,
    instructions: str,
    voice_id: str,
    language: str,
    transcription_model: str | None,
) -> dict[str, Any]:
    """Configuration beginning a session, asking for transcription only when a model is named."""
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
    return {"type": "input_audio_buffer.append", "audio": encode_audio(audio)}


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
