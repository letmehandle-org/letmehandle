"""The real websocket connection, against a simulated service on a real socket.

No database and no account: the service runs in this process on loopback. What these prove is
the part the session above cannot see — that the handshake carries what it should, and that
every way a websocket stops working arrives as one of two typed failures carrying the one fact
reconnection needs.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import pytest
from websockets.asyncio.server import ServerConnection, serve

from letmehandle.adapters.speech.realtime.websocket import websocket_opener
from letmehandle.adapters.speech.websocket.connection import (
    ConnectionClosedError,
    ConnectionFailedError,
)
from letmehandle.adapters.speech.websocket.socket import CLOSE_TIMEOUT_SECONDS
from letmehandle.observability.logging import configure_logging
from tests.support.config import make_settings
from tests.support.simulated_realtime_service import (
    MALFORMED_FRAME,
    SIMULATED_API_KEY,
    SIMULATED_MODEL,
    SIMULATED_TRANSCRIPT,
    SimulatedRealtimeService,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping
    from typing import Any

    from letmehandle.adapters.speech.websocket.connection import (
        ConnectionOpener,
        EventConnection,
    )


@pytest.fixture
async def service() -> AsyncIterator[SimulatedRealtimeService]:
    """A running service, and proof afterwards that neither side left anything running."""
    before = len(asyncio.all_tasks())
    async with SimulatedRealtimeService() as running:
        yield running
    assert running.open_connections == 0
    assert len(asyncio.all_tasks()) == before


def _opener(service: SimulatedRealtimeService, **overrides: Any) -> ConnectionOpener:
    options: dict[str, Any] = {"model": SIMULATED_MODEL, "api_key": SIMULATED_API_KEY}
    options.update(overrides)
    return websocket_opener(service.url, **options)


async def _connected(service: SimulatedRealtimeService) -> EventConnection:
    """A connection the service has greeted, so both ends agree it is open."""
    connection = await _opener(service)()
    greeting = await connection.receive()
    assert greeting is not None
    assert greeting["type"] == "session.created"
    return connection


async def _next_of(connection: EventConnection, kind: str) -> Mapping[str, Any]:
    while True:
        event = await connection.receive()
        assert event is not None, f"the connection closed before {kind}"
        if event["type"] == kind:
            return event


def _audio(payload: bytes) -> dict[str, str]:
    return {"type": "input_audio_buffer.append", "audio": base64.b64encode(payload).decode()}


async def test_the_key_travels_as_a_bearer_header_and_the_model_as_a_query_parameter(
    service: SimulatedRealtimeService,
) -> None:
    connection = await _connected(service)
    await connection.close()

    [handshake] = service.handshakes
    assert handshake.authorization == f"Bearer {SIMULATED_API_KEY}"
    assert handshake.model == SIMULATED_MODEL


async def test_no_key_sends_no_authorization_header_at_all(
    service: SimulatedRealtimeService,
) -> None:
    # A self-run server may need no key, and an empty bearer is a header it might reject.
    with pytest.raises(ConnectionFailedError):
        await _opener(service, api_key=None)()
    assert service.handshakes[0].authorization is None


async def test_a_model_already_in_the_endpoint_is_replaced_rather_than_duplicated(
    service: SimulatedRealtimeService,
) -> None:
    opener = websocket_opener(
        f"{service.url}?model=stale&region=test", model=SIMULATED_MODEL, api_key=SIMULATED_API_KEY
    )
    connection = await opener()
    await connection.close()
    assert service.handshakes[0].model == SIMULATED_MODEL


async def test_a_protocol_conversation_round_trips_as_json_objects(
    service: SimulatedRealtimeService,
) -> None:
    connection = await _connected(service)

    await connection.send({"type": "session.update", "session": {"instructions": "be brief"}})
    updated = await _next_of(connection, "session.updated")
    assert updated["session"]["instructions"] == "be brief"

    speech = b"\x01\x02" * 480
    await connection.send(_audio(speech))
    await _next_of(connection, "input_audio_buffer.speech_started")
    await connection.send(_audio(b"\x00" * 960))
    transcribed = await _next_of(
        connection, "conversation.item.input_audio_transcription.completed"
    )
    assert transcribed["transcript"] == SIMULATED_TRANSCRIPT
    delta = await _next_of(connection, "response.output_audio.delta")
    assert base64.b64decode(delta["delta"]) == speech
    await _next_of(connection, "response.done")

    await connection.close()


async def test_a_refused_key_is_not_worth_retrying(service: SimulatedRealtimeService) -> None:
    service.refuse_authentication()
    with pytest.raises(ConnectionFailedError, match="HTTP 401") as raised:
        await _opener(service)()
    assert raised.value.retryable is False


async def test_an_unknown_model_is_not_worth_retrying(service: SimulatedRealtimeService) -> None:
    with pytest.raises(ConnectionFailedError) as raised:
        await _opener(service, model="no-such-model")()
    assert raised.value.retryable is False


async def test_the_key_never_appears_in_a_failure(service: SimulatedRealtimeService) -> None:
    service.refuse_authentication()
    with pytest.raises(ConnectionFailedError) as raised:
        await _opener(service)()
    assert SIMULATED_API_KEY not in str(raised.value)
    assert raised.value.__cause__ is None


async def test_an_unreachable_service_is_worth_retrying(
    service: SimulatedRealtimeService,
) -> None:
    # Port 1 is reserved and nothing in a test environment listens on it.
    opener = websocket_opener("ws://127.0.0.1:1/v1/realtime", model=SIMULATED_MODEL)
    with pytest.raises(ConnectionFailedError) as raised:
        await opener()
    assert raised.value.retryable is True


async def test_an_endpoint_that_is_not_a_websocket_url_is_not_worth_retrying() -> None:
    opener = websocket_opener("https://speech.example.com/v1/realtime", model=SIMULATED_MODEL)
    with pytest.raises(ConnectionFailedError) as raised:
        await opener()
    assert raised.value.retryable is False


async def test_an_abrupt_drop_mid_stream_is_worth_retrying(
    service: SimulatedRealtimeService,
) -> None:
    connection = await _connected(service)
    service.drop_connections()

    with pytest.raises(ConnectionFailedError) as raised:
        await connection.receive()
    assert raised.value.retryable is True
    await connection.close()


async def test_sending_into_a_dropped_connection_is_worth_retrying(
    service: SimulatedRealtimeService,
) -> None:
    connection = await _connected(service)
    service.drop_connections()
    with pytest.raises(ConnectionFailedError):
        await connection.receive()

    with pytest.raises(ConnectionFailedError) as raised:
        await connection.send({"type": "response.cancel"})
    assert raised.value.retryable is True
    await connection.close()


async def test_a_normal_close_by_the_service_ends_the_stream_with_none(
    service: SimulatedRealtimeService,
) -> None:
    connection = await _connected(service)
    await service.close_connections()

    assert await connection.receive() is None
    await connection.close()


async def test_sending_after_the_service_closed_normally_is_a_closed_error(
    service: SimulatedRealtimeService,
) -> None:
    connection = await _connected(service)
    await service.close_connections()
    assert await connection.receive() is None

    with pytest.raises(ConnectionClosedError):
        await connection.send({"type": "response.cancel"})
    await connection.close()


async def test_using_a_connection_after_closing_it_is_a_closed_error(
    service: SimulatedRealtimeService,
) -> None:
    connection = await _connected(service)
    await connection.close()

    with pytest.raises(ConnectionClosedError):
        await connection.send({"type": "response.cancel"})
    with pytest.raises(ConnectionClosedError):
        await connection.receive()


async def test_closing_twice_is_safe(service: SimulatedRealtimeService) -> None:
    connection = await _connected(service)
    await connection.close()
    await connection.close()


@pytest.mark.parametrize(
    ("frame", "reason"),
    [
        (MALFORMED_FRAME, "not JSON"),
        ('["an", "array"]', "not a JSON object"),
        (b"\x00\x01", "binary frame"),
    ],
)
async def test_a_malformed_frame_is_a_typed_failure_and_not_a_crash(
    service: SimulatedRealtimeService, frame: str | bytes, reason: str
) -> None:
    connection = await _connected(service)
    await service.send_malformed_frame(frame)

    with pytest.raises(ConnectionFailedError, match=reason) as raised:
        await connection.receive()
    # The same server will send the same thing again; reconnecting to it is not a remedy.
    assert raised.value.retryable is False
    await connection.close()


@asynccontextmanager
async def _not_a_websocket_server(reply: bytes | None) -> AsyncIterator[str]:
    """A bare TCP server that answers a handshake wrongly, or never.

    `None` holds the connection open and says nothing; anything else is written back and the
    connection closed. The simulated service cannot misbehave at this level, because the library
    it is built on will not let it.
    """
    held: list[asyncio.StreamWriter] = []

    async def answer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        if reply is None:
            held.append(writer)
            return
        writer.write(reply)
        writer.close()

    server = await asyncio.start_server(answer, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"ws://127.0.0.1:{port}/v1/realtime"
    finally:
        for writer in held:
            writer.close()
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize(
    ("reply", "retryable"),
    [
        # Gone mid-handshake, the way a restarting service is.
        (b"", True),
        # A server fault is its condition, not ours.
        (b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n", True),
        # Something is listening, and it is not a websocket server.
        (b"HTTP/1.1 101 Switching Protocols\r\nContent-Length: 0\r\n\r\n", False),
    ],
)
async def test_a_broken_handshake_says_whether_it_is_worth_retrying(
    reply: bytes, retryable: bool
) -> None:
    async with _not_a_websocket_server(reply) as url:
        with pytest.raises(ConnectionFailedError) as raised:
            await websocket_opener(url, model=SIMULATED_MODEL)()
    assert raised.value.retryable is retryable


async def test_a_handshake_that_never_completes_is_worth_retrying() -> None:
    async with _not_a_websocket_server(None) as url:
        opener = websocket_opener(url, model=SIMULATED_MODEL, open_timeout=0.1)
        with pytest.raises(ConnectionFailedError, match="in time") as raised:
            await opener()
    assert raised.value.retryable is True


async def test_closing_leaves_nothing_running_on_either_side(
    service: SimulatedRealtimeService,
) -> None:
    before = len(asyncio.all_tasks())
    connections = [await _connected(service) for _ in range(3)]
    assert service.open_connections == 3
    assert len(asyncio.all_tasks()) > before

    for connection in connections:
        await connection.close()
    # Bounded, because a connection that was never really closed would otherwise hang the suite
    # rather than fail it.
    async with asyncio.timeout(5):
        await service.wait_until_idle()

    assert service.open_connections == 0
    assert len(asyncio.all_tasks()) == before


async def test_debug_logging_never_prints_the_key_or_what_was_said(
    service: SimulatedRealtimeService,
) -> None:
    # At debug the websocket library logs request headers and frame text: the key, and whatever
    # a caller said. Debugging is exactly when a log gets pasted somewhere it should not go.
    captured = io.StringIO()
    configure_logging(make_settings(log_level="debug"))
    handler = logging.StreamHandler(captured)
    logging.getLogger().addHandler(handler)
    try:
        connection = await _connected(service)
        await connection.send({"type": "session.update", "session": {"instructions": "a secret"}})
        await connection.receive()
        await connection.close()
    finally:
        logging.getLogger().removeHandler(handler)
        configure_logging(make_settings())

    assert SIMULATED_API_KEY not in captured.getvalue()
    assert "a secret" not in captured.getvalue()


async def test_closing_is_prompt_when_the_service_has_stopped_reading() -> None:
    # A service that stops reading fills the socket's buffers until a send cannot finish. Closing
    # must still return promptly: it is what a cancelled call and a quitting harness wait on.
    async def stops_reading(connection: ServerConnection) -> None:
        await connection.recv()
        await asyncio.sleep(60)

    async with serve(stops_reading, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        connection = await websocket_opener(f"ws://127.0.0.1:{port}/", model=SIMULATED_MODEL)()
        await connection.send({"type": "session.update"})

        async def flood() -> None:
            with contextlib.suppress(ConnectionClosedError, ConnectionFailedError):
                while True:
                    await connection.send(
                        {"type": "input_audio_buffer.append", "audio": "A" * 65_536}
                    )

        sending = asyncio.create_task(flood())
        await asyncio.sleep(0.5)
        assert not sending.done(), "the send should be stuck behind a service that stopped reading"

        async with asyncio.timeout(CLOSE_TIMEOUT_SECONDS + 3):
            await connection.close()
        sending.cancel()
        await asyncio.gather(sending, return_exceptions=True)
