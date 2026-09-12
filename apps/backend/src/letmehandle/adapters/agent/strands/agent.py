"""The call agent, on the Strands Agents SDK (D-026).

One SDK agent per judgement, built from the model it is handed, the versioned prompts and the
application's tools, and thrown away afterwards: a judgement shares no conversation, no tool state
and no lock with any other call. The tools are built afresh for each judgement too, around notes
of its own, and what the judgement reports as refused and whether the call ended is read from
those notes and nowhere else.

What the model is given is split along the one line that matters. The instructions and the user's
preferences are the system prompt. The caller's words are a message of their own, delimited and
labelled as a record of what was said. Nothing from the call is ever written into the instructions.

The model proposes; it does not decide. Its assessment is validated into a proposal, and the
proposal goes through the escalation check this agent is handed — the same check whether or not the
model asked for the user, so a model that forgot to still cannot skip an escalation the user's rules
require.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Final, Protocol

from strands import Agent
from strands.agent.conversation_manager import NullConversationManager
from strands.tools.executors import SequentialToolExecutor

from letmehandle.adapters.agent.strands.assessment import CallAssessment
from letmehandle.adapters.agent.strands.tools import ToolLedger, present
from letmehandle.application.agent.notes import JudgementNotes
from letmehandle.application.agent.ports import AgentJudgement, CallAgent
from letmehandle.application.agent.prompts import PROMPT_VERSION, load_prompts
from letmehandle.domain.models.intent import CallImportance, CallIntent
from letmehandle.domain.policy.escalation import EscalationProposal
from letmehandle.observability.logging import get_logger

if TYPE_CHECKING:
    from datetime import timedelta

    from strands.models.model import Model

    from letmehandle.application.agent.ports import CallSoFar
    from letmehandle.application.agent.prompts import Prompts
    from letmehandle.application.agent.tool import ToolsForAJudgement
    from letmehandle.domain.models.escalation import EscalationDecision

# The name the model knows the assessment by, which is the name the SDK gives its tool.
ASSESSMENT_TOOL: Final = CallAssessment.__name__

# How many model turns one judgement may take: one per tool the plan gives the agent, one for the
# assessment, and two for a model that has to be asked for it again. A model still going after that
# is looping, and the bound on time would otherwise be the only thing to stop it.
MAX_TURNS: Final = 8


class ConsiderEscalation(Protocol):
    """The end-of-turn escalation check this agent is handed."""

    async def __call__(self, call: CallSoFar, proposal: EscalationProposal) -> EscalationDecision:
        """Whether the user is needed, given what the model proposed. The policy decides."""


def fallback_proposal() -> EscalationProposal:
    """What a judgement proposes when the model gave no usable answer.

    An honest "not understood": `understood=False` and `CallIntent.UNDETERMINED`, because nothing
    was, and at `CallImportance.IGNORABLE`, because a failure must never be worth more to a caller
    than a real judgement would have been.

    The importance is the choice that matters, and the alternatives are worse in both directions. A
    high one means a model that is down, or a caller who has found words that break it, rings the
    user's phone on every call — through quiet hours, and past the rule that never puts a suspected
    fraudster through, because a broken model has suspected nobody. The user's own threshold is the
    same mistake, since a call at the threshold always clears it. The lowest level grants
    nothing: the policy still records that the caller could not be understood, a contact the user
    marked as important still reaches them because that rule never consulted the model, and a user
    whose threshold is the lowest level has asked to be reached for everything and is.
    """
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
        consider: ConsiderEscalation,
        timeout: timedelta,
        prompt_version: str = PROMPT_VERSION,
    ) -> None:
        if timeout.total_seconds() <= 0:
            raise ValueError("a judgement needs time to happen in")
        self._model = model
        self._tools = tools
        self._consider = consider
        self._timeout = timeout
        self._prompt_version = prompt_version
        self._logger = get_logger(__name__)

    async def judge(self, call: CallSoFar) -> AgentJudgement:
        """Run the model, then the escalation check on whatever it proposed.

        Raises only for a tool that raised. That is a defect, or orchestration failing to act, and
        either is somebody else's to handle — the model misbehaving is this method's to absorb.
        """
        prompts = load_prompts(call.preferences.locale, self._prompt_version)
        ledger = ToolLedger(JudgementNotes())
        agent = Agent(
            model=self._model,
            tools=[present(tool, call, ledger) for tool in self._tools(ledger.notes)],
            system_prompt=prompts.system_prompt(call.preferences, assessment_tool=ASSESSMENT_TOOL),
            # The default handler prints what the model streams, which is the call, to stdout.
            callback_handler=None,
            # The default manager trims history to fit, which would silently drop the start of a
            # call. An overflow is a failure the fallback handles, not something to paper over.
            conversation_manager=NullConversationManager(),
            # One at a time, so refusals are recorded, and actions taken, in the order asked for.
            tool_executor=SequentialToolExecutor(),
            # Retries belong inside the time bound, and the default backs off for minutes.
            retry_strategy=None,
        )
        proposal = await self._assess(agent, prompts, call)
        if ledger.failure is not None:
            raise ledger.failure
        return AgentJudgement(
            proposal=proposal,
            escalation=await self._consider(call, proposal),
            refusals=ledger.notes.refusals,
            ended=ledger.notes.ended,
        )

    async def _assess(self, agent: Agent, prompts: Prompts, call: CallSoFar) -> EscalationProposal:
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
        # Deliberately broad: the port promises a judgement whatever the model does, and a model
        # fails in more ways than any list here would name. Recorded, by kind and never by content,
        # because the content is somebody's call.
        except Exception as error:  # noqa: BLE001
            self._logger.warning(
                "agent.model_failed", call_id=str(call.call_id), failure=type(error).__name__
            )
            return fallback_proposal()

        assessment = result.structured_output
        if not isinstance(assessment, CallAssessment):
            self._logger.warning(
                "agent.no_assessment", call_id=str(call.call_id), stop_reason=result.stop_reason
            )
            return fallback_proposal()
        return assessment.to_proposal()
