"""Delivering a one-time sign-in code, or having a provider make and check one (D-037, D-042)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from letmehandle.domain.errors import CapabilityNotSupportedError

if TYPE_CHECKING:
    from letmehandle.domain.models.phone_number import PhoneNumber


class OTPProvider(ABC):
    """Sends a code to a number, or, where it makes the codes itself, sends one and checks it."""

    @property
    @abstractmethod
    def name(self) -> str:
        """What this provider is called, for logs and for readiness."""

    @property
    @abstractmethod
    def is_safe_for_production(self) -> bool:
        """Whether this provider delivers to a real handset, which production requires."""

    @property
    def fixed_code(self) -> str | None:
        """A code every challenge uses, for testing, from a provider unsafe for production only."""
        return None

    def issues_its_own_codes(self, number: PhoneNumber) -> bool:
        """Whether this provider, rather than the application, makes and checks `number`'s code."""
        return False

    @abstractmethod
    async def send(self, number: PhoneNumber, code: str) -> None:
        """Deliver the application's code, or raise `UnreachableNumberError` or `ProviderError`."""

    async def send_own_code(self, number: PhoneNumber) -> None:
        """Make a code and deliver it, for a number `issues_its_own_codes` answers true for."""
        raise CapabilityNotSupportedError(self.name, "issuing its own sign-in codes")

    async def check(self, number: PhoneNumber, code: str) -> bool:
        """Whether `code` is the live one sent to `number`; `ProviderError` when it cannot say."""
        raise CapabilityNotSupportedError(self.name, "checking sign-in codes")
