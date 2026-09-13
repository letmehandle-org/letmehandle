"""What a session is opened with, what it is told as things change, and what a replacement is told.

A running session's instructions cannot be replaced, only added to, and an addition is limited to
500 tokens. The application hands a session its whole context again whenever something changes —
usually one line of it — so what is sent is the lines that are new, in pieces small enough to be
accepted, and the whole context is kept for a session that has to start again.

A session cannot be resumed either. A replacement is a new session started with the context as it
now stands and a bounded record of what was said, as the protocol's own history, and told not to
greet the caller again. The model is reminded of the conversation; it does not get it back.

Held in memory only, and bounded, for the reasons the history itself gives (D-013).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from letmehandle.adapters.speech.gpt_live import language, protocol
from letmehandle.adapters.speech.session_support.history import ConversationHistory

if TYPE_CHECKING:
    from letmehandle.adapters.speech.session_support.history import Turn
    from letmehandle.domain.models.audio import AudioFormat

# Characters in one addition. The protocol counts tokens, and a character is at most about one token
# in any script this product speaks, so this stays inside the limit with room to spare.
APPEND_CHARACTERS: Final = 1_000
# Characters of history a replacement session is started with, well inside the protocol's limit.
HISTORY_CHARACTERS: Final = 12_000
_UPDATE_HEADING: Final = (
    "Your instructions have changed. These lines are new; where they differ from what you were "
    "told before, they replace it:"
)


class SessionContext:
    """The model, voice, instructions, language and a bounded memory of settled turns."""

    def __init__(
        self,
        *,
        model: str,
        instructions: str,
        voice_id: str,
        wire_format: AudioFormat,
        greeting: str,
        language: str,
        history_turns: int,
    ) -> None:
        self._model = model
        # The instructions as they now stand, and as the current connection was last told them.
        self.instructions = instructions
        self._told = instructions
        self._voice_id = voice_id
        self._wire_format = wire_format
        self._greeting = greeting
        # The language the model was last told to speak.
        self.language = language
        self.history = ConversationHistory(history_turns)

    def start(self, *, resuming: bool) -> dict[str, Any]:
        """The event that starts a session as this one, with what was said when resuming."""
        self._told = self.instructions
        return protocol.start_session(
            model=self._model,
            instructions=self.instructions,
            voice_id=self._voice_id,
            wire_format=self._wire_format,
            history=self._recent_turns() if resuming else (),
        )

    def opening(self, *, resuming: bool) -> list[dict[str, Any]]:
        """What a started session is told first: greet in its language, or carry on in it."""
        if resuming:
            return [protocol.append_instructions(language.resumption(self.language))]
        return [
            protocol.append_instructions(language.opening(self.language, self._greeting)),
            protocol.append_commentary(language.BEGIN),
        ]

    def catch_up(self) -> list[dict[str, Any]]:
        """The additions that tell the current connection what changed since it was last told."""
        earlier = set(self._told.splitlines())
        self._told = self.instructions
        added = [
            line for line in self.instructions.splitlines() if line.strip() and line not in earlier
        ]
        if not added:
            return []
        return [
            protocol.append_instructions(piece)
            for piece in _pieces([_UPDATE_HEADING, *added], APPEND_CHARACTERS)
        ]

    def _recent_turns(self) -> list[Turn]:
        kept: list[Turn] = []
        remaining = HISTORY_CHARACTERS
        for turn in reversed(list(self.history)):
            remaining -= len(turn.text)
            if remaining < 0:
                break
            kept.append(turn)
        return kept[::-1]


def _pieces(lines: list[str], limit: int) -> list[str]:
    """Lines joined into pieces of at most `limit` characters, a line longer than that split."""
    parts = [line[start : start + limit] for line in lines for start in range(0, len(line), limit)]
    pieces: list[str] = []
    current = ""
    for part in parts:
        if current and len(current) + 1 + len(part) > limit:
            pieces.append(current)
            current = part
        else:
            current = f"{current}\n{part}" if current else part
    pieces.append(current)
    return pieces
