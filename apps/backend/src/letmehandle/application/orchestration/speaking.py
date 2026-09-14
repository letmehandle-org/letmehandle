"""The assistant's voice on one call: its speech session and the conversation over it."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from letmehandle.application.agent.prompts import load_prompts
from letmehandle.application.orchestration.inputs import ConversationStopped, Heard
from letmehandle.application.orchestration.metrics import PROVIDER_FAILED, SPEECH_OPEN_SECONDS
from letmehandle.application.preferences.context import DEFAULT_LOCALE, build_preference_context
from letmehandle.application.resilience.circuit import Dependency
from letmehandle.application.resilience.timing import Stopwatch
from letmehandle.application.speech.conversation import (
    Conversation,
    Transcript,
    TranscriptTurn,
)
from letmehandle.domain.failures import FailureKind, classify
from letmehandle.domain.models.timeline import MarkKind
from letmehandle.domain.ports.voice import resolve_voice
from letmehandle.observability import catalogue
from letmehandle.observability.logging import get_logger, log_failure

if TYPE_CHECKING:
    from collections.abc import Callable

    from letmehandle.application.orchestration.inputs import Input
    from letmehandle.application.orchestration.plan import Converse
    from letmehandle.application.orchestration.run import RunContext
    from letmehandle.domain.models.identifiers import CallId
    from letmehandle.domain.models.preferences import UserPreferences
    from letmehandle.domain.ports.call_transport import ParticipantOutcome
    from letmehandle.domain.ports.speech import SpeechCapabilities, SpeechSession

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
    """A transcript that posts each settled line to the run as it is recorded."""

    def __init__(self, post: Callable[[Input], None]) -> None:
        super().__init__()
        self._post = post

    def record(self, turn: TranscriptTurn) -> None:
        super().record(turn)
        self._post(Heard(turn))


class Speaking:
    """One call's speech session and the conversation carried over it, owned by the call's run."""

    def __init__(
        self,
        call_id: CallId,
        context: RunContext,
        *,
        post: Callable[[Input], None],
        note: Callable[[MarkKind, str], None],
    ) -> None:
        self._call_id = call_id
        self._context = context
        self._post = post
        self._note = note
        self._session: tuple[SpeechSession, UserPreferences] | None = None
        self._task: asyncio.Task[None] | None = None
        self._conversation: Conversation | None = None

    @property
    def is_speaking(self) -> bool:
        """Whether a conversation is still running."""
        return self._task is not None and not self._task.done()

    async def open(self, step: Converse, preferences: UserPreferences) -> bool:
        """Open the session through the speech circuit and start the conversation; say if it did."""
        context = self._context
        stopwatch = Stopwatch()
        try:
            with context.tracer.span("speech.open", dependency=Dependency.SPEECH.value):
                await context.circuits[Dependency.SPEECH].call(
                    lambda: self._start(step, preferences)
                )
        # A speech service that does not open in time fails the call: logged and counted by kind.
        except Exception as error:  # noqa: BLE001
            log_failure(logger, "call.speech_unavailable", error)
            kind = classify(error).kind
            context.metrics.increment(PROVIDER_FAILED, {"stage": "speech", "kind": kind})
            self._note(MarkKind.FAILURE, f"speech.{kind}")
            context.metrics.observe(SPEECH_OPEN_SECONDS, stopwatch.seconds, {"outcome": "failed"})
            return False
        context.metrics.observe(SPEECH_OPEN_SECONDS, stopwatch.seconds, {"outcome": "opened"})
        return True

    async def finish_speaking(self) -> None:
        """Wait, within the goodbye bound, until the assistant is quiet for the goodbye pause."""
        conversation = self._conversation
        if conversation is None or not self.is_speaking:
            return
        bounds = self._context.bounds
        # A reply still playing when the bound runs out is cut off.
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(bounds.goodbye.total_seconds()):
                await conversation.quiet(bounds.goodbye_pause.total_seconds())

    async def tell(self, situation: Situation) -> None:
        """Tell a running assistant what has changed, within the provider bound; never raises."""
        if self._session is None or not self.is_speaking:
            return
        session, preferences = self._session
        try:
            async with asyncio.timeout(self._context.bounds.provider.total_seconds()):
                await session.update_context(self._system_context(preferences, situation))
        # The assistant keeps talking on what it knew: logged, not raised.
        except Exception as error:  # noqa: BLE001
            log_failure(logger, "call.context_update_failed", error)

    async def stop(self) -> None:
        """End the conversation and close the session. Safe to call again, and never raises."""
        task, self._task = self._task, None
        self._conversation = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        opened, self._session = self._session, None
        if opened is None:
            return
        try:
            await opened[0].close()
        # A session that fails as it closes counts as closed: logged and counted by kind.
        except Exception as error:  # noqa: BLE001
            log_failure(logger, "call.speech_close_failed", error)
            self._context.metrics.increment(SPEECH_CLOSE_FAILED, {"kind": classify(error).kind})

    async def _start(self, step: Converse, preferences: UserPreferences) -> None:
        assistance = step.assistance
        context = self._context
        locale = _opening_locale(assistance.speech.capabilities, preferences.locale)
        async with asyncio.timeout(context.bounds.speech_open.total_seconds()):
            voice = await resolve_voice(assistance.voices, preferences.voice, locale=locale)
            session = await assistance.speech.connect(
                system_context=self._system_context(preferences, Situation()),
                voice_id=voice,
                greeting=load_prompts(locale).greeting(),
                locale=locale,
                input_format=step.audio.audio_format(),
            )
        self._session = (session, preferences)
        conversation = Conversation(
            session=session,
            source=step.audio.audio_source(self._call_id),
            sink=step.audio.audio_sink(self._call_id),
            transcript=_HeardTranscript(self._post),
            metrics=context.metrics,
            clock=context.clock,
        )
        self._conversation = conversation
        self._task = asyncio.get_running_loop().create_task(self._converse(conversation))

    async def _converse(self, conversation: Conversation) -> None:
        try:
            end = await conversation.run()
        # Every failure of a conversation reaches the run as one input: the assistant is gone.
        except Exception as error:  # noqa: BLE001
            log_failure(logger, "call.conversation_failed", error)
            self._post(ConversationStopped(None))
        else:
            self._post(ConversationStopped(end))

    def _system_context(self, preferences: UserPreferences, situation: Situation) -> str:
        return load_prompts(preferences.locale).conversation_context(
            build_preference_context(preferences, now=self._context.clock.now()),
            preferences.authority,
            situation=situation.as_data(),
        )


def _opening_locale(capabilities: SpeechCapabilities, locale: str) -> str:
    """The user's language where the speech service speaks it, and the default otherwise (D-039)."""
    return locale if capabilities.speaks(locale) else DEFAULT_LOCALE
