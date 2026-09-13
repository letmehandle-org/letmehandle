"""The ElevenLabs Agents protocol, both ways."""

from __future__ import annotations

import base64
from typing import Any

import pytest

from letmehandle.adapters.speech.elevenlabs import protocol
from letmehandle.adapters.speech.elevenlabs.protocol import (
    AgentAudio,
    AgentCorrected,
    AgentSaid,
    CallerSaid,
    ConversationBegan,
    Interrupted,
    Ping,
    ServiceError,
    ToolRequested,
)
from letmehandle.adapters.speech.session_support.fields import MalformedEventError
from letmehandle.domain.models.audio import AudioEncoding, AudioFormat


def metadata(input_format: str = "pcm_16000", output_format: str = "pcm_16000") -> dict[str, Any]:
    return {
        "type": "conversation_initiation_metadata",
        "conversation_initiation_metadata_event": {
            "conversation_id": "conversation-1",
            "user_input_audio_format": input_format,
            "agent_output_audio_format": output_format,
        },
    }


# ----------------------------------------------------------------------------------- inbound


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("pcm_16000", AudioFormat(AudioEncoding.PCM_S16LE, 16_000)),
        ("pcm_8000", AudioFormat(AudioEncoding.PCM_S16LE, 8_000)),
        ("pcm_44100", AudioFormat(AudioEncoding.PCM_S16LE, 44_100)),
        ("ulaw_8000", AudioFormat(AudioEncoding.MULAW, 8_000)),
    ],
)
def test_the_formats_a_conversation_begins_with_are_read(name: str, expected: AudioFormat) -> None:
    began = protocol.parse(metadata(input_format=name, output_format="pcm_24000"))
    assert began == ConversationBegan(expected, AudioFormat(AudioEncoding.PCM_S16LE, 24_000))


@pytest.mark.parametrize("name", ["mp3_44100_128", "pcm_12345", "ulaw_16000", "PCM_16000"])
def test_a_format_this_adapter_cannot_name_is_malformed_not_guessed(name: str) -> None:
    # Guessed, it would be played as noise to a caller.
    with pytest.raises(MalformedEventError, match="agent_output_audio_format"):
        protocol.parse(metadata(output_format=name))


def test_agent_audio_is_decoded_with_its_event_id() -> None:
    event = {
        "type": "audio",
        "audio_event": {"audio_base_64": base64.b64encode(b"\x01\x02").decode(), "event_id": 7},
    }
    assert protocol.parse(event) == AgentAudio(event_id=7, audio=b"\x01\x02")


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ({"type": "ping", "ping_event": {"event_id": 3, "ping_ms": 40}}, Ping(3)),
        ({"type": "interruption", "interruption_event": {"event_id": 9}}, Interrupted(9)),
        (
            {"type": "agent_response", "agent_response_event": {"agent_response": "hello"}},
            AgentSaid("hello"),
        ),
        (
            {"type": "user_transcript", "user_transcription_event": {"user_transcript": "hi"}},
            CallerSaid("hi"),
        ),
        (
            {
                "type": "agent_response_correction",
                "agent_response_correction_event": {
                    "original_agent_response": "let me tell you everything",
                    "corrected_agent_response": "let me",
                },
            },
            AgentCorrected("let me tell you everything", "let me"),
        ),
        (
            {
                "type": "client_tool_call",
                "client_tool_call": {"tool_name": "look_up", "tool_call_id": "t1"},
            },
            ToolRequested("t1", expects_response=True),
        ),
        (
            {
                "type": "client_tool_call",
                "client_tool_call": {"tool_call_id": "t2", "expects_response": False},
            },
            ToolRequested("t2", expects_response=False),
        ),
        (
            {"type": "client_error", "error_event": {"code": 1008, "message": "quoted words"}},
            ServiceError(1008),
        ),
        ({"type": "client_error"}, ServiceError(None)),
    ],
)
def test_known_events_are_recognised(event: dict[str, Any], expected: object) -> None:
    assert protocol.parse(event) == expected


@pytest.mark.parametrize(
    "event",
    [
        {"type": "agent_response", "agent_response_event": {"agent_response": "  "}},
        {"type": "user_transcript", "user_transcription_event": {"user_transcript": ""}},
        {"type": "vad_score", "vad_score_event": {"vad_score": 0.9}},
        {"type": "agent_chat_response_part", "text_response_part": {}},
        {"type": "something_added_next_year"},
        {"no_type": True},
    ],
)
def test_blank_words_and_events_with_no_use_here_are_ignored(event: dict[str, Any]) -> None:
    assert protocol.parse(event) is None


@pytest.mark.parametrize(
    ("event", "field"),
    [
        ({"type": "audio", "audio_event": {"audio_base_64": "!!", "event_id": 1}}, "audio_base_64"),
        ({"type": "audio", "audio_event": {"audio_base_64": "AA=="}}, "event_id"),
        ({"type": "ping", "ping_event": {"event_id": True}}, "event_id"),
        ({"type": "ping"}, "ping_event"),
        ({"type": "interruption", "interruption_event": {"event_id": "9"}}, "event_id"),
        ({"type": "agent_response", "agent_response_event": {}}, "agent_response"),
        ({"type": "client_tool_call", "client_tool_call": {"tool_name": "x"}}, "tool_call_id"),
    ],
)
def test_a_known_event_missing_what_it_needs_is_malformed(
    event: dict[str, Any], field: str
) -> None:
    with pytest.raises(MalformedEventError) as raised:
        protocol.parse(event)
    assert raised.value.field == field


# ---------------------------------------------------------------------------------- outbound


def test_a_conversation_is_opened_with_prompt_language_and_voice_overridden() -> None:
    opening = protocol.begin_conversation(
        prompt="answer for someone", language="en", voice_id="calm", first_message=None
    )
    assert opening == {
        "type": "conversation_initiation_client_data",
        "conversation_config_override": {
            "agent": {"prompt": {"prompt": "answer for someone"}, "language": "en"},
            "tts": {"voice_id": "calm"},
        },
    }


def test_a_voice_is_only_overridden_when_given() -> None:
    opening = protocol.begin_conversation(
        prompt="p", language="hi", voice_id=None, first_message=""
    )
    assert "tts" not in opening["conversation_config_override"]


def test_a_first_message_is_only_overridden_when_given() -> None:
    # Every field sent is an override the agent must allow, so none is sent without a reason.
    opening = protocol.begin_conversation(prompt="p", language="en", voice_id="v", first_message="")
    assert opening["conversation_config_override"]["agent"]["first_message"] == ""


def test_outbound_events_are_spelled_as_the_protocol_spells_them() -> None:
    assert protocol.caller_audio(b"\x00\x01") == {"user_audio_chunk": "AAE="}
    assert protocol.pong(12) == {"type": "pong", "event_id": 12}
    assert protocol.contextual_update("the user joined") == {
        "type": "contextual_update",
        "text": "the user joined",
    }
    assert protocol.refuse_tool("t1") == {
        "type": "client_tool_result",
        "tool_call_id": "t1",
        "result": protocol.TOOL_REFUSAL,
        "is_error": True,
    }
