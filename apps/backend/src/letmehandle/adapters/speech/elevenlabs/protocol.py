"""The ElevenLabs Agents conversation protocol, as data.

No I/O. Outbound, domain intentions become protocol events; inbound, protocol events become the
signals the session acts on. Everything the session knows about the wire passes through here.

What the protocol is, as far as this adapter uses it. JSON events over one websocket to
`/v1/convai/conversation`, the agent named by the `agent_id` query parameter. Almost every event
has a `type` and carries its fields in an object named after it.

The agent is configured at the service, not here. Its model, its tools and — importantly — its
audio formats are the agent's settings; a client can only override the fields the agent allows
to be overridden, and overrides are disabled by default. A field sent without its override
enabled is refused.

Sent:
- `conversation_initiation_client_data` opens the conversation. Its
  `conversation_config_override` carries `agent.prompt.prompt` (the system context),
  `agent.first_message`, `agent.language` and `tts.voice_id`. An empty first message is how a
  client asks the agent to wait for the caller instead of greeting them.
- `user_audio_chunk` is caller audio, base64, in the agent's input format. The one event with
  no `type`: the field name is the whole event.
- `pong` answers a `ping` with the same `event_id`. The service pings to keep the conversation
  alive and expects an answer at once, so the session answers every ping itself.
- `contextual_update` gives the agent background information as `text`. It does not interrupt
  and does not replace the prompt: it is added to the conversation, which is weaker than the
  instruction change the realtime protocol allows, and the provider says so.
- `client_tool_result` answers a `client_tool_call` by `tool_call_id`, with `result` and
  `is_error`. This adapter runs no client tools, so every call is answered as an error rather
  than left waiting.
- `user_message` and `user_activity` exist — typed text treated as speech, and a nudge that
  resets the agent's turn timeout — and are not used: a spoken conversation has neither.

Received:
- `conversation_initiation_metadata` confirms the conversation, with `conversation_id`,
  `user_input_audio_format` and `agent_output_audio_format`. The formats are `pcm_<rate>` —
  16-bit little-endian mono linear audio at 8000, 16000, 22050, 24000, 44100 or 48000 Hz — or
  `ulaw_8000`, G.711 μ-law. Both default to `pcm_16000`; a telephony agent is usually set to
  `ulaw_8000`. The session converts to whatever this event says rather than assuming.
- `audio`: base64 agent speech in `audio_base_64`, with an increasing integer `event_id`.
- `agent_response`: the words of the agent's reply, in `agent_response`.
- `agent_response_correction`: after an interruption, the reply cut down to what was actually
  said, as `original_agent_response` and `corrected_agent_response`.
- `user_transcript`: the caller's settled words, in `user_transcript`. There are no partials.
- `interruption`: the service heard the caller speak over the agent and stopped it. Its
  `event_id` is the boundary: audio with an `event_id` at or below it belongs to the reply that
  was interrupted, and is dropped if it arrives late.
- `ping`: `event_id`, and `ping_ms`, the service's measure of latency.
- `vad_score`: the probability the caller is speaking. Recognised and not used; the service does
  its own turn-taking, and a threshold chosen here would be a guess.
- `client_tool_call`: `tool_name`, `tool_call_id`, `parameters` and `expects_response`.
- `client_error`: `error_event` with an integer `code`, `error_name` and `message`.

There is no event that stops the agent speaking. Barge-in is the service's own, announced by
`interruption`; a client that wants silence can only stop playing what arrives.

A conversation cannot be resumed. A `conversation_id` names one for monitoring and for reading
back afterwards, but nothing reattaches a new connection to it, so a reconnect is a new
conversation and the only way to carry the old one across is to write it into the new one's
prompt. Anything else the service sends is ignored, because it is entitled to add events.

Sources: the ElevenLabs Agents WebSocket API reference, its client and client-to-server event
guides, and its overrides and authentication guides, as published at elevenlabs.io.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from letmehandle.domain.models.audio import AudioEncoding, AudioFormat

if TYPE_CHECKING:
    from collections.abc import Mapping

# What an agent speaks and hears unless it has been configured otherwise.
DEFAULT_WIRE_FORMAT: Final = AudioFormat(AudioEncoding.PCM_S16LE, 16_000)

_MULAW: Final = "ulaw_8000"
_PCM: Final = re.compile(r"pcm_(8000|16000|22050|24000|44100|48000)")

# What a tool call is told when this adapter has no tool to run. Words for the agent, not a
# caller: it is read by the model, which then decides what to say.
TOOL_REFUSAL: Final = "this client runs no tools"

type Event = Mapping[str, Any]


class MalformedEventError(Exception):
    """An event this adapter recognises, missing something it cannot do without.

    Adapter-internal. Distinct from an unrecognised event, which is ignored: a known event with a
    missing field means the service and this adapter disagree about the protocol, and that is
    worth counting rather than a `KeyError` in the middle of a conversation.
    """

    def __init__(self, event_type: str, field: str) -> None:
        super().__init__(f"{event_type} arrived without a usable {field}")
        self.event_type = event_type
        self.field = field


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
    """The service refused something.

    Only the code is kept. The message is dropped because a service is free to quote the request
    back, and the request may be somebody's words.
    """

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
    """What an inbound event means, or `None` for one this adapter has no use for.

    Raises `MalformedEventError` for a recognised event missing a field it needs.
    """
    event_type = event.get("type")
    match event_type:
        case "conversation_initiation_metadata":
            body = _body(event, "conversation_initiation_metadata_event", event_type)
            return ConversationBegan(
                input_format=_format(body, "user_input_audio_format", event_type),
                output_format=_format(body, "agent_output_audio_format", event_type),
            )
        case "ping":
            return Ping(_integer(_body(event, "ping_event", event_type), "event_id", event_type))
        case "audio":
            return _audio(_body(event, "audio_event", event_type), event_type)
        case "agent_response":
            body = _body(event, "agent_response_event", event_type)
            text = _text(body, "agent_response", event_type)
            return AgentSaid(text) if text.strip() else None
        case "agent_response_correction":
            body = _body(event, "agent_response_correction_event", event_type)
            return AgentCorrected(
                original=_text(body, "original_agent_response", event_type),
                said=_text(body, "corrected_agent_response", event_type),
            )
        case "user_transcript":
            body = _body(event, "user_transcription_event", event_type)
            text = _text(body, "user_transcript", event_type)
            # An utterance recognised as nothing — a cough, a door — settles as an empty one.
            return CallerSaid(text) if text.strip() else None
        case "interruption":
            body = _body(event, "interruption_event", event_type)
            return Interrupted(_integer(body, "event_id", event_type))
        case "client_tool_call":
            body = _body(event, "client_tool_call", event_type)
            expects = body.get("expects_response", True)
            return ToolRequested(
                tool_call_id=_text(body, "tool_call_id", event_type),
                expects_response=expects is not False,
            )
        case "client_error":
            # Counted whatever shape it takes: a refusal is worth seeing even when undescribed.
            error: object = event.get("error_event")
            code: object = error.get("code") if isinstance(error, dict) else None
            return ServiceError(_as_integer(code))
        case _:
            return None


def _audio(body: Event, event_type: str) -> AgentAudio:
    try:
        audio = base64.b64decode(_text(body, "audio_base_64", event_type), validate=True)
    except binascii.Error as error:
        raise MalformedEventError(event_type, "audio_base_64") from error
    return AgentAudio(_integer(body, "event_id", event_type), audio)


def _format(body: Event, field: str, event_type: str) -> AudioFormat:
    name = _text(body, field, event_type)
    if name == _MULAW:
        return AudioFormat(AudioEncoding.MULAW, 8_000)
    if match := _PCM.fullmatch(name):
        return AudioFormat(AudioEncoding.PCM_S16LE, int(match.group(1)))
    # A format this adapter cannot name is one it would play as noise.
    raise MalformedEventError(event_type, field)


def _body(event: Event, field: str, event_type: str) -> Event:
    value = event.get(field)
    if not isinstance(value, dict):
        raise MalformedEventError(event_type, field)
    return value


def _text(body: Event, field: str, event_type: str) -> str:
    value = body.get(field)
    if not isinstance(value, str):
        raise MalformedEventError(event_type, field)
    return value


def _integer(body: Event, field: str, event_type: str) -> int:
    value = _as_integer(body.get(field))
    if value is None:
        raise MalformedEventError(event_type, field)
    return value


def _as_integer(value: object) -> int | None:
    # A boolean is an integer to Python and never an event id to anybody else.
    return value if isinstance(value, int) and not isinstance(value, bool) else None


# ---------------------------------------------------------------------------------- outbound


def begin_conversation(
    *, prompt: str, language: str, voice_id: str, first_message: str | None
) -> dict[str, Any]:
    """The event that opens a conversation as this session.

    `first_message` is only sent when given. Left out, the agent greets the caller as it was
    configured to; given as empty, it waits for them — which is what a conversation resumed after
    a dropped connection wants, and costs one more override the agent must allow.
    """
    agent: dict[str, Any] = {"prompt": {"prompt": prompt}, "language": language}
    if first_message is not None:
        agent["first_message"] = first_message
    return {
        "type": "conversation_initiation_client_data",
        "conversation_config_override": {"agent": agent, "tts": {"voice_id": voice_id}},
    }


def caller_audio(audio: bytes) -> dict[str, Any]:
    """Caller audio, already in the agent's input format."""
    return {"user_audio_chunk": base64.b64encode(audio).decode("ascii")}


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
