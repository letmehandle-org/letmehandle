"""Where a user's escalation notifications go: registering, rotating and forgetting devices."""

from __future__ import annotations

from typing import TYPE_CHECKING

from letmehandle.domain.errors import InvariantError

if TYPE_CHECKING:
    from letmehandle.domain.models.identifiers import UserId
    from letmehandle.domain.ports.notification import DeviceToken
    from letmehandle.domain.ports.repositories import DeviceRepository


class DeviceRegistrationService:
    """The token lifecycle a device drives: register, rotate, and sign out."""

    def __init__(self, devices: DeviceRepository) -> None:
        self._devices = devices

    async def register(
        self, user_id: UserId, token: DeviceToken, *, replacing: DeviceToken | None = None
    ) -> None:
        """Record this device's token idempotently, removing `replacing` from this user's."""
        if replacing is not None and replacing.platform is not token.platform:
            raise InvariantError("a token rotates within its platform, never across platforms")
        if replacing is not None and replacing != token:
            await self._devices.remove(user_id, replacing)
        await self._devices.register(user_id, token)

    async def remove(self, user_id: UserId, token: DeviceToken) -> None:
        """Forget this device for this user. Silent when it was not registered."""
        await self._devices.remove(user_id, token)
