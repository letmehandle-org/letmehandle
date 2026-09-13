"""What the device and escalation APIs accept and return."""

from __future__ import annotations

from datetime import datetime

from letmehandle.api.schemas import DevicePayload, PushTokenValue, Response
from letmehandle.domain.models.escalation import EscalationReason
from letmehandle.domain.models.escalation_context import EscalationStatus, NotificationDelivery


class RegisterDeviceRequest(DevicePayload):
    """This device's current push token, and the one the platform rotated away from if known."""

    previous_token: PushTokenValue | None = None


class EscalationContextResponse(Response):
    """An escalation as the app shows it: the untrimmed notification words and their fields."""

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
