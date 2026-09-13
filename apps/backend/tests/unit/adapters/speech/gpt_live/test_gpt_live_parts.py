"""The protocol, the language rules, turn settling and the context, each on its own."""

from __future__ import annotations

import base64

import pytest

from letmehandle.adapters.speech.gpt_live import language, protocol
from letmehandle.adapters.speech.gpt_live.context import (
    APPEND_CHARACTERS,
    HISTORY_CHARACTERS,
    SessionContext,
)
from letmehandle.adapters.speech.gpt_live.protocol import (
    DelegationRequested,
    MalformedEventError,
    OutputAudio,
    ServiceError,
    SessionClosed,
    SessionStarted,
    TranscriptFragment,
)
from letmehandle.adapters.speech.gpt_live.turns import TurnAssembler
from letmehandle.adapters.speech.session_support.history import Speaker, Turn
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.audio import TELEPHONY_NARROWBAND

# ----------------------------------------------------------------------------------- inbound


def test_what_the_service_says_is_recognised() -> None:
    audio = base64.b64encode(b"\x01\x02").decode("ascii")
    assert protocol.parse({"type": "session.started"}) == SessionStarted()
    assert protocol.parse({"type": "session.output_audio.delta", "delta": audio}) == OutputAudio(
        b"\x01\x02"
    )
    assert protocol.parse(
        {"type": "session.input_transcript.delta", "delta": "hi", "start_ms": 10, "end_ms": 20.5}
    ) == TranscriptFragment(Speaker.CALLER, "hi", 10, 20)
    assert protocol.parse(
        {"type": "session.output_transcript.delta", "delta": "yes", "start_ms": 0, "end_ms": 5}
    ) == TranscriptFragment(Speaker.ASSISTANT, "yes", 0, 5)
    assert protocol.parse(
        {"type": "session.delegation.created", "delegation": {"id": "item_1"}}
    ) == DelegationRequested("item_1")


def test_a_final_word_carries_its_reason_and_voice_time_when_given() -> None:
    assert protocol.parse(
        {"type": "session.closed", "reason": "expired", "usage": {"seconds": 39}}
    ) == SessionClosed("expired", 39.0)
    assert protocol.parse({"type": "session.closed"}) == SessionClosed("", None)
    assert protocol.parse(
        {"type": "session.closed", "reason": 7, "usage": {"seconds": True}}
    ) == SessionClosed("", None)


def test_an_error_keeps_its_code_and_type_and_never_its_message() -> None:
    parsed = protocol.parse(
        {"type": "error", "error": {"type": "invalid_request_error", "code": "x", "message": "m"}}
    )
    assert parsed == ServiceError("x", "invalid_request_error")
    assert protocol.parse({"type": "error", "error": "unreadable"}) == ServiceError(None, None)


@pytest.mark.parametrize(
    "event",
    [
        {"type": "session.usage.updated"},
        {"type": "session.instructions.appended"},
        {"type": "something.new"},
        {"type": "session.output_transcript.delta", "delta": "  ", "start_ms": 0, "end_ms": 1},
    ],
)
def test_what_the_adapter_has_no_use_for_is_ignored(event: dict[str, object]) -> None:
    assert protocol.parse(event) is None


@pytest.mark.parametrize(
    ("event", "field"),
    [
        ({"type": "session.output_audio.delta"}, "delta"),
        ({"type": "session.output_audio.delta", "delta": "@@"}, "delta"),
        ({"type": "session.input_transcript.delta", "delta": 3}, "delta"),
        ({"type": "session.input_transcript.delta", "delta": "a", "end_ms": 1}, "start_ms"),
        (
            {"type": "session.input_transcript.delta", "delta": "a", "start_ms": 0, "end_ms": None},
            "end_ms",
        ),
        ({"type": "session.delegation.created", "delegation": {}}, "id"),
        ({"type": "session.delegation.created"}, "delegation"),
    ],
)
def test_a_recognised_event_missing_what_it_needs_is_malformed(
    event: dict[str, object], field: str
) -> None:
    with pytest.raises(MalformedEventError) as raised:
        protocol.parse(event)
    assert raised.value.field == field


# ---------------------------------------------------------------------------------- outbound


def test_a_session_resumed_is_started_with_what_was_said() -> None:
    started = protocol.start_session(
        model="m",
        instructions="i",
        voice_id="v",
        wire_format=TELEPHONY_NARROWBAND,
        history=[Turn(Speaker.CALLER, "hello"), Turn(Speaker.ASSISTANT, "hi")],
    )
    assert [item["role"] for item in started["session"]["input"]] == ["user", "assistant"]


def test_caller_audio_is_base64() -> None:
    assert protocol.append_audio(b"\x00\xff") == {
        "type": "session.input_audio.append",
        "audio": "AP8=",
    }
    assert protocol.close_session() == {"type": "session.close"}


# ---------------------------------------------------------------------------------- language


@pytest.mark.parametrize(
    ("text", "languages", "expected"),
    [
        ("नमस्ते, मेरा पार्सल कहाँ है?", ("en", "hi"), "hi"),
        ("Where is my parcel, please?", ("en", "hi"), "en"),
        ("Where is my parcel, please?", ("en-GB", "hi-IN"), "en"),
        ("OK", ("en", "hi"), None),
        ("ठीक", ("en", "hi"), None),
        # Half and half says nothing clearly.
        ("parcel पार्सल delivery डिलीवरी", ("en", "hi"), None),
        # A Hindi sentence with English names in it is Hindi.
        ("नमस्ते, मैं Blue Express Courier से बोल रहा हूँ। Alex जी का पार्सल है", ("en", "hi"), "hi"),
        # Hindi is not listed, so Hindi is not a language to switch to.
        ("नमस्ते, मेरा पार्सल कहाँ है?", ("en",), None),
        # Marathi and Hindi share a script, so the script cannot tell them apart.
        ("नमस्कार, माझे पार्सल कुठे आहे?", ("hi", "mr"), None),
        ("வணக்கம், என் பார்சல் எங்கே?", ("en", "ta"), "ta"),
        ("1234 5678 !!!", ("en", "hi"), None),
        # Letters of a script no listed language is written in.
        ("こんにちは、荷物はどこですか", ("en", "hi"), None),
    ],
)
def test_the_language_spoken_is_told_by_its_script(
    text: str, languages: tuple[str, ...], expected: str | None
) -> None:
    assert language.spoken_language(text, languages) == expected


def test_every_rule_is_written_in_the_language_it_asks_for() -> None:
    assert language.switch("hi").startswith("कॉलर अब हिंदी")
    assert language.resumption("hi").startswith("इस बातचीत में हिंदी")
    assert language.opening("en", "Hello.").startswith("Speak English")


def test_a_language_without_its_own_phrasing_is_asked_for_by_code() -> None:
    assert "code ta" in language.opening("ta", "Vanakkam.")
    assert '"Vanakkam."' in language.opening("ta", "Vanakkam.")
    assert "code ta" in language.resumption("ta")
    assert "code ta" in language.switch("ta")


# ------------------------------------------------------------------------------------- turns


def fragment(speaker: Speaker, text: str, start: int, end: int) -> TranscriptFragment:
    return TranscriptFragment(speaker, text, start, end)


def test_a_turn_settles_after_its_speaker_is_quiet_for_the_gap() -> None:
    turns = TurnAssembler(gap_ms=500)
    assert turns.fragment(fragment(Speaker.CALLER, "hello ", 0, 100)) == []
    assert turns.fragment(fragment(Speaker.CALLER, "there", 300, 400)) == []
    assert turns.advance(899) == []
    assert turns.advance(900) == [Turn(Speaker.CALLER, "hello there")]
    assert not turns.is_speaking(Speaker.CALLER)


def test_a_new_utterance_after_the_gap_settles_the_one_before() -> None:
    turns = TurnAssembler(gap_ms=500)
    turns.fragment(fragment(Speaker.CALLER, "one", 0, 100))
    assert turns.fragment(fragment(Speaker.CALLER, "two", 700, 800)) == [
        Turn(Speaker.CALLER, "one")
    ]


def test_the_other_speaker_beginning_settles_nothing_by_itself() -> None:
    # Fragments arrive late: the assistant's reply is often written down before the caller's last
    # words are, and settling the caller then would split what they said.
    turns = TurnAssembler(gap_ms=5_000)
    turns.fragment(fragment(Speaker.CALLER, "book a", 0, 400))
    assert turns.fragment(fragment(Speaker.ASSISTANT, "for how many", 400, 600)) == []
    assert turns.fragment(fragment(Speaker.CALLER, " table", 300, 500)) == []
    assert turns.advance(5_500) == [Turn(Speaker.CALLER, "book a table")]


def test_turns_still_pending_settle_in_the_order_they_went_quiet() -> None:
    turns = TurnAssembler(gap_ms=5_000)
    turns.fragment(fragment(Speaker.ASSISTANT, "long sentence", 0, 900))
    turns.fragment(fragment(Speaker.CALLER, "mm", 100, 200))
    assert turns.flush() == [Turn(Speaker.CALLER, "mm"), Turn(Speaker.ASSISTANT, "long sentence")]
    turns.fragment(fragment(Speaker.ASSISTANT, "gone", 1_000, 1_100))
    turns.discard(Speaker.ASSISTANT)
    assert turns.flush() == []


def test_a_turn_must_be_allowed_some_quiet() -> None:
    with pytest.raises(InvariantError):
        TurnAssembler(gap_ms=0)


# ----------------------------------------------------------------------------------- context


def make_context(*, instructions: str = "i", history_turns: int = 8) -> SessionContext:
    return SessionContext(
        model="m",
        instructions=instructions,
        voice_id="v",
        wire_format=TELEPHONY_NARROWBAND,
        greeting="Hello.",
        language="en",
        history_turns=history_turns,
    )


def test_an_unchanged_context_adds_nothing() -> None:
    context = make_context(instructions="a\nb")
    context.instructions = "b\na\n\n"
    assert context.catch_up() == []


def test_a_line_longer_than_one_addition_is_split() -> None:
    context = make_context(instructions="")
    context.instructions = "short\n" + "z" * (APPEND_CHARACTERS * 2 + 5) + "\ntail"
    pieces = [str(event["content"]) for event in context.catch_up()]
    assert all(len(piece) <= APPEND_CHARACTERS for piece in pieces)
    assert "".join(pieces).count("z") == APPEND_CHARACTERS * 2 + 5
    assert pieces[-1].endswith("tail")


def test_a_replacement_is_told_the_most_recent_turns_that_fit() -> None:
    context = make_context(history_turns=8)
    context.history.remember(Turn(Speaker.CALLER, "x" * HISTORY_CHARACTERS))
    context.history.remember(Turn(Speaker.ASSISTANT, "recent"))
    started = context.start(resuming=True)
    assert [item["content"][0]["text"] for item in started["session"]["input"]] == ["recent"]
    assert "input" not in context.start(resuming=False)["session"]
