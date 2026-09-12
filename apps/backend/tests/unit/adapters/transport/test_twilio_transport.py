"""The streaming transport's call handling, callback by callback, against a recording API.

Callbacks are handed to the transport directly, in whatever order and multiplicity a test
chooses, so every mapping from what the provider says to what the orchestrator hears is pinned
here. The simulator's end-to-end tests prove the same behaviour over real HTTP and websockets.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from letmehandle.adapters.transport.twilio import transport as transport_module
from letmehandle.adapters.transport.twilio.callbacks import (
    ConferenceEvent,
    ConferenceUpdate,
    IncomingCall,
    LegProgress,
    LegStatus,
)
from letmehandle.adapters.transport.twilio.signature import SignatureVerifier
from letmehandle.adapters.transport.twilio.transport import (
    TwilioCallTransport,
    TwilioConfig,
    _Remembered,
)
from letmehandle.domain.errors import IllegalTransitionError, InvariantError, ProviderError
from letmehandle.domain.models.audio import TELEPHONY_NARROWBAND, AudioFrame
from letmehandle.domain.models.identifiers import CallId
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.ports.call_transport import (
    AssistantPresence,
    CallEvent,
    CallEventKind,
)
from tests.support.media_socket import MemoryMediaSocket

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from letmehandle.adapters.transport.twilio.rest import EndStatus, ParticipantRequest

CALL = CallId("CAsim-1")
OUR_NUMBER = PhoneNumber.parse("+12025550100")
OTHER_NUMBER = PhoneNumber.parse("+12025550101")
USER = PhoneNumber.parse("+12025550143")
CONFERENCE = "CFsim-1"


class RecordingApi:
    """Answers every request, remembers it, and fails when told to."""

    def __init__(self) -> None:
        self.created: list[tuple[str, ParticipantRequest]] = []
        self.updates: list[tuple[str, str, bool, str | None]] = []
        self.removed: list[tuple[str, str]] = []
        self.ended_conferences: list[str] = []
        self.ended_calls: list[tuple[str, EndStatus]] = []
        self.failure: ProviderError | None = None
        self.closed = 0

    def _maybe_fail(self) -> None:
        if self.failure is not None:
            failure, self.failure = self.failure, None
            raise failure

    async def create_participant(self, conference_name: str, request: ParticipantRequest) -> str:
        self._maybe_fail()
        self.created.append((conference_name, request))
        return f"CAsim-{request.label}"

    async def update_participant(
        self, conference_sid: str, call_sid: str, *, muted: bool, coach_call_sid: str | None
    ) -> None:
        self._maybe_fail()
        self.updates.append((conference_sid, call_sid, muted, coach_call_sid))

    async def remove_participant(self, conference_sid: str, call_sid: str) -> bool:
        self._maybe_fail()
        self.removed.append((conference_sid, call_sid))
        return True

    async def end_conference(self, conference_sid: str) -> bool:
        self._maybe_fail()
        self.ended_conferences.append(conference_sid)
        return True

    async def end_call(self, call_sid: str, status: EndStatus) -> bool:
        self._maybe_fail()
        self.ended_calls.append((call_sid, status))
        return True

    async def close(self) -> None:
        self.closed += 1


FAILURE = ProviderError("twilio", "refused", retryable=False)


@pytest.fixture(autouse=True)
def _short_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transport_module, "LATE_CALLBACK_GRACE_SECONDS", 0.01)


@pytest.fixture
async def api() -> RecordingApi:
    return RecordingApi()


@pytest.fixture
async def transport(api: RecordingApi) -> AsyncIterator[TwilioCallTransport]:
    transport = TwilioCallTransport(
        config=TwilioConfig(
            account_id="account-for-tests", app_id="app-for-tests", numbers=(OUR_NUMBER,)
        ),
        api=api,
        verifier=SignatureVerifier(
            auth_token="token-for-tests", public_base_url="https://calls.example.com"
        ),
    )
    yield transport
    await transport.close()
    assert transport.pending_tasks == 0
    assert transport.open_media_sockets == 0


def incoming(sid: str = CALL.value, caller: str | None = "+12025550123") -> IncomingCall:
    return IncomingCall(call_sid=sid, account_sid="a", caller=caller, called=OUR_NUMBER.value)


def conference(
    event: ConferenceEvent | None,
    sequence: int,
    label: str | None = None,
    *,
    call_sid: str | None = None,
    sid: str = CONFERENCE,
    reason: str | None = None,
) -> ConferenceUpdate:
    return ConferenceUpdate(
        conference_sid=sid,
        account_sid="a",
        event=event,
        sequence=sequence,
        call_sid=call_sid if call_sid is not None else (f"CAsim-{label}" if label else None),
        label=label,
        reason=reason,
    )


def progress(
    label: str, status: LegStatus | None, sequence: int | None, answered_by: str | None = None
) -> LegProgress:
    return LegProgress(
        call_sid=f"CAsim-{label}",
        account_sid="a",
        status=status,
        sequence=sequence,
        answered_by=answered_by,
    )


async def drain(transport: TwilioCallTransport) -> list[CallEvent]:
    await transport.settled()
    events: list[CallEvent] = []
    stream = transport.events()
    while True:
        try:
            events.append(await asyncio.wait_for(anext(stream), 0.01))
        except TimeoutError:
            return events


def shapes(events: list[CallEvent]) -> list[tuple[str, str | None, str | None]]:
    return [
        (
            event.kind.value,
            event.participant.value if event.participant else None,
            event.outcome.value if event.outcome else None,
        )
        for event in events
    ]


async def answered_call(transport: TwilioCallTransport) -> None:
    """A call in its conference with the assistant present, events already read."""
    transport.incoming_call(incoming())
    transport.conference_updated(CALL.value, conference(ConferenceEvent.JOIN, 1, "caller"))
    await transport.answer(CALL)
    transport.conference_updated(CALL.value, conference(ConferenceEvent.JOIN, 2, "assistant-1"))
    await drain(transport)


async def with_user(transport: TwilioCallTransport, sequence: int = 3) -> None:
    await transport.add_participant(CALL, USER)
    transport.leg_progressed(CALL.value, "user-2", progress("user-2", LegStatus.IN_PROGRESS, 2))
    transport.conference_updated(CALL.value, conference(ConferenceEvent.JOIN, sequence, "user-2"))
    await drain(transport)


# ------------------------------------------------------------------------------ identity


async def test_identity_and_declared_capabilities(transport: TwilioCallTransport) -> None:
    assert transport.name == "twilio"
    assert transport.audio_format() == TELEPHONY_NARROWBAND
    assert transport.account_id == "account-for-tests"
    assert transport.capabilities.supports_agent_conversation
    assert transport.capabilities.supports_three_way_call
    assert not transport.capabilities.can_screen_before_ringing


def test_a_transport_with_no_number_to_call_from_is_refused() -> None:
    with pytest.raises(InvariantError, match="number"):
        TwilioConfig(account_id="a", app_id="b", numbers=())


def test_remembering_is_bounded_and_forgets_the_oldest_first() -> None:
    remembered = _Remembered(2)
    assert remembered.add("a")
    assert not remembered.add("a")
    remembered.add("b")
    remembered.add("c")
    assert "a" not in remembered
    assert "c" in remembered


# ------------------------------------------------------------------------ an arrival


async def test_an_arriving_call_is_answered_into_its_own_conference(
    transport: TwilioCallTransport,
) -> None:
    document = transport.incoming_call(incoming())
    assert "<Conference" in document
    assert "call-CAsim-1" in document
    assert "https://calls.example.com/telephony/conference/status?call=CAsim-1" in document
    [event] = await drain(transport)
    assert event.kind is CallEventKind.INCOMING
    assert event.call_id == CALL
    assert event.caller is not None
    assert event.caller.number == PhoneNumber.parse("+12025550123")
    assert transport.active_calls == 1


async def test_a_redelivered_arrival_gets_the_same_answer_and_no_second_event(
    transport: TwilioCallTransport,
) -> None:
    first = transport.incoming_call(incoming())
    assert transport.incoming_call(incoming()) == first
    assert len(await drain(transport)) == 1


@pytest.mark.parametrize("caller", [None, "anonymous", "266696687"])
async def test_a_withheld_or_unreadable_number_is_an_anonymous_caller(
    transport: TwilioCallTransport, caller: str | None
) -> None:
    transport.incoming_call(incoming(caller=caller))
    [event] = await drain(transport)
    assert event.caller is not None
    assert event.caller.is_anonymous


async def test_the_user_is_dialled_from_the_number_the_caller_dialled_when_it_is_ours(
    api: RecordingApi,
) -> None:
    transport = TwilioCallTransport(
        config=TwilioConfig(account_id="a", app_id="b", numbers=(OTHER_NUMBER, OUR_NUMBER)),
        api=api,
        verifier=SignatureVerifier(auth_token="t", public_base_url="https://calls.example.com"),
    )
    transport.incoming_call(incoming())
    transport.incoming_call(
        IncomingCall(call_sid="CAsim-2", account_sid="a", caller=None, called="+12025550199")
    )
    await transport.add_participant(CALL, USER)
    await transport.add_participant(CallId("CAsim-2"), USER)
    assert [request.from_ for _, request in api.created] == [OUR_NUMBER.value, OTHER_NUMBER.value]
    await transport.close()


async def test_a_call_that_is_over_is_not_started_again_by_a_late_redelivery(
    transport: TwilioCallTransport,
) -> None:
    transport.incoming_call(incoming())
    await transport.terminate(CALL)
    assert "<Hangup" in transport.incoming_call(incoming())
    assert transport.active_calls == 0


async def test_the_caller_joining_is_the_call_being_answered_once(
    transport: TwilioCallTransport,
) -> None:
    transport.incoming_call(incoming())
    transport.conference_updated(CALL.value, conference(ConferenceEvent.JOIN, 1, "caller"))
    transport.conference_updated(CALL.value, conference(ConferenceEvent.START, 2))
    transport.conference_updated(CALL.value, conference(None, 3))
    transport.conference_updated(CALL.value, conference(ConferenceEvent.JOIN, 4, "caller"))
    assert [event.kind for event in await drain(transport)] == [
        CallEventKind.INCOMING,
        CallEventKind.ANSWERED,
    ]


# ------------------------------------------------------------------------ the assistant


async def test_answering_dials_the_assistant_application_once(
    transport: TwilioCallTransport, api: RecordingApi
) -> None:
    transport.incoming_call(incoming())
    await transport.answer(CALL)
    await transport.answer(CALL)
    [(name, request)] = api.created
    assert name == "call-CAsim-1"
    assert request.to == "app:app-for-tests?call=CAsim-1&leg=assistant-1"
    assert request.label == "assistant-1"
    assert not request.detect_machine
    assert request.status_callback_url.endswith(
        "/telephony/leg/status?call=CAsim-1&leg=assistant-1"
    )


async def test_answering_a_call_that_is_not_here_is_refused(
    transport: TwilioCallTransport,
) -> None:
    with pytest.raises(ProviderError, match="no such call") as failure:
        await transport.answer(CallId("CAsim-unknown"))
    assert not failure.value.retryable


async def test_a_failed_dial_of_the_assistant_raises_and_can_be_tried_again(
    transport: TwilioCallTransport, api: RecordingApi
) -> None:
    transport.incoming_call(incoming())
    api.failure = FAILURE
    with pytest.raises(ProviderError):
        await transport.answer(CALL)
    await transport.answer(CALL)
    assert [request.label for _, request in api.created] == ["assistant-2"]


async def test_the_assistant_leg_is_told_to_stream_when_it_is_the_one_expected(
    transport: TwilioCallTransport,
) -> None:
    transport.incoming_call(incoming())
    await transport.answer(CALL)
    document = transport.assistant_joining(
        {"call": CALL.value, "leg": "assistant-1"}, "CAsim-assistant-1"
    )
    assert '<Stream url="wss://calls.example.com/telephony/media">' in document
    assert '<Parameter name="leg" value="assistant-1" />' in document


async def test_the_assistant_leg_is_recognised_by_its_call_when_its_parameters_are_missing(
    transport: TwilioCallTransport,
) -> None:
    transport.incoming_call(incoming())
    await transport.answer(CALL)
    assert "<Stream" in transport.assistant_joining({}, "CAsim-assistant-1")
    assert "<Stream" in transport.assistant_joining({"call": "CAsim-gone"}, "CAsim-assistant-1")


async def test_a_leg_nobody_expects_is_hung_up(transport: TwilioCallTransport) -> None:
    transport.incoming_call(incoming())
    await transport.add_participant(CALL, USER)
    assert "<Hangup" in transport.assistant_joining({}, "CAsim-nobody")
    assert "<Hangup" in transport.assistant_joining(
        {"call": CALL.value, "leg": "user-1"}, "CAsim-user-1"
    )


async def test_the_assistant_joining_and_leaving_are_reported_as_the_assistant(
    transport: TwilioCallTransport,
) -> None:
    await answered_call(transport)
    transport.conference_updated(CALL.value, conference(ConferenceEvent.LEAVE, 3, "assistant-1"))
    assert shapes(await drain(transport)) == [("participant_left", "assistant", None)]
    # After leaving, answering brings a new assistant rather than doing nothing.
    await transport.answer(CALL)
    assert transport.assistant_joining({"call": CALL.value, "leg": "assistant-2"}, "x") != (
        transport.assistant_joining({}, "nobody")
    )


async def test_a_dial_that_never_reaches_the_assistant_is_reported(
    transport: TwilioCallTransport,
) -> None:
    transport.incoming_call(incoming())
    await transport.answer(CALL)
    await drain(transport)
    transport.leg_progressed(
        CALL.value, "assistant-1", progress("assistant-1", LegStatus.FAILED, 1)
    )
    assert shapes(await drain(transport)) == [("participant_unreachable", "assistant", "failed")]


# ------------------------------------------------------------------------------ the user


async def test_the_user_is_dialled_with_voicemail_detection(
    transport: TwilioCallTransport, api: RecordingApi
) -> None:
    await answered_call(transport)
    await transport.add_participant(CALL, USER)
    await transport.add_participant(CALL, USER)
    requests = [request for _, request in api.created if request.label.startswith("user")]
    assert len(requests) == 1
    assert requests[0].to == USER.value
    assert requests[0].detect_machine


async def test_the_user_answering_and_joining_is_one_event(transport: TwilioCallTransport) -> None:
    await answered_call(transport)
    await transport.add_participant(CALL, USER)
    for sequence, status in enumerate((LegStatus.INITIATED, LegStatus.RINGING), 0):
        transport.leg_progressed(CALL.value, "user-2", progress("user-2", status, sequence))
    transport.leg_progressed(
        CALL.value, "user-2", progress("user-2", LegStatus.IN_PROGRESS, 2, "human")
    )
    transport.conference_updated(CALL.value, conference(ConferenceEvent.JOIN, 3, "user-2"))
    transport.conference_updated(CALL.value, conference(ConferenceEvent.JOIN, 4, "user-2"))
    assert shapes(await drain(transport)) == [("participant_joined", "user", "answered")]


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (LegStatus.NO_ANSWER, "no_answer"),
        (LegStatus.BUSY, "busy"),
        (LegStatus.FAILED, "failed"),
        (LegStatus.CANCELED, "failed"),
        (LegStatus.COMPLETED, "failed"),
    ],
)
async def test_a_user_who_never_joins_is_reported_by_how_it_turned_out(
    transport: TwilioCallTransport, status: LegStatus, outcome: str
) -> None:
    await answered_call(transport)
    await transport.add_participant(CALL, USER)
    transport.leg_progressed(CALL.value, "user-2", progress("user-2", LegStatus.RINGING, 1))
    transport.leg_progressed(CALL.value, "user-2", progress("user-2", status, 2))
    assert shapes(await drain(transport)) == [("participant_unreachable", "user", outcome)]
    # Anything later about that leg changes nothing: it has been reported.
    transport.leg_progressed(CALL.value, "user-2", progress("user-2", LegStatus.COMPLETED, 3))
    transport.conference_updated(CALL.value, conference(ConferenceEvent.JOIN, 3, "user-2"))
    assert await drain(transport) == []


async def test_a_voicemail_answering_is_reported_and_hung_up(
    transport: TwilioCallTransport, api: RecordingApi
) -> None:
    await answered_call(transport)
    await transport.add_participant(CALL, USER)
    transport.leg_progressed(
        CALL.value, "user-2", progress("user-2", LegStatus.IN_PROGRESS, 2, "machine_start")
    )
    events = await drain(transport)
    assert shapes(events) == [("participant_unreachable", "user", "answered_by_machine")]
    assert api.ended_calls == [("CAsim-user-2", "completed")]
    # The machine's leg joining and leaving afterwards is not the user coming and going.
    transport.conference_updated(CALL.value, conference(ConferenceEvent.JOIN, 3, "user-2"))
    transport.conference_updated(CALL.value, conference(ConferenceEvent.LEAVE, 4, "user-2"))
    assert await drain(transport) == []


async def test_a_voicemail_reported_only_as_the_leg_ends_is_still_a_voicemail(
    transport: TwilioCallTransport,
) -> None:
    await answered_call(transport)
    await transport.add_participant(CALL, USER)
    transport.leg_progressed(
        CALL.value, "user-2", progress("user-2", LegStatus.COMPLETED, 3, "machine_end_beep")
    )
    assert shapes(await drain(transport)) == [
        ("participant_unreachable", "user", "answered_by_machine")
    ]


async def test_progress_redelivered_or_arriving_late_changes_nothing(
    transport: TwilioCallTransport,
) -> None:
    await answered_call(transport)
    await transport.add_participant(CALL, USER)
    busy = progress("user-2", LegStatus.BUSY, 5)
    transport.leg_progressed(CALL.value, "user-2", progress("user-2", LegStatus.RINGING, 3))
    transport.leg_progressed(CALL.value, "user-2", progress("user-2", LegStatus.RINGING, 3))
    # An earlier update arriving after a later one is stale, whatever it says.
    transport.leg_progressed(CALL.value, "user-2", progress("user-2", LegStatus.INITIATED, 1))
    transport.leg_progressed(CALL.value, "user-2", busy)
    transport.leg_progressed(CALL.value, "user-2", busy)
    transport.leg_progressed(CALL.value, "user-2", progress("user-2", None, 6))
    assert shapes(await drain(transport)) == [("participant_unreachable", "user", "busy")]


async def test_progress_without_sequence_numbers_is_still_applied(
    transport: TwilioCallTransport,
) -> None:
    await answered_call(transport)
    await transport.add_participant(CALL, USER)
    transport.leg_progressed(CALL.value, "user-2", progress("user-2", LegStatus.NO_ANSWER, None))
    assert shapes(await drain(transport)) == [("participant_unreachable", "user", "no_answer")]


async def test_progress_for_a_leg_or_call_nobody_knows_is_ignored(
    transport: TwilioCallTransport,
) -> None:
    await answered_call(transport)
    transport.leg_progressed(CALL.value, "user-9", progress("user-9", LegStatus.BUSY, 1))
    transport.leg_progressed("CAsim-gone", "user-2", progress("user-2", LegStatus.BUSY, 1))
    transport.leg_progressed(None, None, progress("user-2", LegStatus.BUSY, 1))
    assert await drain(transport) == []


async def test_a_completion_arriving_before_the_join_and_leave_it_followed_waits_for_them(
    transport: TwilioCallTransport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(transport_module, "LATE_CALLBACK_GRACE_SECONDS", 0.5)
    await answered_call(transport)
    await transport.add_participant(CALL, USER)
    transport.leg_progressed(CALL.value, "user-2", progress("user-2", LegStatus.COMPLETED, 4))
    transport.conference_updated(CALL.value, conference(ConferenceEvent.LEAVE, 6, "user-2"))
    transport.conference_updated(CALL.value, conference(ConferenceEvent.JOIN, 5, "user-2"))
    # Not unreachable: the user was on the call, and has left it.
    assert shapes(await drain(transport)) == [("participant_left", "user", None)]


async def test_a_completed_progress_after_joining_leaves_the_leaving_to_the_conference(
    transport: TwilioCallTransport,
) -> None:
    await answered_call(transport)
    await with_user(transport)
    transport.leg_progressed(CALL.value, "user-2", progress("user-2", LegStatus.COMPLETED, 9))
    assert await drain(transport) == []
    transport.conference_updated(CALL.value, conference(ConferenceEvent.LEAVE, 5, "user-2"))
    assert shapes(await drain(transport)) == [("participant_left", "user", None)]


async def test_a_leave_arriving_before_its_join_is_resolved_by_sequence(
    transport: TwilioCallTransport,
) -> None:
    await answered_call(transport)
    await transport.add_participant(CALL, USER)
    transport.conference_updated(CALL.value, conference(ConferenceEvent.LEAVE, 6, "user-2"))
    transport.conference_updated(CALL.value, conference(ConferenceEvent.JOIN, 5, "user-2"))
    # The later state wins: the user is not on the call, whatever order that was learned in.
    assert shapes(await drain(transport)) == [("participant_left", "user", None)]


async def test_a_participant_is_recognised_by_its_call_when_the_label_is_missing(
    transport: TwilioCallTransport,
) -> None:
    await answered_call(transport)
    await transport.add_participant(CALL, USER)
    transport.conference_updated(
        CALL.value, conference(ConferenceEvent.JOIN, 3, None, call_sid="CAsim-user-2")
    )
    transport.conference_updated(
        CALL.value, conference(ConferenceEvent.JOIN, 4, None, call_sid="CAsim-nobody")
    )
    assert shapes(await drain(transport)) == [("participant_joined", "user", "answered")]


async def test_removing_a_user_still_ringing_cancels_the_dial_and_reports_nothing(
    transport: TwilioCallTransport, api: RecordingApi
) -> None:
    await answered_call(transport)
    await transport.add_participant(CALL, USER)
    await transport.remove_participant(CALL, USER)
    await transport.remove_participant(CALL, USER)
    assert api.ended_calls == [("CAsim-user-2", "canceled")]
    transport.leg_progressed(CALL.value, "user-2", progress("user-2", LegStatus.CANCELED, 4))
    assert await drain(transport) == []
    # Asked for again, the user is dialled again.
    await transport.add_participant(CALL, USER)
    assert api.created[-1][1].label == "user-3"


async def test_removing_a_user_on_the_call_takes_them_out_of_the_conference(
    transport: TwilioCallTransport, api: RecordingApi
) -> None:
    await answered_call(transport)
    await with_user(transport)
    await transport.remove_participant(CALL, USER)
    assert api.removed == [(CONFERENCE, "CAsim-user-2")]
    await transport.remove_participant(CallId("CAsim-gone"), USER)


async def test_a_dial_whose_identifier_is_not_yet_known_is_only_marked_removed(
    transport: TwilioCallTransport, api: RecordingApi
) -> None:
    await answered_call(transport)
    api.failure = FAILURE
    with pytest.raises(ProviderError):
        await transport.add_participant(CALL, USER)
    assert shapes(await drain(transport)) == []
    await transport.remove_participant(CALL, USER)
    assert api.ended_calls == []


# ---------------------------------------------------------------------- the three of them


async def test_a_presence_chosen_before_the_user_joins_waits_for_them(
    transport: TwilioCallTransport, api: RecordingApi
) -> None:
    await answered_call(transport)
    await transport.set_assistant_presence(CALL, AssistantPresence.LISTEN_ONLY)
    assert api.updates == []
    await with_user(transport)
    assert api.updates == [(CONFERENCE, "CAsim-assistant-1", True, None)]


async def test_each_presence_is_one_change_to_the_assistant(
    transport: TwilioCallTransport, api: RecordingApi
) -> None:
    await answered_call(transport)
    await with_user(transport)
    await transport.set_assistant_presence(CALL, AssistantPresence.SPEAK_TO_USER_ONLY)
    await transport.set_assistant_presence(CALL, AssistantPresence.SPEAK_TO_USER_ONLY)
    await transport.set_assistant_presence(CALL, AssistantPresence.LISTEN_ONLY)
    await transport.set_assistant_presence(CALL, AssistantPresence.STAY)
    assert api.updates == [
        (CONFERENCE, "CAsim-assistant-1", False, "CAsim-user-2"),
        (CONFERENCE, "CAsim-assistant-1", True, None),
        (CONFERENCE, "CAsim-assistant-1", False, None),
    ]


async def test_when_the_last_user_leaves_the_assistant_is_audible_again(
    transport: TwilioCallTransport, api: RecordingApi
) -> None:
    await answered_call(transport)
    await transport.set_assistant_presence(CALL, AssistantPresence.LISTEN_ONLY)
    await with_user(transport)
    transport.conference_updated(CALL.value, conference(ConferenceEvent.LEAVE, 5, "user-2"))
    await drain(transport)
    assert api.updates[-1] == (CONFERENCE, "CAsim-assistant-1", False, None)


async def test_leaving_takes_the_assistant_off_the_call_and_is_final(
    transport: TwilioCallTransport, api: RecordingApi
) -> None:
    await answered_call(transport)
    await with_user(transport)
    await transport.set_assistant_presence(CALL, AssistantPresence.LEAVE)
    assert api.removed == [(CONFERENCE, "CAsim-assistant-1")]
    with pytest.raises(IllegalTransitionError):
        await transport.set_assistant_presence(CALL, AssistantPresence.STAY)
    # A new assistant can be brought back, and with it the choice.
    await transport.answer(CALL)
    await transport.set_assistant_presence(CALL, AssistantPresence.STAY)


async def test_a_presence_that_cannot_be_applied_after_a_callback_is_reported_as_a_failure(
    transport: TwilioCallTransport, api: RecordingApi
) -> None:
    await answered_call(transport)
    await transport.set_assistant_presence(CALL, AssistantPresence.LISTEN_ONLY)
    await transport.add_participant(CALL, USER)
    api.failure = FAILURE
    transport.conference_updated(CALL.value, conference(ConferenceEvent.JOIN, 3, "user-2"))
    events = await drain(transport)
    assert [event.kind for event in events] == [
        CallEventKind.PARTICIPANT_JOINED,
        CallEventKind.FAILED,
    ]
    assert "refused" in (events[1].detail or "")


async def test_a_presence_is_not_applied_before_the_assistant_has_joined(
    transport: TwilioCallTransport, api: RecordingApi
) -> None:
    transport.incoming_call(incoming())
    await transport.set_assistant_presence(CALL, AssistantPresence.LISTEN_ONLY)
    await transport.answer(CALL)
    await transport.set_assistant_presence(CALL, AssistantPresence.LISTEN_ONLY)
    assert api.updates == []


# -------------------------------------------------------------------------- the end of it


async def test_the_conference_ending_ends_the_call_once_and_releases_it(
    transport: TwilioCallTransport, api: RecordingApi
) -> None:
    await answered_call(transport)
    await transport.add_participant(CALL, USER)
    transport.conference_updated(
        CALL.value, conference(ConferenceEvent.END, 7, reason="last-participant-left")
    )
    transport.conference_updated(CALL.value, conference(ConferenceEvent.END, 8))
    events = await drain(transport)
    assert shapes(events) == [("ended", None, None)]
    assert events[0].detail == "last-participant-left"
    # The user still ringing is cancelled: answering would join a conference nobody is in.
    assert api.ended_calls == [("CAsim-user-2", "canceled")]
    assert transport.active_calls == 0


async def test_the_caller_hanging_up_ends_the_call(transport: TwilioCallTransport) -> None:
    await answered_call(transport)
    transport.conference_updated(CALL.value, conference(ConferenceEvent.LEAVE, 5, "caller"))
    transport.conference_updated(CALL.value, conference(ConferenceEvent.END, 6))
    events = await drain(transport)
    assert shapes(events) == [("ended", None, None)]
    assert events[0].detail == "the caller hung up"


async def test_callbacks_for_another_conference_or_no_call_are_ignored(
    transport: TwilioCallTransport,
) -> None:
    await answered_call(transport)
    transport.conference_updated(CALL.value, conference(ConferenceEvent.END, 9, sid="CFsim-other"))
    transport.conference_updated("CAsim-gone", conference(ConferenceEvent.END, 9))
    transport.conference_updated(None, conference(ConferenceEvent.END, 9))
    transport.conference_updated(CALL.value, conference(ConferenceEvent.LEAVE, 10, "user-9"))
    assert await drain(transport) == []
    assert transport.active_calls == 1


async def test_a_redelivered_conference_update_is_applied_once(
    transport: TwilioCallTransport,
) -> None:
    await answered_call(transport)
    transport.conference_updated(CALL.value, conference(ConferenceEvent.LEAVE, 3, "assistant-1"))
    # Same conference, same sequence: the same update, whatever else it seems to say.
    transport.conference_updated(CALL.value, conference(ConferenceEvent.END, 3))
    assert shapes(await drain(transport)) == [("participant_left", "assistant", None)]


async def test_terminating_ends_everything_on_the_providers_side_and_is_idempotent(
    transport: TwilioCallTransport, api: RecordingApi
) -> None:
    await answered_call(transport)
    await transport.add_participant(CALL, USER)
    await transport.terminate(CALL)
    await transport.terminate(CALL)
    assert api.ended_conferences == [CONFERENCE]
    assert api.ended_calls == [(CALL.value, "completed"), ("CAsim-user-2", "canceled")]
    assert shapes(await drain(transport)) == [("ended", None, None)]
    assert transport.active_calls == 0


async def test_terminating_before_the_conference_exists_ends_the_callers_leg(
    transport: TwilioCallTransport, api: RecordingApi
) -> None:
    transport.incoming_call(incoming())
    await transport.terminate(CALL)
    assert api.ended_conferences == []
    assert api.ended_calls == [(CALL.value, "completed")]


async def test_a_terminate_the_provider_refused_can_be_tried_again(
    transport: TwilioCallTransport, api: RecordingApi
) -> None:
    await answered_call(transport)
    api.failure = FAILURE
    with pytest.raises(ProviderError):
        await transport.terminate(CALL)
    assert transport.active_calls == 1
    await transport.terminate(CALL)
    assert transport.active_calls == 0


async def test_operations_on_a_call_that_has_gone(transport: TwilioCallTransport) -> None:
    with pytest.raises(ProviderError):
        transport.audio_source(CALL)
    with pytest.raises(ProviderError):
        await transport.add_participant(CALL, USER)
    with pytest.raises(ProviderError):
        await transport.set_assistant_presence(CALL, AssistantPresence.STAY)


async def test_closing_releases_every_call_and_ends_the_event_stream(
    transport: TwilioCallTransport, api: RecordingApi
) -> None:
    await answered_call(transport)
    events = transport.events()
    await transport.close()
    await transport.close()
    assert transport.active_calls == 0
    assert api.closed == 1
    assert [event.kind async for event in events] == [CallEventKind.ENDED]
    assert "<Hangup" in transport.incoming_call(incoming("CAsim-new"))


async def test_closing_cancels_work_still_in_flight(api: RecordingApi) -> None:
    started = asyncio.Event()

    async def slow_remove(conference_sid: str, call_sid: str) -> bool:
        started.set()
        await asyncio.sleep(10)
        return True

    api.remove_participant = slow_remove  # type: ignore[method-assign]
    transport = TwilioCallTransport(
        config=TwilioConfig(account_id="a", app_id="b", numbers=(OUR_NUMBER,)),
        api=api,
        verifier=SignatureVerifier(auth_token="t", public_base_url="https://calls.example.com"),
    )
    await answered_call(transport)
    await with_user(transport)
    await transport.set_assistant_presence(CALL, AssistantPresence.LISTEN_ONLY)
    transport.leg_progressed(
        CALL.value, "user-2", progress("user-2", LegStatus.IN_PROGRESS, 9, "machine_start")
    )
    # A leave for the assistant starts stream work; a voicemail hang-up starts network work.
    transport.conference_updated(CALL.value, conference(ConferenceEvent.JOIN, 4, "user-3"))
    await transport.remove_participant(CALL, PhoneNumber.parse("+12025550144"))
    task = asyncio.create_task(transport.set_assistant_presence(CALL, AssistantPresence.LEAVE))
    await started.wait()
    transport.conference_updated(CALL.value, conference(ConferenceEvent.END, 11))
    await transport.close()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert transport.pending_tasks == 0


# ------------------------------------------------------------------------------ the media


def start_message(
    leg: str = "assistant-1", call_sid: str = "CAsim-assistant-1"
) -> dict[str, object]:
    return {
        "event": "start",
        "streamSid": "MZsim-1",
        "start": {
            "streamSid": "MZsim-1",
            "callSid": call_sid,
            "tracks": ["inbound"],
            "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
            "customParameters": {"call": CALL.value, "leg": leg},
        },
    }


def media_message(track: str = "inbound") -> dict[str, object]:
    return {
        "event": "media",
        "media": {"track": track, "chunk": "1", "timestamp": "20", "payload": "AAEC"},
    }


async def test_a_media_socket_carries_the_callers_audio_until_the_stream_stops(
    transport: TwilioCallTransport,
) -> None:
    await answered_call(transport)
    socket = MemoryMediaSocket()
    socket.provider_sends({"event": "connected"})
    socket.provider_sends({"event": "mark", "mark": {"name": "m"}})
    socket.provider_sends(start_message())
    socket.provider_sends(media_message())
    socket.provider_sends(media_message("outbound"))
    socket.provider_sends({"event": "dtmf", "dtmf": {"digit": "1"}})
    socket.provider_sends({"event": "stop"})
    running = asyncio.create_task(transport.media_connected(socket))
    frames = [frame.data async for frame in transport.stream_audio(CALL)]
    await running
    assert frames == [b"\x00\x01\x02"]
    # Stopped without the socket closing: this side closes it.
    assert socket.closed
    assert transport.open_media_sockets == 0


async def test_a_socket_closing_without_a_stop_ends_the_stream_too(
    transport: TwilioCallTransport,
) -> None:
    await answered_call(transport)
    socket = MemoryMediaSocket()
    socket.provider_sends(start_message())
    running = asyncio.create_task(transport.media_connected(socket))
    await transport.inject_audio(CALL, AudioFrame(b"\x10" * 160, TELEPHONY_NARROWBAND))
    await transport.audio_sink(CALL).discard()
    socket.provider_closes()
    await running
    assert socket.events_sent() == ["media", "clear"]
    assert [frame async for frame in transport.stream_audio(CALL)] == []


@pytest.mark.parametrize(
    "opening",
    [
        [start_message(leg="assistant-9")],
        [start_message(leg="user-2")],
        [start_message(call_sid="CAsim-impostor")],
        [{"event": "start", "start": {}}],
        [media_message()],
        ["not json"],
        [],
    ],
)
async def test_a_socket_that_is_not_the_expected_stream_is_closed(
    transport: TwilioCallTransport, opening: list[object]
) -> None:
    await answered_call(transport)
    transport.assistant_joining({"call": CALL.value, "leg": "assistant-1"}, "CAsim-assistant-1")
    await transport.add_participant(CALL, USER)
    socket = MemoryMediaSocket()
    for message in opening:
        socket.provider_sends(message)
    socket.provider_closes()
    await transport.media_connected(socket)
    assert socket.closed
    assert transport.open_media_sockets == 0


async def test_a_second_socket_for_a_stream_already_connected_is_refused(
    transport: TwilioCallTransport,
) -> None:
    await answered_call(transport)
    first, second = MemoryMediaSocket(), MemoryMediaSocket()
    first.provider_sends(start_message())
    running = asyncio.create_task(transport.media_connected(first))
    await asyncio.sleep(0.01)
    second.provider_sends(start_message())
    await transport.media_connected(second)
    assert second.closed
    assert not first.closed
    first.provider_sends("garbage")
    await running
    assert first.closed


async def test_the_assistant_leaving_ends_its_stream_and_closes_its_socket(
    transport: TwilioCallTransport,
) -> None:
    await answered_call(transport)
    socket = MemoryMediaSocket()
    socket.provider_sends(start_message())
    running = asyncio.create_task(transport.media_connected(socket))
    await asyncio.sleep(0.01)
    transport.conference_updated(CALL.value, conference(ConferenceEvent.LEAVE, 3, "assistant-1"))
    await running
    assert socket.closed
    with pytest.raises(ProviderError, match="no assistant"):
        await transport.inject_audio(CALL, AudioFrame(b"\x10", TELEPHONY_NARROWBAND))


async def test_the_caller_hanging_up_closes_the_assistants_socket(
    transport: TwilioCallTransport,
) -> None:
    await answered_call(transport)
    socket = MemoryMediaSocket()
    socket.provider_sends(start_message())
    running = asyncio.create_task(transport.media_connected(socket))
    await asyncio.sleep(0.01)
    transport.conference_updated(CALL.value, conference(ConferenceEvent.LEAVE, 3, "caller"))
    await running
    await transport.settled()
    assert socket.closed
    assert transport.active_calls == 0


async def test_a_listening_only_assistant_sends_nothing_to_the_call(
    transport: TwilioCallTransport,
) -> None:
    await answered_call(transport)
    socket = MemoryMediaSocket()
    socket.provider_sends(start_message())
    running = asyncio.create_task(transport.media_connected(socket))
    await with_user(transport)
    await transport.set_assistant_presence(CALL, AssistantPresence.LISTEN_ONLY)
    await transport.inject_audio(CALL, AudioFrame(b"\x10" * 160, TELEPHONY_NARROWBAND))
    assert socket.sent == []
    socket.provider_closes()
    await running


def test_a_repeated_delivery_token_is_recognised() -> None:
    transport = TwilioCallTransport(
        config=TwilioConfig(account_id="a", app_id="b", numbers=(OUR_NUMBER,)),
        api=RecordingApi(),
        verifier=SignatureVerifier(auth_token="t", public_base_url="https://calls.example.com"),
    )
    assert not transport.is_repeat_delivery(None)
    assert not transport.is_repeat_delivery(None)
    assert not transport.is_repeat_delivery("token-1")
    assert transport.is_repeat_delivery("token-1")


async def test_terminating_while_a_dial_is_still_being_placed_marks_it_rather_than_waiting(
    transport: TwilioCallTransport, api: RecordingApi
) -> None:
    await answered_call(transport)
    placing = asyncio.Event()
    finish = asyncio.Event()
    original = api.create_participant

    async def slow_create(conference_name: str, request: ParticipantRequest) -> str:
        placing.set()
        await finish.wait()
        return await original(conference_name, request)

    api.create_participant = slow_create  # type: ignore[method-assign]
    dialling = asyncio.create_task(transport.add_participant(CALL, USER))
    await placing.wait()
    await transport.terminate(CALL)
    # The leg with no identifier yet could not be hung up by one; the caller's leg was.
    assert api.ended_calls == [(CALL.value, "completed")]
    finish.set()
    await dialling


async def test_the_provider_ending_the_call_during_a_terminate_releases_it_once(
    transport: TwilioCallTransport, api: RecordingApi
) -> None:
    await answered_call(transport)
    ending = asyncio.Event()
    finish = asyncio.Event()

    async def slow_end(conference_sid: str) -> bool:
        ending.set()
        await finish.wait()
        return True

    api.end_conference = slow_end  # type: ignore[method-assign]
    terminating = asyncio.create_task(transport.terminate(CALL))
    await ending.wait()
    transport.conference_updated(CALL.value, conference(ConferenceEvent.END, 9))
    await transport.settled()
    finish.set()
    await terminating
    assert shapes(await drain(transport)) == [("ended", None, None)]


async def test_failing_to_cancel_a_ringing_leg_after_the_call_ended_is_logged_not_raised(
    transport: TwilioCallTransport, api: RecordingApi
) -> None:
    await answered_call(transport)
    await transport.add_participant(CALL, USER)
    api.failure = FAILURE
    transport.conference_updated(CALL.value, conference(ConferenceEvent.END, 9))
    # The call is over and released; there is nobody left to tell but the log.
    assert shapes(await drain(transport)) == [("ended", None, None)]
    assert transport.active_calls == 0
