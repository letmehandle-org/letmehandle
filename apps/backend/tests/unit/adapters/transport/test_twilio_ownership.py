"""Whose call a streaming call is: the user whose own number forwarded it."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from letmehandle.adapters.transport.twilio.callbacks import IncomingCall
from letmehandle.adapters.transport.twilio.ownership import ForwardedCallOwnership
from letmehandle.adapters.transport.twilio.signature import SignatureVerifier
from letmehandle.adapters.transport.twilio.transport import TwilioCallTransport, TwilioConfig
from letmehandle.domain.models.identifiers import CallId, EventId, UserId
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.ports.call_transport import CallEvent, CallEventKind
from tests.unit.adapters.transport.test_twilio_transport import RecordingApi

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

OUR_NUMBER = PhoneNumber.parse("+12025550100")
USERS_OWN = PhoneNumber.parse("+12025550143")
OWNER = UserId("user-1")


@pytest.fixture
async def transport() -> AsyncIterator[TwilioCallTransport]:
    transport = TwilioCallTransport(
        config=TwilioConfig(account_id="account", app_id="app", numbers=(OUR_NUMBER,)),
        api=RecordingApi(),
        verifier=SignatureVerifier(auth_token="token", public_base_url="https://calls.example.com"),
    )
    yield transport
    await transport.close()


async def find(number: PhoneNumber) -> UserId | None:
    return OWNER if number == USERS_OWN else None


def arrived(call: str) -> CallEvent:
    return CallEvent(CallEventKind.INCOMING, CallId(call), EventId(f"{call}:incoming"))


def call_arriving(transport: TwilioCallTransport, sid: str, forwarded_from: str | None) -> None:
    transport.incoming_call(
        IncomingCall(
            call_sid=sid,
            account_sid="account",
            caller="+12025550123",
            called=OUR_NUMBER.value,
            forwarded_from=forwarded_from,
        )
    )


async def test_a_call_forwarded_from_a_user_s_number_is_theirs(
    transport: TwilioCallTransport,
) -> None:
    call_arriving(transport, "CAsim-1", USERS_OWN.value)
    assert await ForwardedCallOwnership(transport, find).owner_of(arrived("CAsim-1")) == OWNER


@pytest.mark.parametrize("forwarded_from", [None, "+12025550199", "not a number"])
async def test_a_call_from_no_user_s_line_is_nobody_s(
    transport: TwilioCallTransport, forwarded_from: str | None
) -> None:
    # Dialled straight at the account's number, or forwarded from a line nobody signed up with.
    call_arriving(transport, "CAsim-1", forwarded_from)
    assert await ForwardedCallOwnership(transport, find).owner_of(arrived("CAsim-1")) is None


async def test_a_call_already_gone_is_nobody_s(transport: TwilioCallTransport) -> None:
    assert await ForwardedCallOwnership(transport, find).owner_of(arrived("CAsim-gone")) is None


async def test_a_deployment_may_name_whose_unforwarded_calls_are(
    transport: TwilioCallTransport,
) -> None:
    # The development setting names whose directly dialled calls are.
    call_arriving(transport, "CAsim-1", None)
    ownership = ForwardedCallOwnership(transport, find, unforwarded_line=USERS_OWN)
    assert await ownership.owner_of(arrived("CAsim-1")) == OWNER


@pytest.mark.parametrize("forwarded_from", ["+12025550199", "not a number"])
async def test_a_forwarded_call_is_still_the_forwarding_line_s(
    transport: TwilioCallTransport, forwarded_from: str
) -> None:
    call_arriving(transport, "CAsim-1", forwarded_from)
    ownership = ForwardedCallOwnership(transport, find, unforwarded_line=USERS_OWN)
    assert await ownership.owner_of(arrived("CAsim-1")) is None
