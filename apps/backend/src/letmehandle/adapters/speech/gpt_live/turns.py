"""Settling transcript fragments into turns, on a protocol that never says a turn is over.

The service sends each speaker's words as fragments stamped with where they fall on the session's
timeline, and nothing more. A turn is settled here when its speaker has been quiet for `gap_ms` of
that timeline, or when the session ends.

The timeline moves with every fragment's timestamps and with the assistant's audio, which the
service streams at the pace it plays, silence included; so a caller who stops speaking is settled
while the assistant is still thinking of an answer, not only once it gives one.

Quiet is the only signal, deliberately. Both speakers' fragments arrive late and in bursts, a
second or more behind the audio, and the two overlap: the assistant often begins before the last of
the caller's words has been written down. Settling one speaker because the other began splits
utterances, even words, whenever a late fragment turns up; quiet on the timeline does not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from letmehandle.adapters.speech.session_support.history import Speaker, Turn
from letmehandle.domain.errors import InvariantError

if TYPE_CHECKING:
    from letmehandle.adapters.speech.gpt_live.protocol import TranscriptFragment

# How long a speaker is quiet, on the session's timeline, before their words are a settled turn.
# Longer than the pause between two sentences and the lateness of a fragment, short enough that
# what the caller said reaches the rest of the call a moment after the reply to it begins.
DEFAULT_GAP_MS: Final = 1_500


@dataclass(slots=True)
class _Pending:
    """One speaker's words since their last settled turn."""

    parts: list[str] = field(default_factory=list)
    end_ms: float = 0.0


class TurnAssembler:
    """Each speaker's fragments, settled into turns in the order they settle."""

    def __init__(self, gap_ms: int = DEFAULT_GAP_MS) -> None:
        if gap_ms <= 0:
            raise InvariantError("a turn must be allowed some quiet before it settles")
        self._gap_ms = gap_ms
        self._pending: dict[Speaker, _Pending] = {}
        self._now_ms = 0.0

    def is_speaking(self, speaker: Speaker) -> bool:
        """Whether `speaker` has words not yet settled."""
        return speaker in self._pending

    def fragment(self, fragment: TranscriptFragment) -> list[Turn]:
        """Take a fragment, and return the turns it settled before it: never its own."""
        settled = self.advance(fragment.start_ms)
        pending = self._pending.setdefault(fragment.speaker, _Pending())
        # Fragments are joined exactly as they came: the service puts the spaces in itself.
        pending.parts.append(fragment.text)
        pending.end_ms = max(pending.end_ms, fragment.end_ms)
        self._now_ms = max(self._now_ms, fragment.end_ms)
        return settled

    def advance(self, now_ms: float) -> list[Turn]:
        """Move the timeline to `now_ms`, and return the turns that went quiet long enough."""
        self._now_ms = max(self._now_ms, now_ms)
        quiet = [
            speaker
            for speaker, pending in self._pending.items()
            if self._now_ms - pending.end_ms >= self._gap_ms
        ]
        return [self._settle(speaker) for speaker in sorted(quiet, key=self._ended_at)]

    def flush(self) -> list[Turn]:
        """Settle everything still pending, as a session ends."""
        return [self._settle(speaker) for speaker in sorted(self._pending, key=self._ended_at)]

    def discard(self, speaker: Speaker) -> None:
        """Forget `speaker`'s unsettled words."""
        self._pending.pop(speaker, None)

    def _ended_at(self, speaker: Speaker) -> float:
        return self._pending[speaker].end_ms

    def _settle(self, speaker: Speaker) -> Turn:
        pending = self._pending.pop(speaker)
        return Turn(speaker, "".join(pending.parts).strip())
