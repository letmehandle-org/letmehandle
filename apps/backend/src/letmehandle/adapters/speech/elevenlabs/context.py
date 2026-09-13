"""What a conversation is opened with, and the prompt a replacement is told of the one dropped."""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Any, Final

from letmehandle.adapters.speech.elevenlabs import protocol
from letmehandle.adapters.speech.session_support.history import ConversationHistory, Speaker

if TYPE_CHECKING:
    from collections.abc import Sequence

    from letmehandle.adapters.speech.session_support.history import Turn

# Context updates kept for a resumed conversation, bounded so a loop sending them is harmless.
CONTEXT_UPDATES_KEPT: Final = 8

_UPDATES_HEADING: Final = "Information added during the conversation, oldest first:"
_RESUMED_HEADING: Final = (
    "The connection to this conversation was lost and has been restored. Do not greet the "
    "caller again. What was said before, oldest first:"
)
_LABELS: Final = {Speaker.CALLER: "Caller", Speaker.ASSISTANT: "You"}


def switching(languages: Sequence[str]) -> str:
    """What an agent of several languages is told: call its language tool to switch (D-039)."""
    return (
        f"This conversation can be held in these languages: {', '.join(languages)}. When the "
        "caller speaks one of them other than the language you are speaking, call the "
        "language_detection tool to change to it before you answer, without asking the caller "
        "first."
    )


class ConversationContext:
    """Instructions, voice (`None` lets the agent choose), greeting, updates and settled turns."""

    def __init__(
        self,
        *,
        instructions: str,
        voice_id: str | None,
        greeting: str,
        language: str,
        switching: str | None,
        history_turns: int,
    ) -> None:
        self._instructions = instructions
        self._voice_id = voice_id
        self._greeting = greeting
        self._language = language
        self._switching = switching
        self._updates: deque[str] = deque(maxlen=CONTEXT_UPDATES_KEPT)
        self.history = ConversationHistory(history_turns)

    def add_update(self, text: str) -> None:
        """Keep an update, so a resumed conversation is told it too."""
        self._updates.append(text)

    def opening(self, *, resuming: bool) -> dict[str, Any]:
        """The event opening a conversation as this session; a resumed one is told not to greet."""
        return protocol.begin_conversation(
            prompt=self._prompt(resuming=resuming),
            language=self._language,
            voice_id=self._voice_id,
            first_message="" if resuming else self._greeting,
        )

    def _prompt(self, *, resuming: bool) -> str:
        sections = [self._instructions]
        if self._switching is not None:
            sections.append(self._switching)
        if self._updates:
            sections.append("\n".join([_UPDATES_HEADING, *self._updates]))
        turns: list[Turn] = list(self.history) if resuming else []
        if turns:
            lines = (f"{_LABELS[turn.speaker]}: {turn.text}" for turn in turns)
            sections.append("\n".join([_RESUMED_HEADING, *lines]))
        return "\n\n".join(sections)
