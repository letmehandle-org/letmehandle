"""`request_human_escalation`: the model says what it thinks is going on, and the policy decides.

The model is asked for its reading of the call, never for a reason or an urgency. Those are the
policy's to give, from the user's rules: a model that could name its own urgency is a model a
caller can talk into naming it.

Asking reaches nobody yet. The reading is written down, and the model is told in words what the
rules make of it, so the rest of its judgement can take that into account. The user is reached
once the model has finished, by the conclusion, on the most pressing of every reading it gave —
never from inside the model's turn, where a slow ring would be cut off by the bound on the model's
time and a ring could be followed by a hang-up that cancels it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from letmehandle.application.agent.escalation import circumstances_of
from letmehandle.application.agent.tool import ToolResult, ToolSpec
from letmehandle.application.agent.tools.arguments import (
    SHORT_TEXT_CHARACTERS,
    choice_schema,
    flag,
    flag_schema,
    object_schema,
    optional_choice,
    optional_text,
    options_by_name,
    options_by_value,
    required_choice,
    text_schema,
)
from letmehandle.application.agent.tools.base import CheckedTool
from letmehandle.domain.models.authority import Capability
from letmehandle.domain.models.escalation import EscalationReason
from letmehandle.domain.models.intent import CallImportance, CallIntent
from letmehandle.domain.policy.escalation import EscalationProposal, decide_escalation

if TYPE_CHECKING:
    from collections.abc import Mapping

    from letmehandle.application.agent.ports import CallSoFar
    from letmehandle.application.agent.tool import ToolOutcome
    from letmehandle.domain.models.escalation import EscalationDecision

IMPORTANCE: Final = options_by_name(CallImportance)
INTENT: Final = options_by_value(CallIntent)
CAPABILITY: Final = options_by_value(Capability)

# Why the user is being reached, in words the model reads. Every reason has one, and the test
# that walks the enum is what keeps it that way.
REASON_IN_WORDS: Final[Mapping[EscalationReason, str]] = {
    EscalationReason.CALLER_ASKED_FOR_THE_USER: "the caller asked for them",
    EscalationReason.ACTION_NOT_AUTHORISED: "the caller wants something you are not allowed to do",
    EscalationReason.DECISION_NEEDS_THE_USER: "this needs their decision",
    EscalationReason.IMPORTANT_ENOUGH_TO_INTERRUPT: "the call matters enough to interrupt them",
    EscalationReason.CANNOT_UNDERSTAND_THE_CALLER: "the caller could not be understood",
    EscalationReason.USER_RULE_REQUIRES_IT: "one of their rules requires it",
}

_SPEC: Final = ToolSpec(
    name="request_human_escalation",
    description=(
        "Say what you believe about the call, and ask whether the user should be reached. You do "
        "not decide: the user's own rules do, and the answer tells you what they decided. The "
        "user is reached after your assessment is recorded, at most once however often you ask."
    ),
    parameters=object_schema(
        {
            "importance": choice_schema("How much this call matters to the user.", IMPORTANCE),
            "intent": choice_schema("What the call is for.", INTENT),
            "understood": flag_schema("Whether you understood the caller. Defaults to true."),
            "caller_asked_for_the_user": flag_schema("Whether the caller asked for the user."),
            "needs_the_users_decision": flag_schema(
                "Whether something on the call needs the user to decide it."
            ),
            "requested_capability": choice_schema(
                "What the caller wants you to do, if it is one of these.", CAPABILITY
            ),
            "caller_summary": text_schema(
                "One sentence the user reads before answering, about the caller and what they "
                "want.",
                limit=SHORT_TEXT_CHARACTERS,
            ),
        },
        "importance",
        "intent",
    ),
)


def _in_words(decision: EscalationDecision) -> str:
    if decision.reason is None:
        return (
            "The user's rules do not call for reaching the user on this call. Carry on within what "
            "you are allowed to do."
        )
    why = REASON_IN_WORDS[decision.reason]
    if decision.is_immediate:
        return (
            f"The user's rules call for reaching the user now, because {why}. That happens once "
            f"your assessment is recorded."
        )
    return (
        f"The user's rules call for telling the user about this call when it is convenient, not "
        f"now, because {why}. That happens once your assessment is recorded."
    )


class RequestHumanEscalation(CheckedTool[EscalationProposal]):
    """Needs no grant: asking whether the user should be reached is what the policy is for."""

    @property
    def spec(self) -> ToolSpec:
        return _SPEC

    def _parse(self, arguments: Mapping[str, object]) -> EscalationProposal:
        return EscalationProposal(
            importance=required_choice(arguments, "importance", IMPORTANCE),
            intent=required_choice(arguments, "intent", INTENT),
            understood=flag(arguments, "understood", default=True),
            caller_asked_for_the_user=flag(arguments, "caller_asked_for_the_user"),
            needs_the_users_decision=flag(arguments, "needs_the_users_decision"),
            requested_capability=optional_choice(arguments, "requested_capability", CAPABILITY),
            caller_summary=optional_text(arguments, "caller_summary", limit=SHORT_TEXT_CHARACTERS),
        )

    async def _act(self, call: CallSoFar, parsed: EscalationProposal) -> ToolOutcome:
        self._notes.escalation_requested(parsed)
        return ToolResult(_in_words(decide_escalation(parsed, circumstances_of(call))))
