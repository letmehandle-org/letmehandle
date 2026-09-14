"""Only the orchestration package reaches the ledger that changes a call's state (D-029)."""

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
    """Lines moving a call or putting a call's `ParticipantRole` participant on or off it."""
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
