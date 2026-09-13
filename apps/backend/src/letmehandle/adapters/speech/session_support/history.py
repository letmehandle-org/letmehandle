"""What was said in a session, bounded and in memory only, to tell a replacement (D-013)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from letmehandle.domain.errors import InvariantError

if TYPE_CHECKING:
    from collections.abc import Iterator


class Speaker(StrEnum):
    """Who said a settled turn."""

    CALLER = "caller"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class Turn:
    """Something said and settled."""

    speaker: Speaker
    text: str


class ConversationHistory:
    """A bounded memory of settled turns, oldest first."""

    def __init__(self, turns: int) -> None:
        if turns < 0:
            raise InvariantError("a session cannot remember a negative number of turns")
        self._turns: deque[Turn] = deque(maxlen=turns)

    def remember(self, turn: Turn) -> None:
        """Keep a settled turn, forgetting the oldest once the bound is reached."""
        self._turns.append(turn)

    def replace(self, turn: Turn, replacement: Turn | None) -> None:
        """Swap a remembered turn, by identity, for another, or drop it with `None`."""
        kept: list[Turn] = []
        for each in self._turns:
            if each is not turn:
                kept.append(each)
            elif replacement is not None:
                kept.append(replacement)
        self._turns.clear()
        self._turns.extend(kept)

    def __iter__(self) -> Iterator[Turn]:
        return iter(tuple(self._turns))
