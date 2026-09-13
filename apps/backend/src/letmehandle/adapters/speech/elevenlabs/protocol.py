"""The ElevenLabs Agents conversation protocol as data: events in, signals out, no I/O."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from letmehandle.adapters.speech.session_support.fields import (
    MalformedEventError,
    as_integer,
    base64_audio,
    encode_audio,
    integer,
    mapping,
    text,
)
from letmehandle.domain.models.audio import AudioEncoding, AudioFormat

if TYPE_CHECKING:
    from letmehandle.adapters.speech.session_support.fields import Event

# The event that begins a conversation and names its audio formats.
INITIATION_METADATA: Final = "conversation_initiation_metadata"

# What an agent speaks and hears unless it has been configured otherwise.
DEFAULT_WIRE_FORMAT: Final = AudioFormat(AudioEncoding.PCM_S16LE, 16_000)

_MULAW: Final = "ulaw_8000"
_PCM: Final = re.compile(r"pcm_(8000|16000|22050|24000|44100|48000)")

# What a tool call is told when this adapter runs no tools: words for the model.
TOOL_REFUSAL: Final = "this client runs no tools"

# ---------------------------------------------------------------------------------- inbound


@dataclass(frozen=True, slots=True)
class ConversationBegan:
    """The service accepted the conversation, and says which audio it hears and speaks."""

    input_format: AudioFormat
    output_format: AudioFormat


@dataclass(frozen=True, slots=True)
class Ping:
    """The service asking whether this end is still there."""

    event_id: int


@dataclass(frozen=True, slots=True)
class AgentAudio:
    """A piece of the agent's voice, in the format the conversation began with."""

    event_id: int
    audio: bytes


@dataclass(frozen=True, slots=True)
class AgentSaid:
    """The words of the agent's reply."""

    text: str


@dataclass(frozen=True, slots=True)
class AgentCorrected:
    """A reply cut short, reduced to what was said before the cut. `said` may be blank."""

    original: str
    said: str


@dataclass(frozen=True, slots=True)
class CallerSaid:
    """The caller's settled words."""

    text: str


@dataclass(frozen=True, slots=True)
class Interrupted:
    """The caller spoke over the agent. Audio up to `event_id` is of the reply that stopped."""

    event_id: int


@dataclass(frozen=True, slots=True)
class ToolRequested:
    """The agent asked this end to run a tool."""

    tool_call_id: str
    expects_response: bool


@dataclass(frozen=True, slots=True)
class ServiceError:
    """The service refused something; only the code is kept, since a message may quote words."""

    code: int | None


type Inbound = (
    ConversationBegan
    | Ping
    | AgentAudio
    | AgentSaid
    | AgentCorrected
    | CallerSaid
    | Interrupted
    | ToolRequested
    | ServiceError
)


def parse(event: Event) -> Inbound | None:
    """What an inbound event means, `None` when unused; raises `MalformedEventError`."""
    event_type = event.get("type")
    match event_type:
        case str() if event_type == INITIATION_METADATA:
            body = mapping(event, "conversation_initiation_metadata_event", event_type)
            return ConversationBegan(
                input_format=_format(body, "user_input_audio_format", event_type),
                output_format=_format(body, "agent_output_audio_format", event_type),
            )
        case "ping":
            return Ping(integer(mapping(event, "ping_event", event_type), "event_id", event_type))
        case "audio":
            return _audio(mapping(event, "audio_event", event_type), event_type)
        case "agent_response":
            body = mapping(event, "agent_response_event", event_type)
            words = text(body, "agent_response", event_type, allow_empty=True)
            return AgentSaid(words) if words.strip() else None
        case "agent_response_correction":
            body = mapping(event, "agent_response_correction_event", event_type)
            return AgentCorrected(
                original=text(body, "original_agent_response", event_type, allow_empty=True),
                said=text(body, "corrected_agent_response", event_type, allow_empty=True),
            )
        case "user_transcript":
            body = mapping(event, "user_transcription_event", event_type)
            words = text(body, "user_transcript", event_type, allow_empty=True)
            # An utterance recognised as nothing — a cough, a door — settles as an empty one.
            return CallerSaid(words) if words.strip() else None
        case "interruption":
            body = mapping(event, "interruption_event", event_type)
            return Interrupted(integer(body, "event_id", event_type))
        case "client_tool_call":
            body = mapping(event, "client_tool_call", event_type)
            expects = body.get("expects_response", True)
            return ToolRequested(
                tool_call_id=text(body, "tool_call_id", event_type, allow_empty=True),
                expects_response=expects is not False,
            )
        case "client_error":
            # Counted whatever shape it takes: a refusal is worth seeing even when undescribed.
            error: object = event.get("error_event")
            code: object = error.get("code") if isinstance(error, dict) else None
            return ServiceError(as_integer(code))
        case _:
            return None


def _audio(body: Event, event_type: str) -> AgentAudio:
    audio = base64_audio(body, "audio_base_64", event_type, allow_empty=True)
    return AgentAudio(integer(body, "event_id", event_type), audio)


def _format(body: Event, field: str, event_type: str) -> AudioFormat:
    name = text(body, field, event_type, allow_empty=True)
    if name == _MULAW:
        return AudioFormat(AudioEncoding.MULAW, 8_000)
    if match := _PCM.fullmatch(name):
        return AudioFormat(AudioEncoding.PCM_S16LE, int(match.group(1)))
    # A format this adapter cannot name is one it would play as noise.
    raise MalformedEventError(event_type, field)


# ---------------------------------------------------------------------------------- outbound


def begin_conversation(
    *, prompt: str, language: str, voice_id: str | None, first_message: str | None
) -> dict[str, Any]:
    """The opening event; an empty `first_message` waits for the caller."""
    agent: dict[str, Any] = {"prompt": {"prompt": prompt}, "language": language}
    if first_message is not None:
        agent["first_message"] = first_message
    overrides: dict[str, Any] = {"agent": agent}
    if voice_id is not None:
        overrides["tts"] = {"voice_id": voice_id}
    return {
        "type": "conversation_initiation_client_data",
        "conversation_config_override": overrides,
    }


def caller_audio(audio: bytes) -> dict[str, Any]:
    """Caller audio, already in the agent's input format."""
    return {"user_audio_chunk": encode_audio(audio)}


def pong(event_id: int) -> dict[str, Any]:
    """The answer to a ping."""
    return {"type": "pong", "event_id": event_id}


def contextual_update(text: str) -> dict[str, Any]:
    """Background information for the agent, without interrupting it."""
    return {"type": "contextual_update", "text": text}


def refuse_tool(tool_call_id: str) -> dict[str, Any]:
    """The answer to a tool call this adapter cannot run."""
    return {
        "type": "client_tool_result",
        "tool_call_id": tool_call_id,
        "result": TOOL_REFUSAL,
        "is_error": True,
    }
