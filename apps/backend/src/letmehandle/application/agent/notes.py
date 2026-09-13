"""What the tools did during one judgement that the judgement has to report, or act on afterwards.

The adapter cannot read this from the model. A model that was refused may say nothing about it,
and a model that asked for the user or for the call to end may not mention that either, so the
tools write all of it down as it happens. One instance per judgement.

Asking is not doing. A model asks for the user and for an ending while it is still working the call
out, and neither happens until it has finished: the conclusion reads what was asked for here and
acts on it once, in an order that lets the user's rules overrule a hang-up.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from letmehandle.application.agent.ports import CallEnding, ToolRefusal
    from letmehandle.domain.policy.escalation import EscalationProposal


class JudgementNotes:
    """The refusals, the escalations and the ending asked for, and whether the call was ended."""

    def __init__(self) -> None:
        self._refusals: list[ToolRefusal] = []
        self._escalations_requested: list[EscalationProposal] = []
        self._requested_ending: CallEnding | None = None
        self._ended = False

    @property
    def refusals(self) -> tuple[ToolRefusal, ...]:
        return tuple(self._refusals)

    @property
    def escalations_requested(self) -> tuple[EscalationProposal, ...]:
        """Every reading the model sent when it asked for the user, in order."""
        return tuple(self._escalations_requested)

    @property
    def requested_ending(self) -> CallEnding | None:
        """The ending the model asked for, if it asked for one."""
        return self._requested_ending

    @property
    def ended(self) -> bool:
        """Whether the call was actually ended, which asking for an ending does not guarantee."""
        return self._ended

    def refused(self, refusal: ToolRefusal) -> None:
        self._refusals.append(refusal)

    def escalation_requested(self, proposal: EscalationProposal) -> None:
        self._escalations_requested.append(proposal)

    def ending_requested(self, ending: CallEnding) -> None:
        self._requested_ending = ending

    def call_ended(self) -> None:
        self._ended = True
