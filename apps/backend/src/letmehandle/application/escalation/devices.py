"""Where a user's escalation notifications go: registering, rotating and forgetting devices.

Strictly the signed-in user's own (D-012). A token registered by another account moves to this
one, because a handset that changes hands must stop receiving its previous owner's calls.
"""

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
        """Record this device's current token, idempotently.

        `replacing` is the token the platform rotated away from. Removing it here, rather than
        waiting for the platform to report it dead, stops a rotated token receiving the next
        escalation twice or not at all. It is removed only from this user's devices.
        """
        if replacing is not None and replacing.platform is not token.platform:
            raise InvariantError("a token rotates within its platform, never across platforms")
        if replacing is not None and replacing != token:
            await self._devices.remove(user_id, replacing)
        await self._devices.register(user_id, token)

    async def remove(self, user_id: UserId, token: DeviceToken) -> None:
        """Forget this device for this user. Silent when it was not registered."""
        await self._devices.remove(user_id, token)
