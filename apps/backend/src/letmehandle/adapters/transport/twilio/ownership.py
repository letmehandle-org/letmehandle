"""Whose streaming call a call is.

A caller dials the user's own number, and the user's carrier forwards it to the account's. The
account's number is shared by everybody the deployment serves, so it names nobody; the line the
call was forwarded from is the user's, and the user is found by it, as they signed in with it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from letmehandle.application.orchestration.ports import CallOwnership

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from letmehandle.adapters.transport.twilio.transport import TwilioCallTransport
    from letmehandle.domain.models.identifiers import UserId
    from letmehandle.domain.models.phone_number import PhoneNumber
    from letmehandle.domain.ports.call_transport import CallEvent


class ForwardedCallOwnership(CallOwnership):
    """The owner of the number a call was forwarded from; nobody's, when it was not forwarded."""

    def __init__(
        self,
        transport: TwilioCallTransport,
        find_user: Callable[[PhoneNumber], Awaitable[UserId | None]],
    ) -> None:
        self._transport = transport
        self._find_user = find_user

    async def owner_of(self, incoming: CallEvent) -> UserId | None:
        line = self._transport.forwarded_from(incoming.call_id)
        return None if line is None else await self._find_user(line)
