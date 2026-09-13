"""The state diagram in the call flow documentation is the transition table.

Parsed from the documentation's mermaid block and compared with `ALLOWED`, so that adding a state or
a transition without drawing it, or drawing one the code does not allow, fails the build.
"""

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
    # The document says failed is reachable from every state that is not an ending, and leaves those
    # arrows out; they are added back here, exactly as the table adds them.
    implied = {(state, CallState.FAILED) for state in CallState if state not in TERMINAL}
    allowed = {(source, target) for source, targets in ALLOWED.items() for target in targets}
    assert drawn_transitions() | implied == allowed
    assert not drawn_transitions() & implied
