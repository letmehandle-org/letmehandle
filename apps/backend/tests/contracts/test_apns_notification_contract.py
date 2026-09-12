"""The notification contract, run against the APNs adapter talking to a simulated APNs."""

from __future__ import annotations

import httpx
import pytest

from letmehandle.adapters.notification.apns.provider import (
    APNsEnvironment,
    APNsNotificationProvider,
)
from letmehandle.adapters.notification.apns.token import APNsProviderToken
from letmehandle.domain.models.identifiers import CallId
from tests.contracts.fakes import FixedClock
from tests.contracts.other_ports import NotificationProviderContract
from tests.support.push_services import (
    EXAMPLE_KEY_ID,
    EXAMPLE_TEAM_ID,
    EXAMPLE_TOPIC,
    SimulatedAPNs,
    ec_key_pem,
)


def apns_provider(
    service: SimulatedAPNs, clock: FixedClock | None = None
) -> APNsNotificationProvider:
    moment = clock or FixedClock()
    return APNsNotificationProvider(
        token=APNsProviderToken(
            key_id=EXAMPLE_KEY_ID, team_id=EXAMPLE_TEAM_ID, private_key=ec_key_pem(), clock=moment
        ),
        topic=EXAMPLE_TOPIC,
        environment=APNsEnvironment.SANDBOX,
        clock=moment,
        client=httpx.AsyncClient(
            base_url="https://api.sandbox.push.apple.com", transport=service.transport()
        ),
    )


class TestAPNsDelivered(NotificationProviderContract):
    """A service that delivers."""

    @pytest.fixture
    def notifier(self) -> APNsNotificationProvider:
        return apns_provider(SimulatedAPNs())

    @pytest.fixture
    def call_id(self) -> CallId:
        return CallId("call-1")


class TestAPNsTokenDead(NotificationProviderContract):
    """A service that reports the contract's token as unregistered."""

    @pytest.fixture
    def notifier(self) -> APNsNotificationProvider:
        return apns_provider(SimulatedAPNs(responses={"a-token": (410, "Unregistered")}))

    @pytest.fixture
    def call_id(self) -> CallId:
        return CallId("call-1")


class TestAPNsUnavailable(NotificationProviderContract):
    """A service that is down."""

    @pytest.fixture
    def notifier(self) -> APNsNotificationProvider:
        return apns_provider(SimulatedAPNs(responses={"a-token": (503, "ServiceUnavailable")}))

    @pytest.fixture
    def call_id(self) -> CallId:
        return CallId("call-1")
