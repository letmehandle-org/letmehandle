"""What orchestration needs from outside itself.

`CallOwnership` says whose call an arriving call is. That is how the call reached the product, which
D-004 makes the transport's business: a handset reports on behalf of the account it signed in as,
and a telephony number is reached through the user's own line forwarding to it. So each transport's
side answers, chosen in bootstrap, and orchestration asks without knowing which answered (D-033).
"""

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
    from letmehandle.domain.ports.call_transport import CallEvent
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
    """The agent that judges calls, and how to let go of what it remembers about one.

    Together because they are built together: every judgement shares one escalation service, and
    only whoever built it can hand out its `forget`.
    """

    agent: CallAgent
    forget: Callable[[CallId], None]


@dataclass(frozen=True, slots=True)
class AssistantServices:
    """What an assistant needs to hold calls: a speech service, voices, and an agent to judge.

    `judging` builds the agent from the actions orchestration implements, which exist only once
    the orchestrator does.
    """

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
    """How long anything a call waits on may take. Each expiry is a transition, never an escape.

    `ring` is how long the user's phone rings before the assistant takes the call back. `judgement`
    bounds one look at the call by the agent, `speech_open` the speech service opening, `provider`
    one request to the transport, and `storage` one unit of work. `speaker_gone` is how long a
    conversation whose audio stopped waits for the transport to say the call ended. `summary` is how
    long teardown waits for a summary to be written before writing the facts' own; it is shorter
    than `shutdown`, which is how long stopping waits for every call to be torn down. `duration` is
    how long a call may last at all: a transport whose report of a call ending is lost, as a
    handset's is when the app is killed or offline, would otherwise leave the call held for as long
    as the process runs.
    """

    ring: timedelta = timedelta(seconds=30)
    judgement: timedelta = timedelta(seconds=20)
    speech_open: timedelta = timedelta(seconds=10)
    provider: timedelta = timedelta(seconds=10)
    storage: timedelta = timedelta(seconds=5)
    speaker_gone: timedelta = timedelta(seconds=5)
    summary: timedelta = timedelta(seconds=10)
    shutdown: timedelta = timedelta(seconds=15)
    duration: timedelta = timedelta(hours=4)

    def __post_init__(self) -> None:
        for bound in fields(self):
            if getattr(self, bound.name) <= timedelta(0):
                raise InvariantError(f"a {bound.name} bound with no time in it never succeeds")
