"""The strict shape the model's assessment must arrive in; a refused one goes back to the model."""

from __future__ import annotations

from typing import Annotated, Final

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    PlainValidator,
    StrictBool,
    StrictStr,
)

from letmehandle.adapters.agent.strands.schema import one_of, says_something
from letmehandle.application.agent.tools.arguments import (
    SHORT_TEXT_CHARACTERS,
    options_by_name,
    options_by_value,
)
from letmehandle.domain.models.authority import Capability
from letmehandle.domain.models.intent import CallImportance, CallIntent
from letmehandle.domain.policy.escalation import EscalationProposal

# Importance by the words the prompt uses, never by the numbers behind the levels.
_IMPORTANCE: Final = options_by_name(CallImportance)


def _importance_by_name(value: object) -> CallImportance:
    if isinstance(value, str) and value in _IMPORTANCE:
        return _IMPORTANCE[value]
    raise ValueError(f"importance must be one of {', '.join(_IMPORTANCE)}")


def _importance_name(level: CallImportance) -> str:
    return level.name.lower()


type _Intent = Annotated[CallIntent, one_of(options_by_value(CallIntent))]
type _Importance = Annotated[
    CallImportance,
    PlainValidator(_importance_by_name),
    PlainSerializer(_importance_name, return_type=str),
    one_of(_IMPORTANCE),
]
type _Capability = Annotated[Capability, one_of(options_by_value(Capability))]


class CallAssessment(BaseModel):
    """Your assessment of the call so far. Record it once, as the last thing you do."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    intent: _Intent
    importance: _Importance
    understood: StrictBool
    caller_asked_for_the_user: StrictBool
    needs_the_users_decision: StrictBool
    requested_capability: _Capability | None = None
    caller_summary: Annotated[StrictStr, AfterValidator(says_something)] | None = Field(
        default=None, max_length=SHORT_TEXT_CHARACTERS
    )

    def to_proposal(self) -> EscalationProposal:
        """The assessment as the escalation policy reads it."""
        return EscalationProposal(
            importance=self.importance,
            intent=self.intent,
            understood=self.understood,
            caller_asked_for_the_user=self.caller_asked_for_the_user,
            needs_the_users_decision=self.needs_the_users_decision,
            requested_capability=self.requested_capability,
            caller_summary=self.caller_summary,
        )
