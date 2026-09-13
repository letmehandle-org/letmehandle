"""What a conversation is opened with, and what a replacement is told of the one that dropped.

ElevenLabs cannot resume a conversation, so a reconnect starts another, and all it can be told of
the first is what fits in its prompt: the instructions, what was added to them since, and a
bounded record of what was said. It is a reminder written in words, not the service's own memory
restored, and the model treats it as such — which is the limitation the provider declares.

Held in memory only, and bounded, for the reasons the history itself gives (D-013).
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Any, Final

from letmehandle.adapters.speech.elevenlabs import protocol
from letmehandle.adapters.speech.session_support.history import ConversationHistory, Speaker

if TYPE_CHECKING:
    from letmehandle.adapters.speech.session_support.history import Turn

# Context updates kept for a resumed conversation. Few are sent in a call — the user joining is
# the one this exists for — so the bound is there to make a loop sending them harmless, not to
# ration them.
CONTEXT_UPDATES_KEPT: Final = 8

_UPDATES_HEADING: Final = "Information added during the conversation, oldest first:"
_RESUMED_HEADING: Final = (
    "The connection to this conversation was lost and has been restored. Do not greet the "
    "caller again. What was said before, oldest first:"
)
_LABELS: Final = {Speaker.CALLER: "Caller", Speaker.ASSISTANT: "You"}


class ConversationContext:
    """Instructions, voice, greeting, the updates sent since, and a bounded memory of settled turns.

    `voice_id` is `None` for an agent that chooses its voice per language itself.
    """

    def __init__(
        self,
        *,
        instructions: str,
        voice_id: str | None,
        greeting: str,
        language: str,
        history_turns: int,
    ) -> None:
        self._instructions = instructions
        self._voice_id = voice_id
        self._greeting = greeting
        self._language = language
        self._updates: deque[str] = deque(maxlen=CONTEXT_UPDATES_KEPT)
        self.history = ConversationHistory(history_turns)

    def add_update(self, text: str) -> None:
        """Keep an update, so a resumed conversation is told it too."""
        self._updates.append(text)

    def opening(self, *, resuming: bool) -> dict[str, Any]:
        """The event that opens a conversation as this session.

        A resumed conversation is asked not to greet the caller: they have been talking for a
        while, and a cheerful hello halfway through is the clearest sign something broke.
        """
        return protocol.begin_conversation(
            prompt=self._prompt(resuming=resuming),
            language=self._language,
            voice_id=self._voice_id,
            first_message="" if resuming else self._greeting,
        )

    def _prompt(self, *, resuming: bool) -> str:
        sections = [self._instructions]
        if self._updates:
            sections.append("\n".join([_UPDATES_HEADING, *self._updates]))
        turns: list[Turn] = list(self.history) if resuming else []
        if turns:
            lines = (f"{_LABELS[turn.speaker]}: {turn.text}" for turn in turns)
            sections.append("\n".join([_RESUMED_HEADING, *lines]))
        return "\n\n".join(sections)
