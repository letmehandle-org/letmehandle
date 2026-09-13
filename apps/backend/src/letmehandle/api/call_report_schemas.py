"""What a handset sends about its own calls, and what it is told back."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Final

from pydantic import AwareDatetime, Field, PlainValidator, TypeAdapter, ValidationError

from letmehandle.api.schemas import Request, Response
from letmehandle.domain.ports.call_transport import ScreeningDecision
from letmehandle.domain.ports.reported_calls import CallEnding

# A handset holds what it could not send and sends it together. Bounded so that one request is
# one reasonable transaction; a handset with more sends more than one.
MAX_REPORTS_PER_REQUEST = 100

# The identifiers a handset chooses. Shaped like the UUIDs the app generates, and bounded so the
# user-scoped form still fits the columns it is stored in.
_IDENTIFIER_PATTERN: Final = r"^[A-Za-z0-9-]+$"
_IDENTIFIER = Annotated[str, Field(min_length=8, max_length=48, pattern=_IDENTIFIER_PATTERN)]

# E.164 exactly as the handset writes it: a plus, a country code that does not start with zero,
# and at most fifteen digits. The same rule on both sides, so the handset never sends a number it
# believes is valid and has it refused here. Digits are spelled out because `\d` also matches
# digits from other scripts, which no telephone network routes.
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
    # Absent when the caller withheld it, or when the handset only had a national form that
    # cannot be written in E.164 without guessing the country.
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
    """Which fields were wrong and how. Built from locations and messages, never from the input,
    which can be somebody's phone number."""
    return "; ".join(
        f"{'.'.join(str(part) for part in each['loc'])}: {each['msg']}"
        for each in error.errors(include_input=False, include_url=False)
    )


class CallReportBatch(Request):
    # Each report is read on its own, so one a handset got wrong is rejected by itself and the
    # rest are stored. A handset resends a batch it has not seen acknowledged, and refusing the
    # whole batch for one report would resend the good ones with it forever. The schema still
    # describes a report, which is what a client is generated from.
    reports: Annotated[
        # Typed as `object` here only so the schema has no second shape in it: what the
        # validator produces is `CallReportPayload | UnreadableReport`, and `readings` says so.
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

    # Its place in the batch, counting from zero: the one thing every report has.
    index: int
    # The handset's event identifier, when the report carried one that could be read.
    event_id: str | None
    reason: str


class CallReportReceipt(Response):
    """What became of each report in a batch. The handset forgets every one it is told about.

    `accepted` were stored now and `duplicates` had been already; `rejected` never will be,
    because something in them cannot have happened or cannot be read.
    """

    accepted: list[str]
    duplicates: list[str]
    rejected: list[RejectedReport]
