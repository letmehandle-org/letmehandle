"""What a data-modifying statement reports about the rows it touched."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from sqlalchemy import CursorResult
    from sqlalchemy.engine import Result


def affected_rows(result: Result[Any]) -> int:
    """How many rows the statement changed; `Session.execute` types a `CursorResult` as `Result`."""
    return int(cast("CursorResult[Any]", result).rowcount or 0)
