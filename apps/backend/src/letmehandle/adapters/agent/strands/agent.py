"""The call agent, on the Strands Agents SDK (D-026).

One SDK agent per judgement, built from the model it is handed, the versioned prompts and the
application's tools, and thrown away afterwards: a judgement shares no conversation, no tool state
and no lock with any other call. The tools are built afresh for each judgement too, around notes
of its own, and what the judgement reports as refused, and what it asked for, is read from those
notes and nowhere else.

What the model is given is split along the one line that matters. The instructions and the user's
preferences are the system prompt. The caller's words are a message of their own, delimited and
labelled as a record of what was said. Nothing from the call is ever written into the instructions.

The model proposes; it does not decide. Its assessment is validated into a proposal, and once the
model has finished — outside the bound on its time, which is for model turns and not for reaching
the user — the conclusion this agent is handed acts on that proposal and on everything the model
asked for along the way: the escalation first, then an ending if the rules still allow one. A model
that forgot to ask for the user still cannot skip an escalation the user's rules require, and a
model that hung up first cannot cancel one.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Final

from strands import Agent
from strands.agent.conversation_manager import NullConversationManager
from strands.tools.executors import SequentialToolExecutor

from letmehandle.adapters.agent.strands.assessment import CallAssessment
from letmehandle.adapters.agent.strands.tools import ToolLedger, UnknownToolRefusals, present
from letmehandle.application.agent.conclusion import (
    NOT_ENDED_AFTER_A_FAILURE,
    NOT_ENDED_WITHOUT_AN_ASSESSMENT,
)
from letmehandle.application.agent.notes import JudgementNotes
from letmehandle.application.agent.ports import CallAgent
from letmehandle.application.agent.prompts import load_prompts
from letmehandle.domain.models.intent import CallImportance, CallIntent
from letmehandle.domain.policy.escalation import EscalationProposal
from letmehandle.observability.logging import get_logger

if TYPE_CHECKING:
    from datetime import timedelta

    from strands.models.model import Model

    from letmehandle.application.agent.conclusion import JudgementConclusion
    from letmehandle.application.agent.ports import AgentJudgement, CallSoFar
    from letmehandle.application.agent.prompts import Prompts
    from letmehandle.application.agent.tool import ToolsForAJudgement

# The name the model knows the assessment by, which is the name the SDK gives its tool.
ASSESSMENT_TOOL: Final = CallAssessment.__name__

# How many model turns one judgement may take: one per tool the plan gives the agent, one for the
# assessment, and two for a model that has to be asked for it again. A model still going after that
# is looping, and the bound on time would otherwise be the only thing to stop it.
MAX_TURNS: Final = 8


def fallback_proposal() -> EscalationProposal:
    """What a judgement proposes when the model gave no usable answer.

    An honest "not understood": `understood=False` and `CallIntent.UNDETERMINED`, because nothing
    was, and at `CallImportance.IGNORABLE`, because a failure must never be worth more to a caller
    than a real judgement would have been.

    The importance is the choice that matters, and the alternatives are worse in both directions. A
    high one means a model that is down, or a caller who has found words that break it, rings the
    user's phone on every call — outside their active hours, and past the rule that never puts a
    suspected fraudster through, because a broken model has suspected nobody. The user's own
    threshold is the same mistake, since a call at the threshold always clears it. The lowest level
    grants nothing: the policy still records that the caller could not be understood, and a user
    whose threshold is the lowest level has asked to be reached for everything and is.

    A contact the user marked as important still reaches them on the fallback, because who they are
    comes from the number the user trusts and not from the model, and not understanding them is a
    reason. The policy's fraud rule does not apply to them either, so a model that mistakes them for
    a scammer does not silence them.
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
        """Run the model, then act on what it asked for and concluded.

        Raises for a tool that raised, once the escalation the rules require has still been made,
        and for a failure to reach the user. Either is a defect or orchestration failing to act,
        and somebody else's to handle — the model misbehaving is this method's to absorb.
        """
        prompts = load_prompts(call.preferences.locale)
        ledger = ToolLedger(JudgementNotes())
        agent = Agent(
            model=self._model,
            tools=[present(tool, call, ledger) for tool in self._tools(ledger.notes)],
            hooks=[UnknownToolRefusals(ledger)],
            system_prompt=prompts.system_prompt(
                call.preferences, call.authority, assessment_tool=ASSESSMENT_TOOL
            ),
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
            # After the escalation, never instead of it: the user's rules still apply to a call on
            # which something broke. A failure to reach the user as well stays attached as context.
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
        # Deliberately broad: the port promises a judgement whatever the model does, and a model
        # fails in more ways than any list here would name. Recorded, by kind and never by content,
        # because the content is somebody's call.
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
