"""The call agent on the Strands Agents SDK, one SDK agent per judgement (D-026)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Final

from letmehandle.adapters.agent.strands.assessment import CallAssessment
from letmehandle.adapters.agent.strands.model import single_use_agent
from letmehandle.adapters.agent.strands.tools import ToolLedger, UnknownToolRefusals, present
from letmehandle.application.agent.conclusion import (
    NOT_ENDED_AFTER_A_FAILURE,
    NOT_ENDED_WITHOUT_AN_ASSESSMENT,
)
from letmehandle.application.agent.ports import CallAgent
from letmehandle.application.agent.prompts import load_prompts
from letmehandle.domain.models.intent import CallImportance, CallIntent
from letmehandle.domain.policy.escalation import EscalationProposal
from letmehandle.observability.logging import get_logger

if TYPE_CHECKING:
    from datetime import timedelta

    from strands import Agent
    from strands.models.model import Model

    from letmehandle.application.agent.conclusion import JudgementConclusion
    from letmehandle.application.agent.ports import AgentJudgement, CallSoFar
    from letmehandle.application.agent.prompts import Prompts
    from letmehandle.application.agent.tool import ToolsForAJudgement

# The name the model knows the assessment by, which is the name the SDK gives its tool.
ASSESSMENT_TOOL: Final = CallAssessment.__name__

# Model turns per judgement: a tool each, the assessment, and two re-asks for it.
MAX_TURNS: Final = 8


def fallback_proposal() -> EscalationProposal:
    """The not-understood, ignorable proposal made when the model gave no usable answer."""
    return EscalationProposal(
        importance=CallImportance.IGNORABLE,
        intent=CallIntent.UNDETERMINED,
        understood=False,
    )


class StrandsCallAgent(CallAgent):
    """Judges a call by running a model over the application's tools, within a time bound."""

    def __init__(
        self,
        model: Model,
        *,
        tools: ToolsForAJudgement,
        conclusion: JudgementConclusion,
        timeout: timedelta,
    ) -> None:
        if timeout.total_seconds() <= 0:
            raise ValueError("a judgement needs time to happen in")
        self._model = model
        self._tools = tools
        self._conclusion = conclusion
        self._timeout = timeout
        self._logger = get_logger(__name__)

    async def judge(self, call: CallSoFar) -> AgentJudgement:
        """Runs the model, then concludes; a tool's failure is raised after the escalation."""
        prompts = load_prompts(call.preferences.locale)
        ledger = ToolLedger()
        agent = single_use_agent(
            self._model,
            tools=[present(tool, call, ledger) for tool in self._tools(ledger.notes)],
            hooks=[UnknownToolRefusals(ledger)],
            system_prompt=prompts.system_prompt(
                call.preferences, call.authority, assessment_tool=ASSESSMENT_TOOL
            ),
        )
        assessment = await self._assess(agent, prompts, call)
        if ledger.failure is not None:
            withheld: str | None = NOT_ENDED_AFTER_A_FAILURE
        elif assessment is None:
            withheld = NOT_ENDED_WITHOUT_AN_ASSESSMENT
        else:
            withheld = None
        try:
            return await self._conclusion.conclude(
                call,
                ledger.notes,
                assessment if assessment is not None else fallback_proposal(),
                ending_withheld=withheld,
            )
        finally:
            # Raised after the escalation, never instead of it.
            if ledger.failure is not None:
                raise ledger.failure

    async def _assess(
        self, agent: Agent, prompts: Prompts, call: CallSoFar
    ) -> EscalationProposal | None:
        """The model's validated assessment, or None when it gave no usable one."""
        try:
            async with asyncio.timeout(self._timeout.total_seconds()):
                result = await agent.invoke_async(
                    prompts.transcript_message(call.transcript),
                    structured_output_model=CallAssessment,
                    structured_output_prompt=prompts.assessment_request(
                        assessment_tool=ASSESSMENT_TOOL
                    ),
                    limits={"turns": MAX_TURNS},
                )
        # Any model failure becomes the fallback, logged by kind and never by content.
        except Exception as error:  # noqa: BLE001
            self._logger.warning(
                "agent.model_failed", call_id=str(call.call_id), failure=type(error).__name__
            )
            return None

        assessment = result.structured_output
        if not isinstance(assessment, CallAssessment):
            self._logger.warning(
                "agent.no_assessment", call_id=str(call.call_id), stop_reason=result.stop_reason
            )
            return None
        return assessment.to_proposal()
