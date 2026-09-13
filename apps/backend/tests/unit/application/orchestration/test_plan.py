"""A call's plan holds exactly the steps its transport can take, and no others."""

from __future__ import annotations

import pytest

from letmehandle.application.orchestration.plan import DialTheUser, LetItRing, plan_for
from letmehandle.domain.models.identifiers import CallId, EventId
from letmehandle.domain.ports.call_transport import (
    CallEvent,
    CallEventKind,
    ScreeningDecision,
    TransportCapabilities,
)
from tests.contracts.fakes import ScreeningOnlyTransport, StreamingTransport
from tests.support.orchestration import an_assistance

ASSISTANCE = an_assistance()


def incoming(screening: ScreeningDecision | None = None) -> CallEvent:
    return CallEvent(CallEventKind.INCOMING, CallId("call"), EventId("in"), screening=screening)


class ConversationOnlyTransport(StreamingTransport):
    """Answers and carries audio, and cannot add anybody to a call."""

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(
            can_answer_under_program_control=True,
            can_stream_call_audio_to_ai=True,
            can_inject_ai_audio=True,
        )


class ListeningOnlyTransport(StreamingTransport):
    """Carries audio both ways but cannot take a call itself."""

    @property
    def capabilities(self) -> TransportCapabilities:
        return TransportCapabilities(
            can_stream_call_audio_to_ai=True, can_inject_ai_audio=True, can_bridge_human=True
        )


def test_a_streaming_transport_offers_every_step() -> None:
    transport = StreamingTransport()
    plan = plan_for(transport, incoming(), ASSISTANCE)
    assert plan.assistant is not None
    assert plan.assistant.answering is transport
    assert plan.assistant.audio is transport
    assert plan.assistant.assistance is ASSISTANCE
    assert plan.put_through == DialTheUser(transport)
    assert plan.escalation == DialTheUser(transport)
    assert plan.screened is None


@pytest.mark.parametrize("decision", [None, *ScreeningDecision])
def test_a_screening_transport_records_the_decision_and_offers_no_assistant(
    decision: ScreeningDecision | None,
) -> None:
    plan = plan_for(ScreeningOnlyTransport(), incoming(decision), ASSISTANCE)
    assert plan.assistant is None
    assert plan.escalation is None
    assert plan.put_through == LetItRing()
    # A handset that reported no decision let the call ring.
    assert plan.screened is (decision or ScreeningDecision.ALLOW)


def test_without_bridging_there_is_an_assistant_and_no_escalation_or_dial() -> None:
    plan = plan_for(ConversationOnlyTransport(), incoming(), ASSISTANCE)
    assert plan.assistant is not None
    assert plan.escalation is None
    assert plan.put_through is None


def test_audio_without_answering_is_no_assistant_and_so_no_escalation() -> None:
    transport = ListeningOnlyTransport()
    plan = plan_for(transport, incoming(), ASSISTANCE)
    assert plan.assistant is None
    assert plan.escalation is None
    assert plan.put_through == DialTheUser(transport)


def test_without_the_means_to_speak_there_is_no_assistant_and_no_escalation() -> None:
    transport = StreamingTransport()
    plan = plan_for(transport, incoming(), None)
    assert plan.assistant is None
    assert plan.escalation is None
    assert plan.put_through == DialTheUser(transport)
