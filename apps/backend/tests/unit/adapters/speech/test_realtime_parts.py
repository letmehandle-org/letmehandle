"""A realtime session's remembered context: what a replacement connection is told."""

from __future__ import annotations

import pytest

from letmehandle.adapters.speech.realtime.context import SessionContext
from letmehandle.adapters.speech.session_support.history import Speaker, Turn
from letmehandle.domain.errors import InvariantError

# ----------------------------------------------------------------------------------- context


def make_context(history_turns: int = 2) -> SessionContext:
    return SessionContext(
        instructions="first",
        voice_id="calm",
        language="en",
        transcription_model=None,
        history_turns=history_turns,
    )


def test_restoration_is_configuration_then_turns_oldest_first() -> None:
    context = make_context()
    context.instructions = "second"
    context.remember(Turn(Speaker.CALLER, "one"))
    context.remember(Turn(Speaker.ASSISTANT, "two"))

    configuration, *turns = context.restoration()

    assert configuration["session"]["instructions"] == "second"
    assert [turn["item"]["content"][0]["text"] for turn in turns] == ["one", "two"]


def test_history_forgets_the_oldest_turn_past_its_bound() -> None:
    context = make_context(history_turns=2)
    for text in ("one", "two", "three"):
        context.remember(Turn(Speaker.CALLER, text))
    texts = [turn["item"]["content"][0]["text"] for turn in context.restoration()[1:]]
    assert texts == ["two", "three"]


def test_forgetting_a_turn_leaves_an_identical_earlier_one() -> None:
    # The assistant may well say "one moment" twice; only the one that was not heard goes.
    context = make_context(history_turns=3)
    earlier, later = Turn(Speaker.ASSISTANT, "one moment"), Turn(Speaker.ASSISTANT, "one moment")
    context.remember(earlier)
    context.remember(Turn(Speaker.CALLER, "ok"))
    context.remember(later)
    context.forget(later)
    texts = [turn["item"]["content"][0]["text"] for turn in context.restoration()[1:]]
    assert texts == ["one moment", "ok"]


def test_a_negative_history_bound_is_refused() -> None:
    with pytest.raises(InvariantError):
        make_context(history_turns=-1)
