"""What orchestration needs from outside itself.

`CallOwnership` says whose call an arriving call is. That is how the call reached the product, which
D-004 makes the transport's business: a handset reports on behalf of the account it signed in as,
and a telephony number is reached through the user's own line forwarding to it. So each transport's
side answers, chosen in bootstrap, and orchestration asks without knowing which answered.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from letmehandle.domain.models.identifiers import UserId
    from letmehandle.domain.ports.call_transport import CallEvent


class CallOwnership(ABC):
    """Whose call has just arrived."""

    @abstractmethod
    async def owner_of(self, incoming: CallEvent) -> UserId | None:
        """The user the call is for, or None when it is nobody's this deployment serves."""
