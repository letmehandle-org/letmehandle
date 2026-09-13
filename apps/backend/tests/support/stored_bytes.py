"""Reading what a table actually stores, to prove somebody's words are not in it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# The length of the overlapping pieces of the words searched for.
FRAGMENT = 8


async def assert_nowhere_in(session: AsyncSession, table: str, rows: int, words: str) -> None:
    """Scan every column of every row, as text and as raw bytes, for the words.

    Casting the whole row to text covers every column there is now and any added later, so a
    new column that stores the words in clear fails this without anybody updating the test.
    """
    result = await session.execute(text(f"SELECT t::text FROM {table} t"))  # noqa: S608
    rendered = list(result.scalars())
    assert len(rendered) == rows
    starts = {*range(0, len(words) - FRAGMENT + 1, FRAGMENT // 2), max(len(words) - FRAGMENT, 0)}
    pieces = {words, *(words[start : start + FRAGMENT] for start in starts)}
    for row in rendered:
        for piece in pieces:
            assert piece not in row
            assert piece.encode().hex() not in row
