"""Only orchestration changes a call's state.

The state machine is enforced by the call itself, but who may drive it is enforced here: the ledger
is the one writer of a call, and the only code allowed to reach it is the orchestration package. An
agent tool, an adapter or a route that moved a call would be a second owner of it, and a second
owner is how two writers come to disagree about where a call is (D-029). Checked by reading the
source, because the convenient import is exactly the one nobody notices in review.
"""

from __future__ import annotations

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "src" / "letmehandle"

LEDGER = "letmehandle.application.orchestration.ledger"
ORCHESTRATION = "application/orchestration/"

# The call's own module defines its mutators; the ledger is their one caller.
MUTATOR_CALLERS = frozenset({"domain/models/call.py", "application/orchestration/ledger.py"})


def sources() -> list[tuple[str, ast.Module]]:
    return [
        (path.relative_to(SOURCE).as_posix(), ast.parse(path.read_text(encoding="utf-8")))
        for path in sorted(SOURCE.rglob("*.py"))
    ]


def imported(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names


def session_mutations(tree: ast.Module) -> list[int]:
    """Lines moving a call, or putting a participant on or off one.

    Participants are told apart from the bridging port's operations of the same names, which dial
    and hang up legs and change no call, by what they are given: a call's participant is a
    `ParticipantRole` of the call model.
    """
    lines = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        name = node.func.attr
        given_a_role = any(
            isinstance(each, ast.Name) and each.id == "ParticipantRole"
            for argument in node.args
            for each in ast.walk(argument)
        )
        if name == "move_to" or (
            name in {"add_participant", "remove_participant"} and given_a_role
        ):
            lines.append(node.lineno)
    return lines


def test_there_are_sources_to_check() -> None:
    assert len(sources()) > 50


def test_only_orchestration_imports_the_ledger() -> None:
    offenders = [
        relative
        for relative, tree in sources()
        if not relative.startswith(ORCHESTRATION) and LEDGER in imported(tree)
    ]
    assert not offenders, f"the call ledger is imported outside orchestration by {offenders}"


def test_only_the_ledger_moves_a_call() -> None:
    offenders = [
        f"{relative}:{line}"
        for relative, tree in sources()
        if relative not in MUTATOR_CALLERS
        for line in session_mutations(tree)
    ]
    assert not offenders, f"a call is changed outside the ledger at {', '.join(offenders)}"


def test_the_checks_would_catch_a_violation() -> None:
    written = ast.parse("from letmehandle.application.orchestration.ledger import CallLedger")
    assert LEDGER in imported(written)
    assert session_mutations(ast.parse("call.move_to(CallState.FAILED, at_instant=now)"))
    assert session_mutations(ast.parse("call.add_participant(ParticipantRole.HUMAN, now)"))
    assert not session_mutations(ast.parse("step.bridge.add_participant(call_id, number)"))
