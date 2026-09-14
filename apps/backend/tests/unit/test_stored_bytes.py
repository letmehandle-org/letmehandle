"""The stored-bytes scan finds a caller's words wherever a row holds a piece of them."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from tests.support.stored_bytes import assert_nowhere_in

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

WORDS = "the parcel is at the side door"


class _Rows:
    def __init__(self, rows: list[str]) -> None:
        self._rows = rows

    def scalars(self) -> list[str]:
        return self._rows


class _Session:
    def __init__(self, rows: list[str]) -> None:
        self._rows = rows

    async def execute(self, _statement: object) -> _Rows:
        return _Rows(self._rows)


def _session(*rows: str) -> AsyncSession:
    return cast("AsyncSession", _Session(list(rows)))


async def test_rows_without_the_words_pass() -> None:
    await assert_nowhere_in(_session("(1,ciphertext)"), "calls", 1, WORDS)


@pytest.mark.parametrize("stored", [WORDS, WORDS[:8], WORDS[-8:], WORDS.encode().hex()])
async def test_any_stored_piece_of_the_words_fails(stored: str) -> None:
    with pytest.raises(AssertionError):
        await assert_nowhere_in(_session(f"(1,{stored})"), "calls", 1, WORDS)
