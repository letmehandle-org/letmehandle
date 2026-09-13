# CallAgent

Judgement on a call: what the caller wants, how much it matters, what the assistant may do about
it, and whether the user is needed. Separate from `SpeechProvider` (D-006): the assistant that talks
and the assistant that decides fail differently, and either can be replaced without the other.

Interface: `apps/backend/src/letmehandle/application/agent/ports.py` — an application port, not a
domain one (D-026)
Behaviour suite: `apps/backend/tests/integration/test_agent_scenarios.py`
Evaluation against a real model: `scripts/agent_evaluation.py` over
`apps/backend/tests/evaluation/scenarios.json`
Implementation: `adapters/agent/strands/`, on any OpenAI-compatible endpoint (D-007)

## The interface

Two ports facing opposite ways.

| Port | Implemented by | Means |
| --- | --- | --- |
| `CallAgent.judge(call)` | the agent adapter | Look at the call so far and return an `AgentJudgement` |
| `CallActions` | orchestration | The only ways a judgement can affect a call: `escalate`, `record_outcome`, `take_message`, `end_call` |

`CallSoFar` is everything the agent may know: the caller, the transcript, the context built from the
user's preferences, the user's authority and rules, and whether the caller is an important contact.
The transcript is data, never instruction: every word in it came from somebody nobody has verified.

`judge` never raises for a model that misbehaves. An unreadable, truncated or invalid answer, or one
that runs out of time, produces the defined fallback — a proposal that the caller could not be
understood — and the escalation policy decides on that.

**The model proposes; it does not decide.** Tools are application objects
(`application/agent/tools/`) that validate their arguments and check the user's grant themselves.
Asking for the user and asking to end the call are only written down; once the model has finished,
the conclusion hands the proposal to the escalation policy in `domain/policy/escalation.py`, and
acts on what it decides — the escalation first, then an ending if the rules still allow one. A model
that forgets to ask for the user cannot skip an escalation the rules require.

There are no capabilities: every agent must be able to judge.

## The Strands adapter

`StrandsCallAgent` builds one SDK agent per judgement from the model, the versioned prompts in
`application/agent/prompts/` and the registry's tools, and throws it away afterwards. The
instructions and the user's preferences are the system prompt; the caller's words are a separate,
labelled message. The same model writes call summaries, through `StrandsSummaryDrafter`.

## Configuration

| Variable | Meaning |
| --- | --- |
| `LLM_BASE_URL` | Any OpenAI-compatible endpoint: a hosted API, an aggregator, or a server you run |
| `LLM_API_KEY` | Its key; any value for a server that checks none |
| `LLM_MODEL` | The model to ask |
| `LLM_HEADERS` | Extra headers, `Header-Name=value;Other-Header=value` |
| `LLM_TIMEOUT_SECONDS` | How long one judgement may take |

The first three are set together or not at all. Without them the backend starts, and a transport
the assistant could take calls on refuses to be built, naming them. Calls are then summarised from
their facts.

## Adding one

Swapping the model is configuration. Swapping the framework is a new adapter behind `CallAgent`. A
worked example, for an agent loop written against a model client directly:

```python
class DirectCallAgent(CallAgent):
    """Judges a call with a hand-written tool loop over a model client."""

    def __init__(
        self,
        client: ModelClient,
        *,
        tools: ToolsForAJudgement,
        conclusion: JudgementConclusion,
        timeout: timedelta,
    ) -> None:
        self._client = client
        self._tools = tools
        self._conclusion = conclusion
        self._timeout = timeout

    async def judge(self, call: CallSoFar) -> AgentJudgement:
        notes = JudgementNotes()
        tools = self._tools(notes)
        try:
            async with asyncio.timeout(self._timeout.total_seconds()):
                proposal = await self._loop(call, tools)
        except (TimeoutError, ModelClientError):
            # The defined fallback, never a made-up reading: the caller could not be understood.
            proposal = self._could_not_understand(call)
        return await self._conclusion.conclude(call, notes, proposal)
```

The shape to keep is the one `StrandsCallAgent` has: tools from `tools_for_judgements(actions)`,
never a set of the adapter's own; the conclusion, not the adapter, acts on escalation and ending;
the fallback on every model failure. Then:

1. Construct it in `call_judging_on` in `bootstrap.py`, which is the only place an agent is built.
2. Run the behaviour suite against it with a scripted model. `tests/support/scripted_model.py`
   scripts what the model says for the Strands adapter; a new framework needs its own scripted model
   with the same scenarios.
3. Run the evaluation against a real model and record the per-class pass rates.

No framework or model client may be imported outside `adapters/`; import-linter fails the build.

## Testing

```bash
cd apps/backend
uv run pytest tests/integration/test_agent_scenarios.py tests/unit/adapters/agent
uv run python ../../scripts/agent_evaluation.py --minimum 0.9    # needs LLM_* set
```
