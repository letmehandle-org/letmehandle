"""Sign-in codes sent and checked by the provider for each number's calling code (D-041)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from letmehandle.adapters.closing import close_each
from letmehandle.domain.ports.otp import OTPProvider

if TYPE_CHECKING:
    from collections.abc import Mapping

    from letmehandle.domain.models.phone_number import PhoneNumber


class OTPProviderByCallingCode(OTPProvider):
    """Sends each code through the provider for its number's calling code, or the default."""

    def __init__(self, *, default: OTPProvider, by_calling_code: Mapping[str, OTPProvider]) -> None:
        self._default = default
        self._by_calling_code = dict(by_calling_code)

    @property
    def name(self) -> str:
        routes = ",".join(
            f"{code}:{provider.name}" for code, provider in sorted(self._by_calling_code.items())
        )
        return f"{self._default.name}+{routes}"

    @property
    def is_safe_for_production(self) -> bool:
        """Safe only if every provider is: one country on a mock is anybody signing in there."""
        return all(provider.is_safe_for_production for provider in self._providers())

    @property
    def fixed_code(self) -> str | None:
        """A fixed code only when every provider fixes the same one."""
        codes = {provider.fixed_code for provider in self._providers()}
        return next(iter(codes)) if len(codes) == 1 else None

    def issues_its_own_codes(self, number: PhoneNumber) -> bool:
        return self._provider_for(number).issues_its_own_codes(number)

    async def send(self, number: PhoneNumber, code: str) -> None:
        await self._provider_for(number).send(number, code)

    async def send_own_code(self, number: PhoneNumber) -> None:
        await self._provider_for(number).send_own_code(number)

    async def check(self, number: PhoneNumber, code: str) -> bool:
        return await self._provider_for(number).check(number, code)

    async def aclose(self) -> None:
        """Release every provider's connections, each once."""
        await close_each(self._providers())

    def _provider_for(self, number: PhoneNumber) -> OTPProvider:
        return self._by_calling_code.get(number.calling_code, self._default)

    def _providers(self) -> tuple[OTPProvider, ...]:
        return (self._default, *self._by_calling_code.values())
