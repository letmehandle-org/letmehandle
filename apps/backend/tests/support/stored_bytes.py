"""Reading what a table actually stores, to prove somebody's words are not in it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def assert_nowhere_in(session: AsyncSession, table: str, rows: int, words: str) -> None:
    """Scan every column of every row, as text and as raw bytes, for the words.

    Casting the whole row to text covers every column there is now and any added later, so a
    new column that stores the words in clear fails this without anybody updating the test.
    """
    result = await session.execute(text(f"SELECT t::text FROM {table} t"))  # noqa: S608
    rendered = list(result.scalars())
    assert len(rendered) == rows
    needle = words.encode()
    for row in rendered:
        assert words not in row
        assert needle.hex() not in row
    fragments = [words[i : i + 8] for i in range(0, len(words) - 8, 4)]
    for row in rendered:
        for fragment in fragments:
            assert fragment not in row
            assert fragment.encode().hex() not in row
