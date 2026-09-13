"""Whose streaming call a call is: the user whose own line forwarded it (D-033)."""

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
    """The owner of the line that forwarded a call, or of `unforwarded_line` for a direct call."""

    def __init__(
        self,
        transport: TwilioCallTransport,
        find_user: Callable[[PhoneNumber], Awaitable[UserId | None]],
        *,
        unforwarded_line: PhoneNumber | None = None,
    ) -> None:
        self._transport = transport
        self._find_user = find_user
        self._unforwarded_line = unforwarded_line

    async def owner_of(self, incoming: CallEvent) -> UserId | None:
        forwarding = self._transport.forwarding(incoming.call_id)
        if forwarding is None:
            return None
        line = forwarding.line if forwarding.forwarded else self._unforwarded_line
        return None if line is None else await self._find_user(line)
