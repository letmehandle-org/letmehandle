"""The assistant's voice on one call: a speech session, the conversation over it, and what it knows.

Owned by a run. Opening is bounded, the conversation runs as a task this owns, and every way it
stops — the speaker gone, the session over, a failure — reaches the run as one input. Stopping is
safe at any point and more than once, and releases the task and the session before it returns.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from letmehandle.application.agent.prompts import load_prompts
from letmehandle.application.orchestration.inputs import ConversationStopped, Heard
from letmehandle.application.preferences.context import build_preference_context
from letmehandle.application.speech.conversation import (
    Conversation,
    Transcript,
    TranscriptTurn,
)
from letmehandle.domain.failures import FailureKind, classify
from letmehandle.domain.ports.voice import resolve_voice
from letmehandle.observability import catalogue
from letmehandle.observability.logging import get_logger, log_failure

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import timedelta

    from letmehandle.application.orchestration.inputs import Input
    from letmehandle.application.orchestration.plan import Converse
    from letmehandle.domain.models.identifiers import CallId
    from letmehandle.domain.models.preferences import UserPreferences
    from letmehandle.domain.ports.call_transport import ParticipantOutcome
    from letmehandle.domain.ports.clock import Clock
    from letmehandle.domain.ports.metrics import MetricsRecorder
    from letmehandle.domain.ports.speech import SpeechSession

logger = get_logger(__name__)

SPEECH_CLOSE_FAILED: Final = catalogue.count("call.speech_close_failed", kind=FailureKind)


class UserReach(StrEnum):
    """Where reaching the user stands, as the assistant is told it."""

    NOT_ASKED = "not_asked"
    BEING_REACHED = "being_reached"
    ON_THE_CALL = "on_the_call"
    NOT_REACHED = "not_reached"


@dataclass(frozen=True, slots=True)
class Situation:
    """What the assistant is told about the call beyond the user's preferences."""

    user: UserReach = UserReach.NOT_ASKED
    outcome: ParticipantOutcome | None = None

    def as_data(self) -> dict[str, str]:
        data = {"user": self.user.value}
        if self.outcome is not None:
            data["outcome"] = self.outcome.value
        return data


class _HeardTranscript(Transcript):
    """A transcript that tells the run each settled line as it is recorded."""

    def __init__(self, post: Callable[[Input], None]) -> None:
        super().__init__()
        self._post = post

    def record(self, turn: TranscriptTurn) -> None:
        super().record(turn)
        self._post(Heard(turn))


class Speaking:
    """One call's speech session and the conversation carried over it, once started."""

    def __init__(
        self,
        *,
        call_id: CallId,
        post: Callable[[Input], None],
        clock: Clock,
        metrics: MetricsRecorder,
    ) -> None:
        self._call_id = call_id
        self._post = post
        self._clock = clock
        self._metrics = metrics
        self._session: tuple[SpeechSession, UserPreferences] | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def is_speaking(self) -> bool:
        """Whether a conversation is still running."""
        return self._task is not None and not self._task.done()

    async def start(self, step: Converse, preferences: UserPreferences, bound: timedelta) -> None:
        """Open the session and start the conversation, within `bound`. Raises when it cannot."""
        assistance = step.assistance
        async with asyncio.timeout(bound.total_seconds()):
            voice = await resolve_voice(
                assistance.voices, preferences.voice, locale=preferences.locale
            )
            session = await assistance.speech.connect(
                system_context=self._context(preferences, Situation()),
                voice_id=voice,
                locale=preferences.locale,
                input_format=step.audio.audio_format(),
            )
        self._session = (session, preferences)
        conversation = Conversation(
            session=session,
            source=step.audio.audio_source(self._call_id),
            sink=step.audio.audio_sink(self._call_id),
            transcript=_HeardTranscript(self._post),
            metrics=self._metrics,
            clock=self._clock,
        )
        self._task = asyncio.get_running_loop().create_task(self._converse(conversation))

    async def tell(self, situation: Situation, bound: timedelta) -> None:
        """Tell a running assistant what has changed. A failure to is logged, not raised."""
        if self._session is None or not self.is_speaking:
            return
        session, preferences = self._session
        try:
            async with asyncio.timeout(bound.total_seconds()):
                await session.update_context(self._context(preferences, situation))
        # The assistant keeps talking on what it knew; the call is not worth ending for this.
        except Exception as error:  # noqa: BLE001
            log_failure(logger, "call.context_update_failed", error)

    async def stop(self) -> None:
        """End the conversation and close the session. Safe to call again, and never raises."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        opened, self._session = self._session, None
        if opened is None:
            return
        try:
            await opened[0].close()
        # A session that fails as it closes is closed as far as the call is concerned: nothing
        # more will be sent on it, and whatever stops it — a teardown, the assistant gone — must
        # go on past it rather than leave the call half ended. Logged by kind and counted.
        except Exception as error:  # noqa: BLE001
            log_failure(logger, "call.speech_close_failed", error)
            self._metrics.increment(SPEECH_CLOSE_FAILED, {"kind": classify(error).kind})

    async def _converse(self, conversation: Conversation) -> None:
        try:
            end = await conversation.run()
        # Every way a conversation can fail ends the same way for the call — the assistant is gone
        # — so each is one input rather than an exception nobody is awaiting.
        except Exception as error:  # noqa: BLE001
            log_failure(logger, "call.conversation_failed", error)
            self._post(ConversationStopped(None))
        else:
            self._post(ConversationStopped(end))

    def _context(self, preferences: UserPreferences, situation: Situation) -> str:
        return load_prompts(preferences.locale).conversation_context(
            build_preference_context(preferences, now=self._clock.now()),
            preferences.authority,
            situation=situation.as_data(),
        )
