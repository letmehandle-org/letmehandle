"""What a session has to be told again when its connection is replaced."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from letmehandle.adapters.speech.realtime import protocol
from letmehandle.adapters.speech.session_support.history import ConversationHistory

if TYPE_CHECKING:
    from letmehandle.adapters.speech.session_support.history import Turn


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
        self.instructions = instructions
        self._voice_id = voice_id
        self._language = language
        self._transcription_model = transcription_model
        self._history = ConversationHistory(history_turns)

    def remember(self, turn: Turn) -> None:
        """Keep a settled turn, forgetting the oldest once the bound is reached."""
        self._history.remember(turn)

    def forget(self, turn: Turn) -> None:
        """Drop a turn remembered too early. By identity, so an identical earlier turn stays."""
        self._history.replace(turn, None)

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
