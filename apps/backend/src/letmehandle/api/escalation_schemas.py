"""What the device and escalation APIs accept and return."""

from __future__ import annotations

from datetime import datetime

from letmehandle.api.schemas import DevicePayload, PushTokenValue, Response
from letmehandle.domain.models.escalation import EscalationReason
from letmehandle.domain.models.escalation_context import EscalationStatus, NotificationDelivery


class RegisterDeviceRequest(DevicePayload):
    """This device's current push token.

    `previous_token` is the token the platform rotated away from, when the app knows it. It is
    removed from this user's devices so the old one does not linger until a delivery fails.
    """

    previous_token: PushTokenValue | None = None


class EscalationContextResponse(Response):
    """An escalation as the app shows it — the same words the notification carried.

    `title`, `caller_label` and `body` are exactly what a notification would display, untrimmed.
    The structured fields beside them are for the app to lay out itself: `caller` is absent when
    nobody knows who is calling, where `caller_label` says so in words. `status` says whether the
    call is still going; an `ended` escalation is shown as what happened, not as a call to join.
    `delivery` says what became of the push, so a failure can be surfaced rather than hidden.
    """

    call_id: str
    status: EscalationStatus
    reason: EscalationReason
    title: str
    caller_label: str
    body: str
    caller: str | None
    established: str | None
    needed: str | None
    raised_at: datetime
    ended_at: datetime | None
    delivery: NotificationDelivery
