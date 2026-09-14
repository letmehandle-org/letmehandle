"""What orchestration needs from outside itself (D-033)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, fields
from datetime import timedelta
from typing import TYPE_CHECKING

from letmehandle.domain.errors import InvariantError

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractAsyncContextManager

    from letmehandle.application.agent.ports import CallActions, CallAgent
    from letmehandle.domain.models.identifiers import CallId, UserId
    from letmehandle.domain.ports.call_transport import CallEvent, CallTransport
    from letmehandle.domain.ports.repositories import (
        CallRepository,
        CallTimelineRepository,
        PreferencesRepository,
        SummaryRepository,
        TranscriptRepository,
        UserRepository,
    )
    from letmehandle.domain.ports.speech import SpeechProvider
    from letmehandle.domain.ports.voice import VoiceProvider


class CallOwnership(ABC):
    """Whose call has just arrived."""

    @abstractmethod
    async def owner_of(self, incoming: CallEvent) -> UserId | None:
        """The user the call is for, or None when it is nobody's this deployment serves."""


@dataclass(frozen=True, slots=True)
class CallLine:
    """A transport calls arrive on, with the ownership that reads that transport's calls."""

    transport: CallTransport
    ownership: CallOwnership


@dataclass(frozen=True, slots=True)
class CallStores:
    """The storage one unit of work gives orchestration."""

    users: UserRepository
    preferences: PreferencesRepository
    calls: CallRepository
    transcripts: TranscriptRepository
    summaries: SummaryRepository
    timeline: CallTimelineRepository


type OpenCallStores = Callable[[], AbstractAsyncContextManager[CallStores]]


@dataclass(frozen=True, slots=True)
class CallJudging:
    """The agent that judges calls, and how to let go of what it remembers about one."""

    agent: CallAgent
    forget: Callable[[CallId], None]


@dataclass(frozen=True, slots=True)
class AssistantServices:
    """What an assistant speaks with, and how to build its agent from orchestration's actions."""

    speech: SpeechProvider
    voices: VoiceProvider
    judging: Callable[[CallActions], CallJudging]


@dataclass(frozen=True, slots=True)
class Assistance:
    """The same, with the agent built: what one assistant step on a call's plan speaks with."""

    speech: SpeechProvider
    voices: VoiceProvider
    judging: CallJudging


@dataclass(frozen=True, slots=True)
class Bounds:
    """How long each thing a call waits on may take; every expiry is a transition (D-029)."""

    # How long the user's phone rings before the assistant takes the call back.
    ring: timedelta = timedelta(seconds=30)
    judgement: timedelta = timedelta(seconds=20)
    speech_open: timedelta = timedelta(seconds=10)
    # One request to the transport, or one context update to the speech session.
    provider: timedelta = timedelta(seconds=10)
    # One unit of work in storage.
    storage: timedelta = timedelta(seconds=5)
    # How long the transport has to report a hang-up once the assistant's audio has gone.
    speaker_gone: timedelta = timedelta(seconds=5)
    # How long an ending the agent asked for waits for the assistant to finish speaking.
    goodbye: timedelta = timedelta(seconds=10)
    # How long the assistant must be quiet to have finished speaking.
    goodbye_pause: timedelta = timedelta(seconds=1)
    # How long teardown waits for a written summary, shorter than `shutdown`.
    summary: timedelta = timedelta(seconds=10)
    # How long stopping waits for every call to be torn down.
    shutdown: timedelta = timedelta(seconds=15)
    # The longest a call may last, for a transport whose report of its ending is lost.
    duration: timedelta = timedelta(hours=4)

    def __post_init__(self) -> None:
        for bound in fields(self):
            if getattr(self, bound.name) <= timedelta(0):
                raise InvariantError(f"a {bound.name} bound with no time in it never succeeds")
