"""Reading a connection that may fail, and replacing one that has.

The same for every protocol: a failure is a value the reader acts on rather than an exception it
has to remember to catch, and a replacement is found by waiting, trying, and giving up within the
policy's bounds. What a new connection has to be told once it is open is each protocol's own
business, and is whatever `open_connection` does before it returns.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from letmehandle.adapters.speech.session_support.telemetry import StreamErrorKind
from letmehandle.adapters.speech.websocket.connection import (
    ConnectionClosedError,
    ConnectionFailedError,
    EventConnectionError,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from letmehandle.adapters.speech.session_support.reconnect import (
        ReconnectBudget,
        ReconnectPolicy,
    )
    from letmehandle.adapters.speech.session_support.telemetry import SessionTelemetry
    from letmehandle.adapters.speech.session_support.timing import Timekeeping
    from letmehandle.adapters.speech.websocket.connection import EventConnection


async def receive(connection: EventConnection) -> Mapping[str, Any] | EventConnectionError:
    """The next event, or the failure that ended the connection, as a value to act on."""
    try:
        event = await connection.receive()
    except EventConnectionError as error:
        return error
    if event is None:
        # The service ending a connection on its own is how a session-length limit or a restart
        # looks from here, and both are worth reconnecting through.
        return ConnectionFailedError("the service closed the connection", retryable=True)
    return event


def is_retryable(error: EventConnectionError) -> bool:
    """Whether another connection could succeed where this one failed."""
    return isinstance(error, ConnectionFailedError) and error.retryable


async def replace_connection(
    *,
    open_connection: Callable[[], Awaitable[EventConnection]],
    abandon: Callable[[], Awaitable[None]],
    policy: ReconnectPolicy,
    timekeeping: Timekeeping,
    telemetry: SessionTelemetry,
    budget: ReconnectBudget,
) -> EventConnection | str:
    """A new connection, or the reason none could be had.

    `abandon` releases whatever a failed attempt left half-open, so `open_connection` holds what
    it opens where `abandon` will find it. A refusal that cannot change ends the attempts at once
    rather than spending the rest of them on it.

    `budget` carries attempts from one recovery to the next, so it resets only once a replacement
    has shown it works.
    """
    telemetry.reconnecting()
    while budget.spent < policy.max_attempts:
        attempt = budget.spent
        budget.spent += 1
        await timekeeping.sleep(policy.delay(attempt, timekeeping.draw()))
        try:
            connection = await open_connection()
        except EventConnectionError as failure:
            await abandon()
            telemetry.stream_error(StreamErrorKind.CONNECTION)
            # A replacement found closed while it was being set up is worth another attempt: the
            # service closing it normally is a restart or a limit, not a refusal of the next one.
            if not (is_retryable(failure) or isinstance(failure, ConnectionClosedError)):
                telemetry.reconnected(succeeded=False)
                return str(failure)
            continue
        telemetry.reconnected(succeeded=True)
        return connection
    telemetry.reconnected(succeeded=False)
    return f"the connection could not be restored in {policy.max_attempts} attempts"
