"""What a handset sends about its own calls, and what it is told back."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Final

from pydantic import AwareDatetime, Field, PlainValidator, TypeAdapter, ValidationError

from letmehandle.api.schemas import Request, Response
from letmehandle.domain.ports.call_transport import ScreeningDecision
from letmehandle.domain.ports.reported_calls import CallEnding

# The most reports one request carries.
MAX_REPORTS_PER_REQUEST = 100

# A handset's identifier, bounded so its user-scoped form fits the stored columns.
_IDENTIFIER_PATTERN: Final = r"^[A-Za-z0-9-]+$"
_IDENTIFIER = Annotated[str, Field(min_length=8, max_length=48, pattern=_IDENTIFIER_PATTERN)]

# E.164 as the handset writes it, with ASCII digits only.
E164_PATTERN: Final = r"^\+[1-9][0-9]{1,14}$"


class ReportedCallKind(StrEnum):
    """What a handset can observe about its own call. Nothing joins or leaves one visibly."""

    INCOMING = "incoming"
    ANSWERED = "answered"
    ENDED = "ended"


_EVENT_ID: Final = TypeAdapter(_IDENTIFIER)


class CallReportPayload(Request):
    event_id: _IDENTIFIER
    call_id: _IDENTIFIER
    kind: ReportedCallKind
    occurred_at: AwareDatetime
    # Absent when withheld, or known only in a national form.
    caller_number: Annotated[str, Field(pattern=E164_PATTERN)] | None = None
    screening: ScreeningDecision | None = None
    ending: CallEnding | None = None


@dataclass(frozen=True, slots=True)
class UnreadableReport:
    """A report in a batch that could not be read, kept so the handset can be told which."""

    event_id: str | None
    reason: str


def _read_report(value: object) -> CallReportPayload | UnreadableReport:
    """One report, or why it cannot be one — never a failure of the batch it arrived in."""
    try:
        return CallReportPayload.model_validate(value)
    except ValidationError as error:
        return UnreadableReport(event_id=_event_id_of(value), reason=_reason(error))


def _event_id_of(value: object) -> str | None:
    """The handset's identifier for a report, when it is one a handset could have sent."""
    if not isinstance(value, dict):
        return None
    event_id = value.get("event_id")
    try:
        return _EVENT_ID.validate_python(event_id)
    except ValidationError:
        return None


def _reason(error: ValidationError) -> str:
    """Which fields were wrong and how, never repeating the input."""
    return "; ".join(
        f"{'.'.join(str(part) for part in each['loc'])}: {each['msg']}"
        for each in error.errors(include_input=False, include_url=False)
    )


class CallReportBatch(Request):
    # Each report is read on its own, so one unreadable report is rejected alone.
    reports: Annotated[
        # `object` keeps one shape in the schema; `readings` gives the validated type.
        list[
            Annotated[
                object, PlainValidator(_read_report, json_schema_input_type=CallReportPayload)
            ]
        ],
        Field(min_length=1, max_length=MAX_REPORTS_PER_REQUEST),
    ]

    def readings(self) -> list[CallReportPayload | UnreadableReport]:
        """Each report as it was read: a report to store, or why it could not be one."""
        return [
            each for each in self.reports if isinstance(each, (CallReportPayload, UnreadableReport))
        ]


class RejectedReport(Response):
    """A report that was not stored, and will not be however often it is sent."""

    # Its zero-based place in the batch.
    index: int
    # The handset's event identifier, when readable.
    event_id: str | None
    reason: str


class CallReportReceipt(Response):
    """What became of each report in a batch; the handset forgets every one listed."""

    accepted: list[str]
    duplicates: list[str]
    rejected: list[RejectedReport]
