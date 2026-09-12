"""The provider's HTTP callbacks, read into typed values once they are proved genuine.

Only what this transport acts on is read; every other parameter was still signed and is simply
not looked at. A callback missing something this code needs is malformed, and is refused
rather than guessed at.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Sequence

# Query parameters this transport puts on its own callback URLs, so a callback says which call
# and which leg it is about before any provider identifier is known.
CALL_PARAMETER: Final = "call"
LEG_PARAMETER: Final = "leg"
# A stream parameter only: the secret an assistant leg's stream must present to be attached.
TOKEN_PARAMETER: Final = "token"  # noqa: S105 - the name of a parameter, not its value


class CallbackMalformedError(Exception):
    """A genuine callback without something this transport needs. Names the parameter only."""


class ConferenceEvent(StrEnum):
    """The conference events this transport acts on, as the provider names them."""

    START = "conference-start"
    END = "conference-end"
    JOIN = "participant-join"
    LEAVE = "participant-leave"


class LegStatus(StrEnum):
    """A dialled leg's progress, as the provider names it."""

    QUEUED = "queued"
    INITIATED = "initiated"
    RINGING = "ringing"
    IN_PROGRESS = "in-progress"
    COMPLETED = "completed"
    BUSY = "busy"
    NO_ANSWER = "no-answer"
    FAILED = "failed"
    CANCELED = "canceled"

    @property
    def is_final(self) -> bool:
        return self in _FINAL


_FINAL: Final = frozenset(
    {
        LegStatus.COMPLETED,
        LegStatus.BUSY,
        LegStatus.NO_ANSWER,
        LegStatus.FAILED,
        LegStatus.CANCELED,
    }
)


@dataclass(frozen=True, slots=True)
class Parameters:
    """A callback's parameters, looked up by name.

    A parameter this transport reads must appear once. The signature covers a repeated
    parameter's values in sorted order rather than the order they arrived in, so a callback
    carrying two could be reordered in transit without breaking its signature, and whichever
    came first would decide what it meant.
    """

    pairs: Sequence[tuple[str, str]]

    def get(self, name: str) -> str | None:
        values = [value for key, value in self.pairs if key == name]
        if len(values) > 1:
            raise CallbackMalformedError(f"the callback repeats {name}")
        return values[0] if values else None

    def require(self, name: str) -> str:
        value = self.get(name)
        if not value:
            raise CallbackMalformedError(f"the callback carries no {name}")
        return value

    def sequence(self) -> int:
        text = self.require("SequenceNumber")
        if not text.isdigit():
            raise CallbackMalformedError("the callback's SequenceNumber is not a number")
        return int(text)


@dataclass(frozen=True, slots=True)
class IncomingCall:
    """A call arriving at one of the account's numbers."""

    call_sid: str
    account_sid: str
    caller: str | None
    called: str | None


@dataclass(frozen=True, slots=True)
class ConferenceUpdate:
    """Something that happened in a call's conference, in the provider's order."""

    conference_sid: str
    account_sid: str
    event: ConferenceEvent | None
    sequence: int
    call_sid: str | None
    label: str | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class LegProgress:
    """How dialling one leg is going."""

    call_sid: str
    account_sid: str
    status: LegStatus | None
    sequence: int | None
    answered_by: str | None

    @property
    def answered_by_machine(self) -> bool:
        """A voicemail greeting or a fax, which must never pass for the user joining."""
        return self.answered_by is not None and (
            self.answered_by.startswith("machine") or self.answered_by == "fax"
        )


def read_incoming_call(params: Parameters) -> IncomingCall:
    return IncomingCall(
        call_sid=params.require("CallSid"),
        account_sid=params.require("AccountSid"),
        caller=params.get("From") or None,
        called=params.get("To") or None,
    )


def read_conference_update(params: Parameters) -> ConferenceUpdate:
    event = params.require("StatusCallbackEvent")
    return ConferenceUpdate(
        conference_sid=params.require("ConferenceSid"),
        account_sid=params.require("AccountSid"),
        event=ConferenceEvent(event) if event in ConferenceEvent else None,
        sequence=params.sequence(),
        call_sid=params.get("CallSid") or None,
        label=params.get("ParticipantLabel") or None,
        reason=params.get("ReasonConferenceEnded") or None,
    )


def read_leg_progress(params: Parameters) -> LegProgress:
    status = params.require("CallStatus")
    sequence = params.get("SequenceNumber")
    if sequence is not None and not sequence.isdigit():
        raise CallbackMalformedError("the callback's SequenceNumber is not a number")
    return LegProgress(
        call_sid=params.require("CallSid"),
        account_sid=params.require("AccountSid"),
        status=LegStatus(status) if status in LegStatus else None,
        sequence=int(sequence) if sequence is not None else None,
        answered_by=params.get("AnsweredBy") or None,
    )
