"""Where somebody's phone sends the calls it does not take.

On a deployment whose calls arrive over a telephony account, a call reaches the assistant only
because the user's own carrier forwarded it there: unanswered, or while the line was busy. The
product cannot set that up from its side — it is a setting on the user's line — so the most it
can do is say which number to forward to, and say it the same way everywhere it is asked.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from letmehandle.domain.models.phone_number import PhoneNumber


@dataclass(frozen=True, slots=True)
class CallForwarding:
    """The number a user forwards unanswered and busy calls to.

    One number for every user. It names nobody: whose call a forwarded call is, the carrier's
    forwarded-from number says (D-033).
    """

    number: PhoneNumber
