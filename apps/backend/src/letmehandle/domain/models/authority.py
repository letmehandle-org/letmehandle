"""What the assistant is allowed to do on someone's behalf, closed unless granted."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Capability(StrEnum):
    """One thing the assistant may be permitted to do, each a decision a user makes on its own."""

    ANSWER_QUESTIONS_ABOUT_AVAILABILITY = "answer_questions_about_availability"
    SHARE_DELIVERY_INSTRUCTIONS = "share_delivery_instructions"
    CONFIRM_APPOINTMENTS = "confirm_appointments"
    RESCHEDULE_APPOINTMENTS = "reschedule_appointments"
    DECLINE_ON_THE_USERS_BEHALF = "decline_on_the_users_behalf"
    TAKE_A_MESSAGE = "take_a_message"
    SHARE_CONTACT_DETAILS = "share_contact_details"


@dataclass(frozen=True, slots=True)
class AgentAuthority:
    """The capabilities a user has granted, frozen for the length of a call."""

    capabilities: frozenset[Capability] = field(default_factory=frozenset)

    @classmethod
    def none(cls) -> AgentAuthority:
        """Granting nothing: what an unconfigured user gets."""
        return cls()

    @classmethod
    def granting(cls, *capabilities: Capability) -> AgentAuthority:
        return cls(frozenset(capabilities))

    def allows(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def __bool__(self) -> bool:
        """Whether anything at all has been granted."""
        return bool(self.capabilities)
