"""The provider's routes: nothing is read until it is proved, and repeats change nothing."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.websockets import WebSocketDisconnect, WebSocketState

from letmehandle.adapters.transport.twilio.routes import (
    CALLBACK_REPEATED,
    TELEPHONY_BODY_LIMIT_BYTES,
    StarletteMediaSocket,
    build_router,
)
from letmehandle.adapters.transport.twilio.signature import SignatureVerifier, compute_signature
from letmehandle.adapters.transport.twilio.stream import MediaSocketClosedError
from letmehandle.adapters.transport.twilio.transport import TwilioCallTransport, TwilioConfig
from letmehandle.domain.models.identifiers import CallId
from letmehandle.domain.models.phone_number import PhoneNumber
from tests.support.recording_metrics import RecordingMetrics
from tests.support.recording_tracer import RecordingTracer
from tests.unit.adapters.transport.test_twilio_transport import RecordingApi, drain

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Coroutine

    from httpx import Response

ACCOUNT = "account-for-tests"
TOKEN = "token-for-tests"
BASE = "https://calls.example.com"


@pytest.fixture
async def transport() -> AsyncIterator[TwilioCallTransport]:
    transport = TwilioCallTransport(
        config=TwilioConfig(
            account_id=ACCOUNT, app_id="app", numbers=(PhoneNumber.parse("+12025550100"),)
        ),
        api=RecordingApi(),
        verifier=SignatureVerifier(auth_token=TOKEN, public_base_url=BASE),
    )
    yield transport
    await transport.close()


@pytest.fixture
async def client(transport: TwilioCallTransport) -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(
        build_router(transport, tracer=RecordingTracer(), metrics=RecordingMetrics())
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as c:
        yield c


def signed_post(
    client: AsyncClient,
    path: str,
    params: list[tuple[str, str]],
    *,
    token: str = TOKEN,
    idempotency: str | None = None,
) -> Coroutine[Any, Any, Response]:
    headers = {"X-Twilio-Signature": compute_signature(BASE + path, params, token)}
    if idempotency is not None:
        headers["I-Twilio-Idempotency-Token"] = idempotency
    return client.post(
        path,
        content=urlencode(params),
        headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
    )


ARRIVAL = [
    ("AccountSid", ACCOUNT),
    ("CallSid", "CAsim-1"),
    ("From", "+12025550123"),
    ("To", "+12025550100"),
]


async def test_a_signed_arrival_is_answered_with_instructions(
    client: AsyncClient, transport: TwilioCallTransport
) -> None:
    response = await signed_post(client, "/telephony/voice/incoming", ARRIVAL)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert "<Conference" in response.text
    assert transport.active_calls == 1


async def test_a_redelivered_arrival_still_gets_its_instructions(client: AsyncClient) -> None:
    first = await signed_post(client, "/telephony/voice/incoming", ARRIVAL, idempotency="t-1")
    again = await signed_post(client, "/telephony/voice/incoming", ARRIVAL, idempotency="t-1")
    assert again.status_code == 200
    assert again.text == first.text


@pytest.mark.parametrize(
    "path",
    [
        "/telephony/voice/incoming",
        "/telephony/voice/assistant",
        "/telephony/voice/caller-left?call=CAsim-1",
        "/telephony/conference/status?call=CAsim-1",
        "/telephony/leg/status?call=CAsim-1&leg=user-2",
    ],
)
async def test_every_route_refuses_what_it_cannot_prove(
    client: AsyncClient, transport: TwilioCallTransport, path: str
) -> None:
    forged = await signed_post(client, path, ARRIVAL, token="not-the-token")
    unsigned = await client.post(path, data=dict(ARRIVAL))
    assert forged.status_code == 403
    assert unsigned.status_code == 403
    assert transport.active_calls == 0


async def oversized_chunks(size: int) -> AsyncIterator[bytes]:
    for _ in range(size // 4_096 + 1):
        yield b"a" * 4_096


@pytest.mark.parametrize(
    "path",
    [
        "/telephony/voice/incoming",
        "/telephony/voice/assistant",
        "/telephony/voice/caller-left?call=CAsim-1",
        "/telephony/conference/status?call=CAsim-1",
        "/telephony/leg/status?call=CAsim-1&leg=user-2",
    ],
)
async def test_a_body_too_large_for_a_callback_is_refused_before_it_is_read(
    client: AsyncClient, path: str
) -> None:
    # Unsigned, so it is refused either way; the point is that it is refused for its size, and
    # without sixty-four megabytes being held in memory to find out it was forged.
    size = TELEPHONY_BODY_LIMIT_BYTES + 1
    declared = await client.post(path, content=b"a" * size)
    streamed = await client.post(path, content=oversized_chunks(size))
    assert (declared.status_code, streamed.status_code) == (413, 413)


async def test_a_genuine_signature_from_another_account_is_refused(
    client: AsyncClient, transport: TwilioCallTransport
) -> None:
    params = [("AccountSid", "somebody-else"), *ARRIVAL[1:]]
    response = await signed_post(client, "/telephony/voice/incoming", params)
    assert response.status_code == 403
    assert transport.active_calls == 0


async def test_a_genuine_callback_missing_what_is_needed_is_unprocessable(
    client: AsyncClient,
) -> None:
    response = await signed_post(client, "/telephony/voice/incoming", [("AccountSid", ACCOUNT)])
    assert response.status_code == 422


async def test_the_callers_dial_ending_is_answered_by_hanging_up_and_ends_the_call(
    client: AsyncClient, transport: TwilioCallTransport
) -> None:
    await signed_post(client, "/telephony/voice/incoming", ARRIVAL)
    await drain(transport)
    response = await signed_post(
        client,
        "/telephony/voice/caller-left?call=CAsim-1",
        [("AccountSid", ACCOUNT), ("CallSid", "CAsim-1"), ("DialCallStatus", "completed")],
    )
    assert response.status_code == 200
    assert "<Hangup" in response.text
    assert [event.kind.value for event in await drain(transport)] == ["ended"]


async def test_the_assistant_route_reads_the_call_from_the_form_or_the_query(
    client: AsyncClient, transport: TwilioCallTransport
) -> None:
    await signed_post(client, "/telephony/voice/incoming", ARRIVAL)
    await transport.answer(CallId("CAsim-1"))
    form = [
        ("AccountSid", ACCOUNT),
        ("CallSid", "CAsim-x"),
        ("call", "CAsim-1"),
        ("leg", "assistant-1"),
    ]
    response = await signed_post(client, "/telephony/voice/assistant", form)
    assert "<Stream" in response.text
    query = await signed_post(
        client,
        "/telephony/voice/assistant?call=CAsim-1&leg=assistant-1",
        [("AccountSid", ACCOUNT), ("CallSid", "CAsim-y")],
    )
    assert "<Stream" in query.text


async def test_status_callbacks_are_applied_once_per_delivery_token(
    client: AsyncClient, transport: TwilioCallTransport
) -> None:
    await signed_post(client, "/telephony/voice/incoming", ARRIVAL)
    await drain(transport)
    join = [
        ("AccountSid", ACCOUNT),
        ("ConferenceSid", "CFsim-1"),
        ("StatusCallbackEvent", "participant-join"),
        ("SequenceNumber", "1"),
        ("ParticipantLabel", "caller"),
        ("CallSid", "CAsim-1"),
    ]
    path = "/telephony/conference/status?call=CAsim-1"
    first = await signed_post(client, path, join, idempotency="t-9")
    again = await signed_post(client, path, join, idempotency="t-9")
    assert (first.status_code, again.status_code) == (204, 204)
    assert [event.kind.value for event in await drain(transport)] == ["answered"]
    progress = [
        ("AccountSid", ACCOUNT),
        ("CallSid", "CAsim-user"),
        ("CallStatus", "busy"),
        ("SequenceNumber", "2"),
    ]
    leg = await signed_post(client, "/telephony/leg/status?call=CAsim-1&leg=user-2", progress)
    assert leg.status_code == 204


async def test_a_callback_repeating_a_parameter_it_is_read_for_is_unprocessable(
    client: AsyncClient, transport: TwilioCallTransport
) -> None:
    # A repeated parameter is signed the same whichever order its values arrive in, so the
    # order cannot be allowed to decide which of them is meant.
    await signed_post(client, "/telephony/voice/incoming", ARRIVAL)
    await drain(transport)
    ambiguous = [
        ("AccountSid", ACCOUNT),
        ("ConferenceSid", "CFsim-1"),
        ("StatusCallbackEvent", "participant-join"),
        ("StatusCallbackEvent", "participant-leave"),
        ("SequenceNumber", "1"),
        ("ParticipantLabel", "caller"),
    ]
    response = await signed_post(client, "/telephony/conference/status?call=CAsim-1", ambiguous)
    assert response.status_code == 422
    assert await drain(transport) == []
    assert transport.active_calls == 1


# ----------------------------------------------------------- the websocket, as a socket


class FakeWebSocket:
    def __init__(self, *, messages: list[Any] | None = None) -> None:
        self.application_state = WebSocketState.CONNECTED
        self.client_state = WebSocketState.CONNECTED
        self.messages = messages or []
        self.sent: list[str] = []
        self.closes = 0
        self.fail_with: BaseException | None = None

    async def receive(self) -> Any:
        if self.fail_with is not None:
            raise self.fail_with
        return self.messages.pop(0)

    async def send_text(self, text: str) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append(text)

    async def close(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.closes += 1


def socket_over(fake: FakeWebSocket) -> StarletteMediaSocket:
    return StarletteMediaSocket(fake)  # type: ignore[arg-type]


async def test_text_frames_are_read_and_a_disconnect_is_the_end() -> None:
    fake = FakeWebSocket(
        messages=[
            {"type": "websocket.receive", "text": "hello"},
            {"type": "websocket.receive", "bytes": b"\x00"},
            {"type": "websocket.disconnect", "code": 1000},
        ]
    )
    socket = socket_over(fake)
    assert await socket.receive() == "hello"
    # Binary is not this protocol; it reads as text the parser will refuse.
    assert await socket.receive() == ""
    assert await socket.receive() is None


async def test_reading_a_socket_this_side_has_closed_is_the_end() -> None:
    fake = FakeWebSocket()
    fake.application_state = WebSocketState.DISCONNECTED
    assert await socket_over(fake).receive() is None
    racing = FakeWebSocket()
    racing.fail_with = RuntimeError("closed")
    assert await socket_over(racing).receive() is None


async def test_sending_on_a_socket_that_has_gone_is_a_typed_failure() -> None:
    fake = FakeWebSocket()
    socket = socket_over(fake)
    await socket.send("a")
    assert fake.sent == ["a"]
    for failure in (RuntimeError("closed"), WebSocketDisconnect(1006), OSError("reset")):
        fake.fail_with = failure
        with pytest.raises(MediaSocketClosedError):
            await socket.send("b")
    fake.application_state = WebSocketState.DISCONNECTED
    with pytest.raises(MediaSocketClosedError):
        await socket.send("c")


async def test_closing_is_safe_whoever_closed_first() -> None:
    fake = FakeWebSocket()
    socket = socket_over(fake)
    await socket.close()
    assert fake.closes == 1
    fake.client_state = WebSocketState.DISCONNECTED
    await socket.close()
    assert fake.closes == 1
    gone = FakeWebSocket()
    gone.application_state = WebSocketState.DISCONNECTED
    await socket_over(gone).close()
    racing = FakeWebSocket()
    racing.fail_with = RuntimeError("already closed")
    await socket_over(racing).close()


async def test_each_callback_is_a_span_naming_its_route_and_call_and_a_repeat_is_counted(
    transport: TwilioCallTransport,
) -> None:
    tracer = RecordingTracer()
    metrics = RecordingMetrics()
    app = FastAPI()
    app.include_router(build_router(transport, tracer=tracer, metrics=metrics))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://127.0.0.1") as http:
        await signed_post(http, "/telephony/voice/incoming", ARRIVAL, idempotency="t-1")
        await signed_post(http, "/telephony/voice/incoming", ARRIVAL, idempotency="t-1")
        status = [
            ("AccountSid", ACCOUNT),
            ("ConferenceSid", "CFsim-1"),
            ("StatusCallbackEvent", "conference-start"),
            ("SequenceNumber", "1"),
        ]
        path = "/telephony/conference/status?call=CAsim-1"
        await signed_post(http, path, status, idempotency="t-2")
        repeated = await signed_post(http, path, status, idempotency="t-2")
        forged = await signed_post(http, path, status, token="not-the-token")

    assert repeated.status_code == 204
    assert forged.status_code == 403
    assert [span.attributes for span in tracer.named("telephony.callback")] == [
        {"stage": "incoming", "call.id": "CAsim-1"},
        {"stage": "incoming", "call.id": "CAsim-1"},
        {"stage": "conference", "call.id": "CAsim-1"},
        {"stage": "conference", "call.id": "CAsim-1", "outcome": "repeated"},
        {"stage": "conference", "outcome": "refused"},
    ]
    assert metrics.counted(CALLBACK_REPEATED, stage="incoming") == 1
    assert metrics.counted(CALLBACK_REPEATED, stage="conference") == 1
