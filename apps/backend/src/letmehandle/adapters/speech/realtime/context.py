"""What a session has to be told again when its connection is replaced.

A reconnect is a new conversation as far as the service knows. Without this the model comes back
from a dropped connection with no instructions, the wrong voice and no idea what was just said,
which to the caller is an assistant that has forgotten them mid-sentence.

Held in memory only, and bounded. The history is somebody's words, kept for exactly as long as
the session lives (D-013), and a long call must not turn it into an ever-growing replay that
takes longer to send than the outage it recovers from.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Any

from letmehandle.adapters.speech.realtime import protocol
from letmehandle.domain.errors import InvariantError

if TYPE_CHECKING:
    from letmehandle.adapters.speech.realtime.protocol import Turn


class SessionContext:
    """Instructions, voice and a bounded memory of settled turns."""

    def __init__(
        self,
        *,
        instructions: str,
        voice_id: str,
        language: str,
        transcription_model: str | None,
        history_turns: int,
    ) -> None:
        if history_turns < 0:
            raise InvariantError("a session cannot remember a negative number of turns")
        self.instructions = instructions
        self._voice_id = voice_id
        self._language = language
        self._transcription_model = transcription_model
        self._history: deque[Turn] = deque(maxlen=history_turns)

    def remember(self, turn: Turn) -> None:
        """Keep a settled turn, forgetting the oldest once the bound is reached."""
        self._history.append(turn)

    def forget(self, turn: Turn) -> None:
        """Drop a turn remembered too early. By identity, so an identical earlier turn stays."""
        kept = [each for each in self._history if each is not turn]
        self._history.clear()
        self._history.extend(kept)

    def configuration(self) -> dict[str, Any]:
        """The event that sets a new connection up as this session."""
        return protocol.configure_session(
            instructions=self.instructions,
            voice_id=self._voice_id,
            language=self._language,
            transcription_model=self._transcription_model,
        )

    def restoration(self) -> list[dict[str, Any]]:
        """Configuration, then what was said, oldest first."""
        return [self.configuration(), *(protocol.restore_turn(turn) for turn in self._history)]
