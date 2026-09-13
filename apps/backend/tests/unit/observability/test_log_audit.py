"""Every log call names its event literally and passes no field that could carry content."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

import letmehandle
from letmehandle.observability.scrubbing import SENSITIVE_FIELDS

_PRODUCT: Final = Path(letmehandle.__file__).parent
_LEVELS: Final = frozenset({"debug", "info", "warning", "error", "exception", "critical", "msg"})


@dataclass(frozen=True, slots=True)
class LogCall:
    """One call that writes a line: where it is, the event it names, and its fields."""

    where: str
    event: ast.expr | None
    fields: tuple[str | None, ...]
    positional: int


def _log_calls() -> list[LogCall]:
    found: list[LogCall] = []
    for path in sorted(_PRODUCT.rglob("*.py")):
        where = str(path.relative_to(_PRODUCT))
        for node in ast.walk(ast.parse(path.read_text(), filename=where)):
            if not isinstance(node, ast.Call):
                continue
            fields = tuple(keyword.arg for keyword in node.keywords)
            target = node.func
            if isinstance(target, ast.Name) and target.id == "log_failure":
                # The helper takes the logger first, then the event, then the error.
                event = node.args[1] if len(node.args) > 1 else None
                found.append(LogCall(f"{where}:{node.lineno}", event, fields, len(node.args) - 3))
            elif (
                isinstance(target, ast.Attribute)
                and target.attr in _LEVELS
                and "log" in ast.unparse(target.value).lower()
            ):
                event = node.args[0] if node.args else None
                found.append(LogCall(f"{where}:{node.lineno}", event, fields, len(node.args) - 1))
    return found


LOG_CALLS: Final = _log_calls()


def test_the_audit_finds_the_products_log_calls() -> None:
    # Guards against an empty audit passing every test below.
    assert len(LOG_CALLS) >= 40
    assert any(call.where.startswith("application/orchestration/run.py") for call in LOG_CALLS)


@pytest.mark.parametrize("call", LOG_CALLS, ids=lambda call: call.where)
def test_no_log_call_passes_a_field_that_could_be_personal_or_secret(call: LogCall) -> None:
    offending = [
        field
        for field in call.fields
        if field is not None and re.sub(r"[_\-]", "", field).lower() in SENSITIVE_FIELDS
    ]

    assert offending == [], f"{call.where} logs {offending}"
    # `**fields` hides its names from this audit, and so is left to the one helper built around it.
    assert None not in call.fields or call.where.startswith("observability/logging.py")


@pytest.mark.parametrize("call", LOG_CALLS, ids=lambda call: call.where)
def test_every_event_is_named_in_the_source_and_nothing_is_formatted_into_it(call: LogCall) -> None:
    helper = call.where.startswith("observability/logging.py")
    if helper:
        # The one place an event arrives as a parameter: from call sites this audit reads.
        return

    assert isinstance(call.event, ast.Constant), f"{call.where} builds its event name"
    assert isinstance(call.event.value, str)
    assert re.fullmatch(r"[a-z_]+(\.[a-z_]+)*", call.event.value), call.where
    assert call.positional <= 0, f"{call.where} formats arguments into its line"
