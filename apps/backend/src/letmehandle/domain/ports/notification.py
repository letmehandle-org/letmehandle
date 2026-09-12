"""Reaching the user's device.

Best effort, and the return type says so. The authoritative event when the assistant needs a
human is the user's phone ringing; this is context for that ring. A caller that treats a
delivery failure as fatal would cancel an escalation because a push did not arrive, which is
exactly backwards.
"""

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
    """Where to send. Opaque: its shape belongs to the platform, not to this product."""

    platform: DevicePlatform
    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise InvariantError("a device token with nothing in it reaches nobody")

    def __str__(self) -> str:
        """Truncated. A device token identifies a person's handset."""
        return f"{self.platform}:{self.value[:6]}…"


@dataclass(frozen=True, slots=True)
class EscalationNotification:
    """What the user reads while their phone is ringing.

    Enough to walk into the call already knowing something, and no more. In particular no
    transcript and no recording: the notification is delivered through two companies' servers
    and stored on a lock screen.
    """

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
    """The result, returned rather than raised.

    Returned because the caller must carry on regardless, and an exception is a poor way to
    say "this did not work and it does not matter much". `TOKEN_INVALID` is separated out
    because it is the one outcome that requires an action: the token should be removed rather
    than retried forever.
    """

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

    @abstractmethod
    async def send(
        self, token: DeviceToken, notification: EscalationNotification
    ) -> DeliveryOutcome:
        """Attempt delivery and report what happened.

        Must not raise for an ordinary delivery failure. Raising is reserved for a programming
        error — a token for the wrong platform, for instance — because that is a defect rather
        than a network having a bad day.
        """
