"""The handset transport's own behaviour, beyond what the shared contract can see."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
import structlog

from letmehandle.adapters.transport.android_native.transport import AndroidNativeCallTransport
from letmehandle.domain.models.identifiers import CallId, EventId, UserId
from letmehandle.domain.ports.call_transport import CallEvent, CallEventKind

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


@pytest.fixture(autouse=True)
def _unfiltered_logging() -> Iterator[None]:
    # Another test may have configured logging at a level that drops these events before they
    # could be captured. Whatever was configured is put back afterwards.
    configured = structlog.get_config()
    structlog.reset_defaults()
    yield
    structlog.configure(**configured)


USER = UserId("user-1")
SOMEBODY_ELSE = UserId("user-2")


def event(call: str, kind: CallEventKind = CallEventKind.INCOMING, number: int = 1) -> CallEvent:
    return CallEvent(kind, CallId(call), EventId(f"{call}-{number}"))


async def next_event(stream: AsyncIterator[CallEvent]) -> CallEvent:
    return await asyncio.wait_for(anext(stream), timeout=1)


async def nothing_waiting(stream: AsyncIterator[CallEvent]) -> bool:
    try:
        await asyncio.wait_for(anext(stream), timeout=0.05)
    except TimeoutError:
        return True
    return False


async def test_reported_events_arrive_in_the_order_they_were_published() -> None:
    transport = AndroidNativeCallTransport()
    first = event("a", CallEventKind.INCOMING, 1)
    second = event("a", CallEventKind.ANSWERED, 2)
    await transport.publish(USER, first)
    await transport.publish(USER, second)

    stream = transport.events()
    assert await next_event(stream) == first
    assert await next_event(stream) == second


async def test_a_released_call_is_not_handed_on_again() -> None:
    # Terminating cannot hang up a handset's call; it releases the transport's interest in it,
    # so a late report about it does not reappear to whoever consumes these events.
    transport = AndroidNativeCallTransport()
    await transport.terminate(CallId("a"))
    with structlog.testing.capture_logs() as logs:
        await transport.publish(USER, event("a", CallEventKind.ENDED))
    await transport.publish(USER, event("b"))

    assert [entry["event"] for entry in logs] == ["call_event_after_release"]

    stream = transport.events()
    assert (await next_event(stream)).call_id == CallId("b")
    assert await nothing_waiting(stream)


async def test_only_the_most_recent_releases_are_remembered() -> None:
    # Bounded, and wrong in the safe direction when it forgets: an old call's report is handed
    # on rather than a current call's being lost.
    transport = AndroidNativeCallTransport(released_limit=1)
    await transport.terminate(CallId("old"))
    await transport.terminate(CallId("new"))
    await transport.terminate(CallId("new"))
    await transport.publish(USER, event("old"))
    await transport.publish(USER, event("new"))

    stream = transport.events()
    assert (await next_event(stream)).call_id == CallId("old")
    assert await nothing_waiting(stream)


async def test_one_users_flood_of_reports_cannot_crowd_out_another_users_events() -> None:
    transport = AndroidNativeCallTransport(per_user_feed_limit=2)
    with structlog.testing.capture_logs() as logs:
        for number in range(5):
            await transport.publish(USER, event("flood", number=number))
        await transport.publish(SOMEBODY_ELSE, event("quiet"))

    assert [entry["event"] for entry in logs] == ["call_event_feed_full"] * 3
    stream = transport.events()
    assert [(await next_event(stream)).call_id.value for _ in range(3)] == [
        "flood",
        "flood",
        "quiet",
    ]
    assert await nothing_waiting(stream)
    # Read, the flood's events no longer count against it.
    await transport.publish(USER, event("flood", number=9))
    assert (await next_event(transport.events())).event_id == EventId("flood-9")


@pytest.mark.parametrize("name", ["answer", "stream_audio", "inject_audio", "add_participant"])
def test_it_has_no_method_for_what_it_does_not_declare(name: str) -> None:
    # The static half of the honesty: there is nothing here that could be called by mistake.
    assert not hasattr(AndroidNativeCallTransport(), name)
