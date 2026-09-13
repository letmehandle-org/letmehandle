"""The state diagram in the call flow documentation is the transition table."""

from __future__ import annotations

import re
from pathlib import Path

from letmehandle.domain.models.call_state import ALLOWED, TERMINAL, CallState

DOCUMENT = Path(__file__).resolve().parents[4] / "docs" / "architecture" / "call-flow.md"

_ARROW = re.compile(r"^\s*(\S+)\s*-->\s*(\S+)\s*$")


def drawn_transitions() -> set[tuple[CallState, CallState]]:
    """Every arrow in the state diagram between two states, without the start and end markers."""
    text = DOCUMENT.read_text(encoding="utf-8")
    diagram = text.split("```mermaid\nstateDiagram-v2\n", 1)[1].split("```", 1)[0]
    arrows = [match.groups() for line in diagram.splitlines() if (match := _ARROW.match(line))]
    return {
        (CallState(source), CallState(target))
        for source, target in arrows
        if "[*]" not in (source, target)
    }


def test_the_diagram_draws_every_allowed_move_and_no_other() -> None:
    # The document leaves out the arrows to failed from every non-ending state; they are added back.
    implied = {(state, CallState.FAILED) for state in CallState if state not in TERMINAL}
    allowed = {(source, target) for source, targets in ALLOWED.items() for target in targets}
    assert drawn_transitions() | implied == allowed
    assert not drawn_transitions() & implied
