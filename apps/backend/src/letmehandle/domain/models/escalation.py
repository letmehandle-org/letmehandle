"""The decision to involve the human, and why.

The reason is structured. A sentence would be easier to produce and impossible to act on: the
notification wants a human-readable line, the metrics want a category, and the tests want
something to assert. Deriving all three from prose means parsing prose.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from letmehandle.domain.errors import InvariantError


class EscalationReason(StrEnum):
    """Why the assistant wants a person.

    These are the reasons it can actually distinguish, and each leads somewhere different: an
    unauthorised action may be resolved by granting a capability, a caller's request may be
    resolved by the user answering, and a failure is an operational problem.
    """

    CALLER_ASKED_FOR_THE_USER = "caller_asked_for_the_user"
    ACTION_NOT_AUTHORISED = "action_not_authorised"
    DECISION_NEEDS_THE_USER = "decision_needs_the_user"
    IMPORTANT_ENOUGH_TO_INTERRUPT = "important_enough_to_interrupt"
    CANNOT_UNDERSTAND_THE_CALLER = "cannot_understand_the_caller"
    USER_RULE_REQUIRES_IT = "user_rule_requires_it"


class EscalationUrgency(StrEnum):
    """How hard to try to reach the user.

    `WHILE_CONVENIENT` exists so that "the user would want to know" does not have to mean
    "ring their phone now". Without it every escalation is an interruption, and an assistant
    that always interrupts is one people turn off.
    """

    IMMEDIATE = "immediate"
    WHILE_CONVENIENT = "while_convenient"


@dataclass(frozen=True, slots=True)
class EscalationDecision:
    """Whether a human is needed, and on what grounds.

    Construct through `not_needed` or `needed`. The pairing of "no escalation" with a reason,
    or "escalate" with none, is not a state this type allows to exist.
    """

    required: bool
    reason: EscalationReason | None = None
    urgency: EscalationUrgency | None = None
    caller_summary: str | None = None

    def __post_init__(self) -> None:
        if self.required and (self.reason is None or self.urgency is None):
            raise InvariantError(
                "an escalation must say why it is happening and how urgent it is; "
                "the notification and the call history are both built from those"
            )
        if not self.required and (self.reason is not None or self.urgency is not None):
            raise InvariantError(
                "a decision not to escalate carries no reason or urgency, or it reads as one "
                "that was cancelled"
            )
        if self.caller_summary is not None and not self.caller_summary.strip():
            raise InvariantError("a caller summary is either absent or says something")

    @classmethod
    def not_needed(cls) -> EscalationDecision:
        return cls(required=False)

    @classmethod
    def needed(
        cls,
        reason: EscalationReason,
        urgency: EscalationUrgency,
        caller_summary: str | None = None,
    ) -> EscalationDecision:
        """Escalate, for this reason.

        `caller_summary` is what the user reads before they answer — "a courier is at the gate
        and needs to know where to leave a parcel". It is what turns a ringing phone into a
        call somebody can walk into already knowing something.
        """
        return cls(required=True, reason=reason, urgency=urgency, caller_summary=caller_summary)

    @property
    def is_immediate(self) -> bool:
        return self.urgency is EscalationUrgency.IMMEDIATE
