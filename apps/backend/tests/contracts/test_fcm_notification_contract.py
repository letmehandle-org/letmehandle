"""The notification contract, run against the FCM adapter talking to a simulated FCM."""

from __future__ import annotations

import httpx
import pytest

from letmehandle.adapters.notification.fcm.credentials import AccessTokenSource, ServiceAccount
from letmehandle.adapters.notification.fcm.provider import FCMNotificationProvider
from letmehandle.domain.models.identifiers import CallId
from tests.contracts.fakes import FixedClock
from tests.contracts.other_ports import NotificationProviderContract
from tests.support.push_services import (
    EXAMPLE_PROJECT,
    SimulatedFCM,
    fcm_error_body,
    fcm_error_code,
    service_account_json,
)


def fcm_provider(service: SimulatedFCM, clock: FixedClock | None = None) -> FCMNotificationProvider:
    client = httpx.AsyncClient(transport=service.transport())
    return FCMNotificationProvider(
        project_id=EXAMPLE_PROJECT,
        tokens=AccessTokenSource(
            ServiceAccount.parse(service_account_json()), client=client, clock=clock or FixedClock()
        ),
        client=client,
    )


class TestFCMDelivered(NotificationProviderContract):
    """A service that delivers."""

    @pytest.fixture
    def notifier(self) -> FCMNotificationProvider:
        return fcm_provider(SimulatedFCM())

    @pytest.fixture
    def call_id(self) -> CallId:
        return CallId("call-1")


class TestFCMTokenDead(NotificationProviderContract):
    """A service that reports the contract's token as unregistered."""

    @pytest.fixture
    def notifier(self) -> FCMNotificationProvider:
        body = fcm_error_body(404, "NOT_FOUND", details=[fcm_error_code("UNREGISTERED")])
        return fcm_provider(SimulatedFCM(responses={"a-token": (404, body)}))

    @pytest.fixture
    def call_id(self) -> CallId:
        return CallId("call-1")


class TestFCMUnavailable(NotificationProviderContract):
    """A service that is down."""

    @pytest.fixture
    def notifier(self) -> FCMNotificationProvider:
        body = fcm_error_body(503, "UNAVAILABLE", details=[fcm_error_code("UNAVAILABLE")])
        return fcm_provider(SimulatedFCM(responses={"a-token": (503, body)}))

    @pytest.fixture
    def call_id(self) -> CallId:
        return CallId("call-1")
