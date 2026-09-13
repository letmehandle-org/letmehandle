"""The protocol, both ways, including the dialects compatible servers still speak."""

from __future__ import annotations

import base64
from typing import Any

import pytest

from letmehandle.adapters.speech.realtime import protocol
from letmehandle.adapters.speech.realtime.protocol import (
    AudioDelta,
    CallerStartedSpeaking,
    CallerStoppedSpeaking,
    ResponseFinished,
    ResponseStarted,
    ServiceError,
    TranscriptDelta,
    TranscriptSettled,
)
from letmehandle.adapters.speech.session_support.fields import MalformedEventError
from letmehandle.adapters.speech.session_support.history import Speaker, Turn
from tests.support.scripted_realtime_connection import audio_delta

# ----------------------------------------------------------------------------------- inbound


def test_speech_signals_are_recognised() -> None:
    assert protocol.parse({"type": "input_audio_buffer.speech_started"}) == CallerStartedSpeaking()
    assert protocol.parse({"type": "input_audio_buffer.speech_stopped"}) == CallerStoppedSpeaking()


def test_a_response_is_bracketed() -> None:
    assert protocol.parse(
        {"type": "response.created", "response": {"id": "resp_1"}}
    ) == ResponseStarted("resp_1")
    assert protocol.parse(
        {"type": "response.done", "response": {"id": "resp_1", "status": "cancelled"}}
    ) == ResponseFinished("resp_1", "cancelled")


@pytest.mark.parametrize("event_type", ["response.output_audio.delta", "response.audio.delta"])
def test_audio_is_read_under_either_name(event_type: str) -> None:
    # The earlier protocol called this `response.audio.delta`, and servers built against it
    # still send that. Recognising only one spelling is a session that connects and says nothing.
    event = {**audio_delta("resp_1", "item_1", size=4), "type": event_type}
    assert protocol.parse(event) == AudioDelta("resp_1", "item_1", 0, bytes(4))


@pytest.mark.parametrize(
    ("event_type", "field", "expected"),
    [
        ("response.output_audio_transcript.delta", "delta", TranscriptDelta),
        ("response.audio_transcript.delta", "delta", TranscriptDelta),
        ("response.output_audio_transcript.done", "transcript", TranscriptSettled),
        ("response.audio_transcript.done", "transcript", TranscriptSettled),
    ],
)
def test_the_assistant_s_words_are_read_under_either_name(
    event_type: str, field: str, expected: type[TranscriptDelta | TranscriptSettled]
) -> None:
    assert protocol.parse({"type": event_type, field: "hello"}) == expected(
        Speaker.ASSISTANT, "hello"
    )


def test_the_caller_s_words_are_read() -> None:
    assert protocol.parse(
        {"type": "conversation.item.input_audio_transcription.delta", "delta": "hel"}
    ) == TranscriptDelta(Speaker.CALLER, "hel")
    assert protocol.parse(
        {"type": "conversation.item.input_audio_transcription.completed", "transcript": "hello"}
    ) == TranscriptSettled(Speaker.CALLER, "hello")


@pytest.mark.parametrize(
    "event",
    [
        {"type": "response.output_audio_transcript.delta", "delta": " "},
        {"type": "conversation.item.input_audio_transcription.completed", "transcript": ""},
    ],
)
def test_words_that_are_only_whitespace_are_nothing(event: dict[str, Any]) -> None:
    # An utterance recognised as a cough settles as an empty transcript. It is not a turn.
    assert protocol.parse(event) is None


def test_a_service_error_keeps_its_code_and_drops_its_message() -> None:
    parsed = protocol.parse(
        {"type": "error", "error": {"code": "bad_thing", "message": "you said: something private"}}
    )
    assert parsed == ServiceError("bad_thing")
    assert "private" not in repr(parsed)
    assert protocol.parse({"type": "error", "error": {"message": "no code"}}) == ServiceError(None)


@pytest.mark.parametrize(
    "event",
    [
        {"type": "session.created", "session": {}},
        {"type": "rate_limits.updated"},
        {"type": "response.output_text.delta", "delta": "x"},
        {"no": "type at all"},
    ],
)
def test_what_is_not_recognised_is_ignored(event: dict[str, Any]) -> None:
    # A compatible server is entitled to send events this adapter has no use for.
    assert protocol.parse(event) is None


@pytest.mark.parametrize(
    ("event", "field"),
    [
        ({"type": "response.created"}, "response"),
        ({"type": "response.created", "response": {"id": ""}}, "id"),
        ({"type": "response.done", "response": {"id": "r"}}, "status"),
        ({"type": "error", "error": "not a mapping"}, "error"),
        ({**audio_delta("r", "i"), "delta": "not base64!"}, "delta"),
        ({**audio_delta("r", "i"), "delta": 7}, "delta"),
        ({**audio_delta("r", "i"), "content_index": True}, "content_index"),
        ({**audio_delta("r", "i"), "content_index": None}, "content_index"),
        ({**audio_delta("r", "i"), "item_id": None}, "item_id"),
        ({"type": "response.output_audio_transcript.done"}, "transcript"),
    ],
)
def test_a_known_event_missing_what_it_needs_is_a_typed_failure(
    event: dict[str, Any], field: str
) -> None:
    with pytest.raises(MalformedEventError) as raised:
        protocol.parse(event)
    assert raised.value.field == field


# ---------------------------------------------------------------------------------- outbound


def test_session_configuration_uses_the_current_shape() -> None:
    event = protocol.configure_session(
        instructions="answer for someone", voice_id="calm", language="en", transcription_model=None
    )
    session = event["session"]
    assert event["type"] == "session.update"
    assert session["type"] == "realtime"
    assert session["instructions"] == "answer for someone"
    assert session["audio"]["input"]["format"] == {"type": "audio/pcm", "rate": 24_000}
    assert session["audio"]["output"] == {
        "format": {"type": "audio/pcm", "rate": 24_000},
        "voice": "calm",
    }
    assert session["audio"]["input"]["turn_detection"] == {"type": "server_vad"}
    # No transcription model named, so none is asked for: asking a server for one it does not
    # have fails every session.
    assert "transcription" not in session["audio"]["input"]


def test_transcription_is_requested_only_when_a_model_is_named() -> None:
    event = protocol.configure_session(
        instructions="i", voice_id="v", language="en", transcription_model="a-transcriber"
    )
    assert event["session"]["audio"]["input"]["transcription"] == {
        "model": "a-transcriber",
        "language": "en",
    }


def test_new_instructions_change_nothing_else() -> None:
    # The voice cannot change mid-session, so an update that repeated it would be refused.
    assert protocol.update_instructions("the user has joined") == {
        "type": "session.update",
        "session": {"type": "realtime", "instructions": "the user has joined"},
    }


def test_audio_is_sent_as_base64() -> None:
    event = protocol.append_audio(b"\x00\x01\x02\x03")
    assert event["type"] == "input_audio_buffer.append"
    assert base64.b64decode(event["audio"]) == b"\x00\x01\x02\x03"


def test_cancel_and_truncate() -> None:
    assert protocol.cancel_response() == {"type": "response.cancel"}
    assert protocol.truncate(item_id="item_1", content_index=0, audio_end_ms=1_500) == {
        "type": "conversation.item.truncate",
        "item_id": "item_1",
        "content_index": 0,
        "audio_end_ms": 1_500,
    }


@pytest.mark.parametrize(
    ("speaker", "role", "content_type"),
    [(Speaker.CALLER, "user", "input_text"), (Speaker.ASSISTANT, "assistant", "output_text")],
)
def test_a_turn_is_restored_as_a_message(speaker: Speaker, role: str, content_type: str) -> None:
    assert protocol.restore_turn(Turn(speaker, "words")) == {
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": role,
            "content": [{"type": content_type, "text": "words"}],
        },
    }
