"""Delivery to iOS devices, directly through Apple's push service (D-015).

One POST per notification to `/3/device/<token>` over HTTP/2, authenticated with a provider
token. The request is shaped for an escalation: an alert, priority 10 (immediately), the
time-sensitive interruption level so it breaks through a Focus on iOS 15 and later, a short
expiration so a notification for a long-finished call is not delivered hours later, and the call
as the collapse id so a repeat replaces rather than stacks.

Every documented response is mapped to an outcome. Three reasons mean the token is dead and must
be removed; the rest are either worth trying again (`FAILED`) or will fail identically until
something is fixed (`REJECTED`). Nothing here raises for a delivery that did not happen.
"""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Final
from urllib.parse import quote

import httpx

from letmehandle.adapters.notification.shared import collapse_id_for, compact_json
from letmehandle.domain.errors import ProviderError
from letmehandle.domain.ports.notification import (
    DeliveryOutcome,
    DeliveryStatus,
    DevicePlatform,
    NotificationProvider,
)
from letmehandle.observability.logging import get_logger

if TYPE_CHECKING:
    from letmehandle.adapters.notification.apns.token import APNsProviderToken
    from letmehandle.domain.ports.clock import Clock
    from letmehandle.domain.ports.notification import DeviceToken, EscalationNotification

logger = get_logger(__name__)

PROVIDER_NAME: Final = "apns"

# The payload limit for an alert. VoIP pushes may be larger; these are not VoIP pushes.
PAYLOAD_LIMIT_BYTES: Final = 4096

# Long enough to ride out a phone briefly without signal, short enough that a notification for a
# call that has ended is not delivered after the user has moved on.
DEFAULT_EXPIRY: Final = timedelta(minutes=10)
DEFAULT_REQUEST_TIMEOUT: Final = 10.0


class APNsEnvironment(StrEnum):
    """Which of Apple's servers. A token issued by one is `BadDeviceToken` to the other."""

    SANDBOX = "sandbox"
    PRODUCTION = "production"


HOSTS: Final = {
    APNsEnvironment.SANDBOX: "https://api.sandbox.push.apple.com",
    APNsEnvironment.PRODUCTION: "https://api.push.apple.com",
}

# The token is dead for this app: stop sending to it. 410 means the same whatever its reason.
_TOKEN_DEAD: Final = frozenset({"BadDeviceToken", "Unregistered", "ExpiredToken"})

# Transient. The same request may succeed later.
_RETRYABLE: Final = frozenset(
    {
        "IdleTimeout",
        "ExpiredProviderToken",
        "TooManyRequests",
        "TooManyProviderTokenUpdates",
        "InternalServerError",
        "ServiceUnavailable",
        "Shutdown",
    }
)


def build_client(environment: APNsEnvironment, *, timeout: float) -> httpx.AsyncClient:
    """An HTTP/2 client for one environment. HTTP/1.1 is not accepted by APNs at all."""
    return httpx.AsyncClient(base_url=HOSTS[environment], http2=True, timeout=timeout)


class APNsNotificationProvider(NotificationProvider):
    """Sends escalation alerts to iOS devices."""

    def __init__(
        self,
        *,
        token: APNsProviderToken,
        topic: str,
        environment: APNsEnvironment,
        clock: Clock,
        client: httpx.AsyncClient | None = None,
        expiry: timedelta = DEFAULT_EXPIRY,
    ) -> None:
        self._token = token
        self._topic = topic
        self._clock = clock
        self._expiry = expiry
        self._client = client or build_client(environment, timeout=DEFAULT_REQUEST_TIMEOUT)

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    @property
    def platform(self) -> DevicePlatform:
        return DevicePlatform.IOS

    @property
    def payload_limit_bytes(self) -> int:
        return PAYLOAD_LIMIT_BYTES

    def payload_size(self, notification: EscalationNotification) -> int:
        return len(encode(notification))

    async def send(
        self, token: DeviceToken, notification: EscalationNotification
    ) -> DeliveryOutcome:
        if token.platform is not DevicePlatform.IOS:
            raise ProviderError(
                PROVIDER_NAME, f"a {token.platform} token cannot be sent by APNs", retryable=False
            )
        expires_at = self._clock.now() + self._expiry
        try:
            response = await self._client.post(
                # Quoted, because the path is built from a value a client registered.
                f"/3/device/{quote(token.value, safe='')}",
                content=encode(notification),
                headers={
                    "authorization": f"bearer {self._token.current()}",
                    "apns-push-type": "alert",
                    "apns-priority": "10",
                    "apns-expiration": str(int(expires_at.timestamp())),
                    "apns-topic": self._topic,
                    "apns-collapse-id": collapse_id_for(notification.call_id),
                    "content-type": "application/json",
                },
            )
        except httpx.HTTPError as error:
            # The exception's text can include the URL, and the URL includes the device token.
            logger.warning(
                "notification.transport_failed", provider=PROVIDER_NAME, error=type(error).__name__
            )
            return DeliveryOutcome(DeliveryStatus.FAILED, f"transport: {type(error).__name__}")

        reason = _reason(response)
        if reason == "ExpiredProviderToken":
            self._token.rejected()
        return outcome_for(response.status_code, reason)

    async def aclose(self) -> None:
        """Close the connection. Called once, when the application stops."""
        await self._client.aclose()


def encode(notification: EscalationNotification) -> bytes:
    """The JSON body.

    The call's identifiers sit beside `aps` rather than inside it, which is where Apple puts an
    app's own keys. They are written first so that nothing in them can replace `aps`.
    """
    return compact_json(
        {
            **notification.data,
            "aps": {
                "alert": {
                    "title": notification.title,
                    "subtitle": notification.caller_label,
                    "body": notification.body,
                },
                "sound": "default",
                "interruption-level": "time-sensitive",
            },
        }
    )


def outcome_for(status_code: int, reason: str | None) -> DeliveryOutcome:
    """What one APNs response means for the notification and for the token."""
    detail = f"{status_code} {reason}" if reason else str(status_code)
    if status_code == 200:
        return DeliveryOutcome(DeliveryStatus.DELIVERED)
    if status_code == 410 or reason in _TOKEN_DEAD:
        return DeliveryOutcome(DeliveryStatus.TOKEN_INVALID, detail)
    if reason in _RETRYABLE or status_code == 429 or status_code >= 500:
        return DeliveryOutcome(DeliveryStatus.FAILED, detail)
    if 400 <= status_code < 500:
        return DeliveryOutcome(DeliveryStatus.REJECTED, detail)
    return DeliveryOutcome(DeliveryStatus.FAILED, detail)


def _reason(response: httpx.Response) -> str | None:
    """The `reason` from an error body, or nothing when the body is not what Apple documents."""
    try:
        body = response.json()
    except ValueError:
        return None
    reason = body.get("reason") if isinstance(body, dict) else None
    return reason if isinstance(reason, str) else None
