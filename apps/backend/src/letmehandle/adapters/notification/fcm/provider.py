"""Delivery to Android devices, directly through FCM's HTTP v1 API (D-015).

One `messages:send` per notification. The message is shaped for an escalation: a notification
the system shows even when the app is not running, Android priority `HIGH` so it is delivered at
once and may wake a dozing device, a short TTL so a notification for a long-finished call is not
delivered later, and the call as collapse key and notification tag so a repeat replaces rather
than stacks. The notification names a channel the app creates with high importance; on Android 8
and later that channel, not anything in the message, decides whether it makes a sound.

Every documented error maps to an outcome. `UNREGISTERED`, and `INVALID_ARGUMENT` when Google
names the token as the invalid field, mean the token is dead. `INVALID_ARGUMENT` about anything
else is this message being wrong, which removing a user's device would not fix.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any, Final

import httpx

from letmehandle.adapters.notification.fcm.credentials import AccessTokenError
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
    from letmehandle.adapters.notification.fcm.credentials import AccessTokenSource
    from letmehandle.domain.ports.notification import DeviceToken, EscalationNotification

logger = get_logger(__name__)

PROVIDER_NAME: Final = "fcm"
SEND_ENDPOINT: Final = "https://fcm.googleapis.com/v1/projects/{project}/messages:send"

# FCM's limit for a message to a token, counting keys and values.
PAYLOAD_LIMIT_BYTES: Final = 4096

DEFAULT_TTL: Final = timedelta(minutes=10)
DEFAULT_REQUEST_TIMEOUT: Final = 10.0

# The notification channel the app creates for escalations. A contract with the app: a channel it
# has not created falls back to the default channel declared in its manifest.
ESCALATION_CHANNEL: Final = "escalation"

_FCM_ERROR_TYPE: Final = "type.googleapis.com/google.firebase.fcm.v1.FcmError"
_BAD_REQUEST_TYPE: Final = "type.googleapis.com/google.rpc.BadRequest"
_TOKEN_FIELDS: Final = frozenset({"message.token", "token"})

_RETRYABLE: Final = frozenset({"QUOTA_EXCEEDED", "UNAVAILABLE", "INTERNAL", "UNAUTHENTICATED"})


def build_client(*, timeout: float) -> httpx.AsyncClient:
    """A client for Google's endpoints. HTTP/2 when offered, which Google's front ends do."""
    return httpx.AsyncClient(http2=True, timeout=timeout)


class FCMNotificationProvider(NotificationProvider):
    """Sends escalation notifications to Android devices."""

    def __init__(
        self,
        *,
        project_id: str,
        tokens: AccessTokenSource,
        client: httpx.AsyncClient,
        ttl: timedelta = DEFAULT_TTL,
    ) -> None:
        self._endpoint = SEND_ENDPOINT.format(project=project_id)
        self._tokens = tokens
        self._client = client
        self._ttl = ttl

    @property
    def name(self) -> str:
        return PROVIDER_NAME

    @property
    def platform(self) -> DevicePlatform:
        return DevicePlatform.ANDROID

    @property
    def payload_limit_bytes(self) -> int:
        return PAYLOAD_LIMIT_BYTES

    def payload_size(self, notification: EscalationNotification) -> int:
        # Everything but the address: the limit is on what is delivered, not on where to.
        return len(compact_json(self.message(notification)))

    def message(
        self, notification: EscalationNotification, token: DeviceToken | None = None
    ) -> dict[str, Any]:
        """The v1 `Message`, addressed to `token` when one is given."""
        collapse = collapse_id_for(notification.call_id)
        addressed: dict[str, Any] = {} if token is None else {"token": token.value}
        return {
            **addressed,
            "notification": {
                "title": notification.title,
                # No subtitle on Android: who is calling leads the body instead.
                "body": f"{notification.caller_label}\n{notification.body}",
            },
            "data": dict(notification.data),
            "android": {
                "priority": "HIGH",
                "ttl": f"{int(self._ttl.total_seconds())}s",
                "collapse_key": collapse,
                "notification": {"tag": collapse, "channel_id": ESCALATION_CHANNEL},
            },
        }

    async def send(
        self, token: DeviceToken, notification: EscalationNotification
    ) -> DeliveryOutcome:
        if token.platform is not DevicePlatform.ANDROID:
            raise ProviderError(
                PROVIDER_NAME, f"a {token.platform} token cannot be sent by FCM", retryable=False
            )
        try:
            access = await self._tokens.current()
        except AccessTokenError as error:
            status = DeliveryStatus.FAILED if error.retryable else DeliveryStatus.REJECTED
            logger.warning(
                "notification.authorisation_failed", provider=PROVIDER_NAME, reason=error.reason
            )
            return DeliveryOutcome(status, f"authorisation: {error.reason}")

        try:
            response = await self._client.post(
                self._endpoint,
                content=compact_json({"message": self.message(notification, token)}),
                headers={"authorization": f"Bearer {access}", "content-type": "application/json"},
            )
        except httpx.HTTPError as error:
            logger.warning(
                "notification.transport_failed", provider=PROVIDER_NAME, error=type(error).__name__
            )
            return DeliveryOutcome(DeliveryStatus.FAILED, f"transport: {type(error).__name__}")

        outcome, code = outcome_for(response)
        if code == "UNAUTHENTICATED":
            self._tokens.rejected()
        return outcome

    async def aclose(self) -> None:
        """Close the connection. Called once, when the application stops."""
        await self._client.aclose()


def outcome_for(response: httpx.Response) -> tuple[DeliveryOutcome, str | None]:
    """What one FCM response means, and the error code it was read from."""
    if response.status_code == 200:
        return DeliveryOutcome(DeliveryStatus.DELIVERED), None
    code, invalid_fields = _error_of(response)
    detail = f"{response.status_code} {code}" if code else str(response.status_code)

    if code == "UNREGISTERED":
        return DeliveryOutcome(DeliveryStatus.TOKEN_INVALID, detail), code
    if code == "INVALID_ARGUMENT" and invalid_fields & _TOKEN_FIELDS:
        return DeliveryOutcome(DeliveryStatus.TOKEN_INVALID, detail), code
    if code in _RETRYABLE or response.status_code == 429 or response.status_code >= 500:
        return DeliveryOutcome(DeliveryStatus.FAILED, detail), code
    if 400 <= response.status_code < 500:
        return DeliveryOutcome(DeliveryStatus.REJECTED, detail), code
    return DeliveryOutcome(DeliveryStatus.FAILED, detail), code


def _error_of(response: httpx.Response) -> tuple[str | None, frozenset[str]]:
    """The most specific error code in a Google error body, and the fields it names as invalid.

    FCM's own code, from its `FcmError` detail, wins over the generic status: a missing token and
    a dead one are both `NOT_FOUND`, and only the detail says `UNREGISTERED`.
    """
    try:
        body = response.json()
    except ValueError:
        return None, frozenset()
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return None, frozenset()

    code = error.get("status") if isinstance(error.get("status"), str) else None
    fields: set[str] = set()
    details = error.get("details")
    for detail in details if isinstance(details, list) else []:
        if not isinstance(detail, dict):
            continue
        if detail.get("@type") == _FCM_ERROR_TYPE and isinstance(detail.get("errorCode"), str):
            code = detail["errorCode"]
        if detail.get("@type") == _BAD_REQUEST_TYPE:
            violations = detail.get("fieldViolations")
            for violation in violations if isinstance(violations, list) else []:
                if isinstance(violation, dict) and isinstance(violation.get("field"), str):
                    fields.add(violation["field"])
    return code, frozenset(fields)
