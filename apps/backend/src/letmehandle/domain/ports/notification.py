"""Reaching the user's device, best effort, as context for a ring that never depends on it."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from letmehandle.domain.errors import InvariantError

if TYPE_CHECKING:
    from letmehandle.domain.models.identifiers import CallId


class DevicePlatform(StrEnum):
    """Which delivery path a device needs."""

    IOS = "ios"
    ANDROID = "android"


@dataclass(frozen=True, slots=True)
class DeviceToken:
    """Where to send, opaque to this product."""

    platform: DevicePlatform
    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise InvariantError("a device token with nothing in it reaches nobody")

    def __str__(self) -> str:
        """The platform and the token's first characters only."""
        return f"{self.platform}:{self.value[:6]}…"


@dataclass(frozen=True, slots=True)
class EscalationNotification:
    """What the user reads while their phone rings, with no transcript and no recording."""

    call_id: CallId
    title: str
    body: str
    caller_label: str
    data: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.title.strip() or not self.body.strip():
            raise InvariantError(
                "a notification with nothing in it is worse than none: it interrupts and "
                "explains nothing"
            )


class DeliveryStatus(StrEnum):
    """What became of one attempt."""

    DELIVERED = "delivered"
    REJECTED = "rejected"
    # S105 reads the name as a password. It is the outcome of a push delivery.
    TOKEN_INVALID = "token_invalid"  # noqa: S105
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    """The result of one delivery, returned rather than raised."""

    status: DeliveryStatus
    detail: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status is DeliveryStatus.DELIVERED

    @property
    def token_should_be_removed(self) -> bool:
        return self.status is DeliveryStatus.TOKEN_INVALID


class NotificationProvider(ABC):
    """Sends a notification to one device."""

    @property
    @abstractmethod
    def name(self) -> str:
        """What this provider is called, for logs and metrics."""

    @property
    @abstractmethod
    def platform(self) -> DevicePlatform:
        """Which platform's devices this provider can reach."""

    @property
    @abstractmethod
    def payload_limit_bytes(self) -> int:
        """The largest payload this provider's platform accepts, in bytes."""

    @abstractmethod
    def payload_size(self, notification: EscalationNotification) -> int:
        """How many bytes of that limit this notification uses, as this provider encodes it."""

    @abstractmethod
    async def send(
        self, token: DeviceToken, notification: EscalationNotification
    ) -> DeliveryOutcome:
        """Attempt delivery and report what happened, raising only for a defect."""
