"""What one judgement's tools refused and asked for, for the conclusion to report and act on."""

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
