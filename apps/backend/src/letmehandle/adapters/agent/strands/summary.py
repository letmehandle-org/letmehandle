"""The call summariser's model, on the Strands Agents SDK (D-026).

One SDK agent per summary, with no tools and nothing shared with any other call: it is asked for
one structured answer and thrown away. What it writes is checked twice. Here, for shape, strictly,
so an answer that is cut off, uses a word outside a set, or carries a field nothing reads is refused
and the SDK hands the model the reason to correct. Then in the application, for truth and quality,
where a draft that fails is replaced by the fallback rather than argued with.

The instructions are the system prompt. The call and what was said on it are a message of their
own, delimited as data, and nothing from the call is written into the instructions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Final

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictStr
from strands import Agent
from strands.agent.conversation_manager import NullConversationManager

from letmehandle.adapters.agent.strands.assessment import one_of
from letmehandle.application.agent.tools.arguments import SHORT_TEXT_CHARACTERS
from letmehandle.application.agent.tools.outcome import MAX_DETAILS
from letmehandle.application.calls.prompts import SUMMARY_PROMPT_VERSION, load_summary_prompts
from letmehandle.application.calls.summariser import (
    DetailKind,
    DraftDetail,
    SummaryDraft,
    SummaryDrafter,
    SummaryNotWrittenError,
)
from letmehandle.domain.models.intent import CallIntent
from letmehandle.domain.models.summary import MAX_HEADLINE_CHARACTERS, CallOutcome

if TYPE_CHECKING:
    from strands.models.model import Model

    from letmehandle.application.calls.summariser import SummaryRequest

# One turn to answer, one for a model that has to be asked for the answer, and one to correct an
# answer the schema refused. A model still going after that is not going to write a summary.
MAX_TURNS: Final = 3


def _says_something(value: str) -> str:
    # Refused where the model can still hear why, rather than later, where it cannot.
    if not value.strip():
        raise ValueError("must not be blank")
    return value


type _Kind = Annotated[DetailKind, one_of([kind.value for kind in DetailKind])]
type _Intent = Annotated[CallIntent, one_of([intent.value for intent in CallIntent])]
type _Outcome = Annotated[CallOutcome, one_of([outcome.value for outcome in CallOutcome])]
type _Text = Annotated[StrictStr, AfterValidator(_says_something)]


class SummaryDetail(BaseModel):
    """One fact from the call, with the exact words it came from."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: _Kind
    value: _Text = Field(max_length=SHORT_TEXT_CHARACTERS)
    evidence: _Text = Field(max_length=SHORT_TEXT_CHARACTERS)


class CallSummaryAnswer(BaseModel):
    """The summary of the call. Write it once."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    headline: _Text = Field(max_length=MAX_HEADLINE_CHARACTERS)
    intent: _Intent
    outcome: _Outcome
    details: tuple[SummaryDetail, ...] = Field(default=(), max_length=MAX_DETAILS)

    def to_draft(self) -> SummaryDraft:
        """The answer as the application checks it."""
        return SummaryDraft(
            headline=self.headline,
            intent=self.intent,
            outcome=self.outcome,
            details=tuple(
                DraftDetail(kind=each.kind, value=each.value, evidence=each.evidence)
                for each in self.details
            ),
        )


# The name the model knows the answer by, which is the name the SDK gives its tool.
ANSWER_TOOL: Final = CallSummaryAnswer.__name__


class StrandsSummaryDrafter(SummaryDrafter):
    """Asks a model for a summary through the SDK's structured output."""

    def __init__(self, model: Model, *, prompt_version: str = SUMMARY_PROMPT_VERSION) -> None:
        self._model = model
        self._prompt_version = prompt_version

    async def draft(self, request: SummaryRequest) -> SummaryDraft:
        """The model's answer, or `SummaryNotWrittenError` when it finished without a valid one."""
        prompts = load_summary_prompts(request.locale, self._prompt_version)
        agent = Agent(
            model=self._model,
            tools=[],
            system_prompt=prompts.instructions_prompt(answer_tool=ANSWER_TOOL),
            # The default handler prints what the model streams, which quotes the call, to stdout.
            callback_handler=None,
            # Trimming would silently summarise part of a call; an overflow is a failure instead.
            conversation_manager=NullConversationManager(),
            # Retries belong inside the summariser's bound, and the default backs off for minutes.
            retry_strategy=None,
        )
        result = await agent.invoke_async(
            prompts.call_message(request),
            structured_output_model=CallSummaryAnswer,
            structured_output_prompt=prompts.answer_request(answer_tool=ANSWER_TOOL),
            limits={"turns": MAX_TURNS},
        )
        answer = result.structured_output
        if not isinstance(answer, CallSummaryAnswer):
            raise SummaryNotWrittenError(f"the model stopped with {result.stop_reason}")
        return answer.to_draft()
