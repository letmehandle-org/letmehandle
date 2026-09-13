"""A timeline mark is named in a closed vocabulary, never in content."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.timeline import MAX_MARK_NAME_LENGTH, MarkKind, TimelineMark

AT = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("name", ["agent_handling", "dial.timeout", "storage.final.circuit_open"])
def test_a_state_or_a_stage_and_its_failure_is_a_name(name: str) -> None:
    assert TimelineMark(AT, MarkKind.FAILURE, name).name == name


@pytest.mark.parametrize(
    "name",
    [
        "",
        "Agent Handling",
        "the caller asked for a refund",
        "+12025550123",
        "dial.2",
        "dial..timeout",
        "a" * (MAX_MARK_NAME_LENGTH + 1),
    ],
)
def test_anything_that_could_be_content_is_refused_without_repeating_it(name: str) -> None:
    with pytest.raises(InvariantError) as refused:
        TimelineMark(AT, MarkKind.TRANSITION, name)

    assert str(refused.value) == "a timeline mark is named in lower-case words, never in content"
