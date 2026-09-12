"""The dispatcher: every device, the right provider, bounded, deduplicated, and never raising."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from letmehandle.application.escalation.dispatch import (
    AttemptResult,
    DispatchResult,
    EscalationDispatcher,
)
from letmehandle.domain.models.escalation import EscalationReason
from letmehandle.domain.models.escalation_context import (
    EscalationContext,
    EscalationStatus,
    NotificationDelivery,
)
from letmehandle.domain.models.identifiers import CallId, UserId
from letmehandle.domain.ports.notification import (
    DeliveryOutcome,
    DeliveryStatus,
    DevicePlatform,
    DeviceToken,
    EscalationNotification,
)
from tests.contracts.fakes import RecordingNotificationProvider
from tests.support.escalation_stores import InMemoryStores
from tests.support.recording_metrics import RecordingMetrics

RAISED = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
ALICE = UserId("user-1")
BOB = UserId("user-2")
PHONE = DeviceToken(DevicePlatform.IOS, "iphone-token")
TABLET = DeviceToken(DevicePlatform.IOS, "ipad-token")
ANDROID = DeviceToken(DevicePlatform.ANDROID, "android-token")


def a_context(call: str = "call-1", **overrides: object) -> EscalationContext:
    values: dict[str, object] = {
        "call_id": CallId(call),
        "reason": EscalationReason.CALLER_ASKED_FOR_THE_USER,
        "raised_at": RAISED,
        "caller_label": "a courier",
        "established": "They are at the gate.",
        "needed": "Where to leave the parcel.",
    }
    values.update(overrides)
    return EscalationContext(**values)  # type: ignore[arg-type]


class NamedProvider(RecordingNotificationProvider):
    def __init__(self, name: str, platform: DevicePlatform, **kwargs: object) -> None:
        super().__init__(platform=platform, **kwargs)  # type: ignore[arg-type]
        self._name = name

    @property
    def name(self) -> str:
        return self._name


class HangingProvider(NamedProvider):
    async def send(
        self, token: DeviceToken, notification: EscalationNotification
    ) -> DeliveryOutcome:
        await asyncio.sleep(3600)
        return DeliveryOutcome(DeliveryStatus.DELIVERED)  # pragma: no cover - never reached


class BrokenProvider(NamedProvider):
    async def send(
        self, token: DeviceToken, notification: EscalationNotification
    ) -> DeliveryOutcome:
        raise RuntimeError("a bug in an adapter")


def dispatcher(
    stores: InMemoryStores,
    *providers: RecordingNotificationProvider,
    metrics: RecordingMetrics | None = None,
    timeout: float = 5,
) -> EscalationDispatcher:
    return EscalationDispatcher(
        providers=providers,
        stores=stores.scope,
        metrics=metrics or RecordingMetrics(),
        timeout=timedelta(seconds=timeout),
    )


async def registered(stores: InMemoryStores, user: UserId, *tokens: DeviceToken) -> None:
    for token in tokens:
        await stores.devices.register(user, token)


class TestDelivery:
    async def test_every_device_gets_it_through_the_provider_for_its_platform(self) -> None:
        stores = InMemoryStores()
        await registered(stores, ALICE, PHONE, TABLET, ANDROID)
        apple = NamedProvider("apple_like", DevicePlatform.IOS)
        google = NamedProvider("google_like", DevicePlatform.ANDROID)

        report = await dispatcher(stores, apple, google).dispatch(ALICE, a_context())

        assert report.result is DispatchResult.SENT
        assert report.delivered == 3
        assert {token for token, _ in apple.sent} == {PHONE, TABLET}
        assert [token for token, _ in google.sent] == [ANDROID]
        assert report.delivery is NotificationDelivery.DELIVERED

    async def test_what_is_sent_is_the_notification_for_the_context(self) -> None:
        stores = InMemoryStores()
        await registered(stores, ALICE, PHONE)
        apple = NamedProvider("apple_like", DevicePlatform.IOS)
        await dispatcher(stores, apple).dispatch(ALICE, a_context())
        (_, sent) = apple.sent[0]
        assert sent.title == "The caller asked for you"
        assert sent.caller_label == "a courier"
        assert sent.data["call_id"] == "call-1"

    async def test_it_is_trimmed_to_the_provider_s_own_limit(self) -> None:
        stores = InMemoryStores()
        await registered(stores, ALICE, PHONE)
        small = NamedProvider("apple_like", DevicePlatform.IOS, limit=150)
        await dispatcher(stores, small).dispatch(ALICE, a_context())
        (_, sent) = small.sent[0]
        assert small.payload_size(sent) <= 150
        assert "So far:" not in sent.body

    async def test_the_context_is_stored_before_anything_is_sent(self) -> None:
        stores = InMemoryStores()
        await registered(stores, ALICE, PHONE)
        seen: list[EscalationContext | None] = []

        class Observing(NamedProvider):
            async def send(
                self, token: DeviceToken, notification: EscalationNotification
            ) -> DeliveryOutcome:
                seen.append(await stores.contexts.get(ALICE, CallId("call-1")))
                return await super().send(token, notification)

        await dispatcher(stores, Observing("apple_like", DevicePlatform.IOS)).dispatch(
            ALICE, a_context()
        )
        assert seen[0] is not None
        assert seen[0].delivery is NotificationDelivery.PENDING
        stored = await stores.contexts.get(ALICE, CallId("call-1"))
        assert stored is not None and stored.delivery is NotificationDelivery.DELIVERED

    async def test_only_the_user_s_own_devices_are_sent_to(self) -> None:
        stores = InMemoryStores()
        await registered(stores, ALICE, PHONE)
        await registered(stores, BOB, TABLET)
        apple = NamedProvider("apple_like", DevicePlatform.IOS)
        await dispatcher(stores, apple).dispatch(ALICE, a_context())
        assert [token for token, _ in apple.sent] == [PHONE]

    async def test_a_user_with_no_devices_is_recorded_as_such(self) -> None:
        stores = InMemoryStores()
        metrics = RecordingMetrics()
        report = await dispatcher(
            stores, NamedProvider("apple_like", DevicePlatform.IOS), metrics=metrics
        ).dispatch(ALICE, a_context())
        assert report.result is DispatchResult.NO_DEVICES
        stored = await stores.contexts.get(ALICE, CallId("call-1"))
        assert stored is not None and stored.delivery is NotificationDelivery.NO_DEVICES
        assert metrics.counted("escalation.dispatch", outcome="no_devices") == 1


class TestDeduplication:
    async def test_a_second_dispatch_for_the_same_call_sends_nothing(self) -> None:
        stores = InMemoryStores()
        await registered(stores, ALICE, PHONE)
        apple = NamedProvider("apple_like", DevicePlatform.IOS)
        metrics = RecordingMetrics()
        service = dispatcher(stores, apple, metrics=metrics)

        first = await service.dispatch(ALICE, a_context())
        second = await service.dispatch(ALICE, a_context(needed="Something new."))

        assert (first.result, second.result) == (DispatchResult.SENT, DispatchResult.DEDUPLICATED)
        assert len(apple.sent) == 1
        stored = await stores.contexts.get(ALICE, CallId("call-1"))
        assert stored is not None and stored.needed == "Where to leave the parcel."
        assert metrics.counted("escalation.dispatch", outcome="deduplicated") == 1

    async def test_concurrent_dispatches_for_one_call_send_once(self) -> None:
        stores = InMemoryStores()
        await registered(stores, ALICE, PHONE)
        apple = NamedProvider("apple_like", DevicePlatform.IOS)
        service = dispatcher(stores, apple)
        reports = await asyncio.gather(*(service.dispatch(ALICE, a_context()) for _ in range(4)))
        assert sorted(report.result for report in reports) == sorted(
            [DispatchResult.SENT] + [DispatchResult.DEDUPLICATED] * 3
        )
        assert len(apple.sent) == 1

    async def test_a_different_call_is_not_a_duplicate(self) -> None:
        stores = InMemoryStores()
        await registered(stores, ALICE, PHONE)
        apple = NamedProvider("apple_like", DevicePlatform.IOS)
        service = dispatcher(stores, apple)
        await service.dispatch(ALICE, a_context("call-1"))
        await service.dispatch(ALICE, a_context("call-2"))
        assert len(apple.sent) == 2

    async def test_a_context_given_as_already_delivered_is_stored_as_pending(self) -> None:
        stores = InMemoryStores()
        await dispatcher(stores).dispatch(ALICE, a_context(delivery=NotificationDelivery.DELIVERED))
        stored = await stores.contexts.get(ALICE, CallId("call-1"))
        assert stored is not None and stored.delivery is NotificationDelivery.NO_DEVICES


class TestFailureNeverPropagates:
    async def test_one_failing_provider_does_not_affect_the_other(self) -> None:
        stores = InMemoryStores()
        await registered(stores, ALICE, PHONE, ANDROID)
        apple = BrokenProvider("apple_like", DevicePlatform.IOS)
        google = NamedProvider("google_like", DevicePlatform.ANDROID)
        metrics = RecordingMetrics()

        report = await dispatcher(stores, apple, google, metrics=metrics).dispatch(
            ALICE, a_context()
        )

        results = {attempt.token: attempt.result for attempt in report.attempts}
        assert results == {PHONE: AttemptResult.ERRORED, ANDROID: AttemptResult.DELIVERED}
        assert report.delivery is NotificationDelivery.DELIVERED
        assert metrics.counted("escalation.delivery", provider="apple_like", outcome="errored") == 1
        assert (
            metrics.counted("escalation.delivery", provider="google_like", outcome="delivered") == 1
        )

    async def test_a_hanging_provider_is_cut_off_at_the_deadline(self) -> None:
        stores = InMemoryStores()
        await registered(stores, ALICE, PHONE, ANDROID)
        apple = HangingProvider("apple_like", DevicePlatform.IOS)
        google = NamedProvider("google_like", DevicePlatform.ANDROID)

        started = asyncio.get_running_loop().time()
        report = await dispatcher(stores, apple, google, timeout=0.05).dispatch(ALICE, a_context())

        assert asyncio.get_running_loop().time() - started < 1
        results = {attempt.token: attempt.result for attempt in report.attempts}
        assert results == {PHONE: AttemptResult.TIMED_OUT, ANDROID: AttemptResult.DELIVERED}

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (DeliveryStatus.FAILED, AttemptResult.FAILED),
            (DeliveryStatus.REJECTED, AttemptResult.REJECTED),
        ],
    )
    async def test_an_undelivered_notification_is_recorded_as_failed(
        self, status: DeliveryStatus, expected: AttemptResult
    ) -> None:
        stores = InMemoryStores()
        await registered(stores, ALICE, PHONE)
        metrics = RecordingMetrics()
        report = await dispatcher(
            stores, NamedProvider("apple_like", DevicePlatform.IOS, status=status), metrics=metrics
        ).dispatch(ALICE, a_context())
        assert report.attempts[0].result is expected
        assert report.delivery is NotificationDelivery.FAILED
        stored = await stores.contexts.get(ALICE, CallId("call-1"))
        assert stored is not None and stored.delivery is NotificationDelivery.FAILED
        assert PHONE in await stores.devices.tokens_for(ALICE)

    async def test_a_platform_with_no_provider_is_an_outcome_not_an_error(self) -> None:
        stores = InMemoryStores()
        await registered(stores, ALICE, PHONE, ANDROID)
        google = NamedProvider("google_like", DevicePlatform.ANDROID)
        metrics = RecordingMetrics()
        report = await dispatcher(stores, google, metrics=metrics).dispatch(ALICE, a_context())
        results = {attempt.token: attempt.result for attempt in report.attempts}
        assert results == {PHONE: AttemptResult.NOT_CONFIGURED, ANDROID: AttemptResult.DELIVERED}
        assert metrics.counted("escalation.delivery", platform="ios", outcome="not_configured") == 1

    async def test_with_no_provider_at_all_every_device_is_not_configured(self) -> None:
        stores = InMemoryStores()
        await registered(stores, ALICE, PHONE)
        report = await dispatcher(stores).dispatch(ALICE, a_context())
        assert report.attempts[0].result is AttemptResult.NOT_CONFIGURED
        assert report.delivery is NotificationDelivery.FAILED

    async def test_storage_unavailable_before_sending_is_a_report(self) -> None:
        stores = InMemoryStores(fail_on_opening={1})
        await registered(stores, ALICE, PHONE)
        apple = NamedProvider("apple_like", DevicePlatform.IOS)
        metrics = RecordingMetrics()
        report = await dispatcher(stores, apple, metrics=metrics).dispatch(ALICE, a_context())
        assert report.result is DispatchResult.STORAGE_UNAVAILABLE
        assert apple.sent == []
        assert metrics.counted("escalation.storage_failed", stage="claim", kind="error") == 1
        assert metrics.counted("escalation.dispatch", outcome="storage_unavailable") == 1

    async def test_storage_unavailable_after_sending_keeps_what_was_sent(self) -> None:
        stores = InMemoryStores(fail_on_opening={2})
        await registered(stores, ALICE, PHONE)
        apple = NamedProvider("apple_like", DevicePlatform.IOS, status=DeliveryStatus.TOKEN_INVALID)
        metrics = RecordingMetrics()
        report = await dispatcher(stores, apple, metrics=metrics).dispatch(ALICE, a_context())
        assert report.result is DispatchResult.SENT
        assert report.attempts[0].token_removed is False
        assert metrics.counted("escalation.storage_failed", stage="record") == 1
        assert metrics.counted("escalation.token_removed") == 0

    async def test_no_metric_carries_content(self) -> None:
        stores = InMemoryStores()
        await registered(stores, ALICE, PHONE, ANDROID)
        metrics = RecordingMetrics()
        await dispatcher(
            stores,
            NamedProvider("apple_like", DevicePlatform.IOS, status=DeliveryStatus.TOKEN_INVALID),
            BrokenProvider("google_like", DevicePlatform.ANDROID),
            metrics=metrics,
        ).dispatch(ALICE, a_context())
        values = {value for labels in metrics.all_labels() for value in labels.values()}
        assert not values & {"call-1", "user-1", "iphone-token", "android-token", "a courier"}

    def test_two_providers_for_one_platform_is_a_wiring_mistake(self) -> None:
        with pytest.raises(ValueError, match="two notification providers"):
            dispatcher(
                InMemoryStores(),
                NamedProvider("one", DevicePlatform.IOS),
                NamedProvider("two", DevicePlatform.IOS),
            )


class TestTokenRejection:
    async def test_a_dead_token_is_removed_and_a_live_one_kept(self) -> None:
        stores = InMemoryStores()
        await registered(stores, ALICE, PHONE, ANDROID)
        metrics = RecordingMetrics()
        report = await dispatcher(
            stores,
            NamedProvider("apple_like", DevicePlatform.IOS, status=DeliveryStatus.TOKEN_INVALID),
            NamedProvider("google_like", DevicePlatform.ANDROID),
            metrics=metrics,
        ).dispatch(ALICE, a_context())

        assert await stores.devices.tokens_for(ALICE) == [ANDROID]
        removed = {attempt.token: attempt.token_removed for attempt in report.attempts}
        assert removed == {PHONE: True, ANDROID: False}
        assert (
            metrics.counted("escalation.token_removed", platform="ios", provider="apple_like") == 1
        )


class TestBackgroundAndEnding:
    async def test_start_returns_at_once_and_the_task_finishes_on_its_own(self) -> None:
        stores = InMemoryStores()
        await registered(stores, ALICE, PHONE)
        apple = NamedProvider("apple_like", DevicePlatform.IOS)
        service = dispatcher(stores, apple)

        task = service.start(ALICE, a_context())
        assert apple.sent == []
        report = await task
        assert report.result is DispatchResult.SENT
        await service.aclose()

    async def test_closing_waits_for_background_dispatches(self) -> None:
        stores = InMemoryStores()
        await registered(stores, ALICE, PHONE)
        apple = NamedProvider("apple_like", DevicePlatform.IOS)
        service = dispatcher(stores, apple)
        service.start(ALICE, a_context())
        await service.aclose()
        assert len(apple.sent) == 1
        await service.aclose()

    async def test_a_call_that_ends_is_marked_so(self) -> None:
        stores = InMemoryStores()
        service = dispatcher(stores)
        await service.dispatch(ALICE, a_context())
        assert await service.call_ended(ALICE, CallId("call-1"), RAISED + timedelta(minutes=1))
        stored = await stores.contexts.get(ALICE, CallId("call-1"))
        assert stored is not None and stored.status is EscalationStatus.ENDED

    async def test_ending_a_call_that_never_escalated_is_false(self) -> None:
        assert not await dispatcher(InMemoryStores()).call_ended(ALICE, CallId("other"), RAISED)

    async def test_ending_when_storage_fails_is_false_and_counted(self) -> None:
        stores = InMemoryStores(fail_on_opening={1})
        metrics = RecordingMetrics()
        ended = await dispatcher(stores, metrics=metrics).call_ended(ALICE, CallId("c"), RAISED)
        assert ended is False
        assert metrics.counted("escalation.storage_failed", stage="end") == 1

    async def test_a_storage_timeout_is_counted_as_one(self) -> None:
        class TimingOut(InMemoryStores):
            def scope(self):  # type: ignore[no-untyped-def]
                raise TimeoutError

        metrics = RecordingMetrics()
        await dispatcher(TimingOut(), metrics=metrics).call_ended(ALICE, CallId("c"), RAISED)
        assert metrics.counted("escalation.storage_failed", kind="timeout") == 1
