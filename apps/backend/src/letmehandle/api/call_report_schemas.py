"""What a handset sends about its own calls, and what it is told back."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import AwareDatetime, Field, field_validator

from letmehandle.api.schemas import Request, Response
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.ports.call_transport import ScreeningDecision
from letmehandle.domain.ports.reported_calls import CallEnding

# A handset holds what it could not send and sends it together. Bounded so that one request is
# one reasonable transaction; a handset with more sends more than one.
MAX_REPORTS_PER_REQUEST = 100

# The identifiers a handset chooses. Shaped like the UUIDs the app generates, and bounded so the
# user-scoped form still fits the columns it is stored in.
_IDENTIFIER = Annotated[str, Field(min_length=8, max_length=48, pattern=r"^[A-Za-z0-9-]+$")]


class ReportedCallKind(StrEnum):
    """What a handset can observe about its own call. Nothing joins or leaves one visibly."""

    INCOMING = "incoming"
    ANSWERED = "answered"
    ENDED = "ended"


class CallReportPayload(Request):
    event_id: _IDENTIFIER
    call_id: _IDENTIFIER
    kind: ReportedCallKind
    occurred_at: AwareDatetime
    # Absent when the caller withheld it, or when the handset only had a national form that
    # cannot be written in E.164 without guessing the country.
    caller_number: Annotated[str, Field(min_length=5, max_length=20)] | None = None
    screening: ScreeningDecision | None = None
    ending: CallEnding | None = None

    @field_validator("caller_number")
    @classmethod
    def _must_be_a_number(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return PhoneNumber.parse(value).value
        except InvariantError as error:
            raise ValueError(str(error)) from error


class CallReportBatch(Request):
    reports: Annotated[
        list[CallReportPayload], Field(min_length=1, max_length=MAX_REPORTS_PER_REQUEST)
    ]


class CallReportReceipt(Response):
    """Which reports were stored now, and which had been already. The handset forgets both."""

    accepted: list[str]
    duplicates: list[str]
