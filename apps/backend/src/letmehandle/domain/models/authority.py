"""What the assistant is allowed to do on someone's behalf.

A capability set, not a boolean. "Can the assistant act?" is not a question with one answer:
telling a courier where to leave a parcel and agreeing to a payment are both acting, and a
user who wants the first does not thereby want the second.

Everything defaults to closed. A capability that has to be granted can be forgotten and the
result is an assistant that escalates too often; a capability granted by default can be
forgotten and the result is an assistant that agreed to something on a stranger's say-so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Capability(StrEnum):
    """One thing the assistant may be permitted to do.

    Each is a distinct decision a user would actually make differently, which is the test for
    whether a capability belongs here. Splitting further produces a settings screen nobody
    finishes; merging produces permissions nobody meant to give.
    """

    ANSWER_QUESTIONS_ABOUT_AVAILABILITY = "answer_questions_about_availability"
    SHARE_DELIVERY_INSTRUCTIONS = "share_delivery_instructions"
    CONFIRM_APPOINTMENTS = "confirm_appointments"
    RESCHEDULE_APPOINTMENTS = "reschedule_appointments"
    DECLINE_ON_THE_USERS_BEHALF = "decline_on_the_users_behalf"
    TAKE_A_MESSAGE = "take_a_message"
    SHARE_CONTACT_DETAILS = "share_contact_details"


@dataclass(frozen=True, slots=True)
class AgentAuthority:
    """The set of capabilities a user has granted.

    Frozen, so that nothing widens its own permissions midway through a call. Granting returns
    a new set rather than mutating this one, which means a capability check made earlier in a
    call cannot be invalidated by something that happened later.
    """

    capabilities: frozenset[Capability] = field(default_factory=frozenset)

    @classmethod
    def none(cls) -> AgentAuthority:
        """Granting nothing. The starting point, and what an unconfigured user gets."""
        return cls()

    @classmethod
    def granting(cls, *capabilities: Capability) -> AgentAuthority:
        return cls(frozenset(capabilities))

    def allows(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def __bool__(self) -> bool:
        """Whether anything at all has been granted."""
        return bool(self.capabilities)
