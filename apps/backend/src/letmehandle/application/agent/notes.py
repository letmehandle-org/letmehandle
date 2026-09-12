"""What the tools did during one judgement that the judgement has to report.

The adapter cannot read this from the model. A model that was refused may say nothing about it,
and a model that ended the call may not mention that either, so the tools write both down as they
happen and the adapter copies them into `AgentJudgement`. One instance per judgement.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from letmehandle.application.agent.ports import ToolRefusal


class JudgementNotes:
    """The refusals and the ending, in the order they happened."""

    def __init__(self) -> None:
        self._refusals: list[ToolRefusal] = []
        self._ended = False

    @property
    def refusals(self) -> tuple[ToolRefusal, ...]:
        return tuple(self._refusals)

    @property
    def ended(self) -> bool:
        return self._ended

    def refused(self, refusal: ToolRefusal) -> None:
        self._refusals.append(refusal)

    def call_ended(self) -> None:
        self._ended = True
