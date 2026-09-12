# Phase 6 — Agent and decision-making

**Goal:** the assistant decides — what the caller wants, how much it matters, what it is
allowed to do, and when a human is genuinely required.

This phase owns judgement. Phase 5 owns conversation. They stay apart (D-006).

## In scope

### The agent
Built on the agent framework, configured with an OpenAI-compatible LLM provider (D-007).

Responsibilities:
- Consume the conversation so far and the normalised preference context from Phase 3.
- Classify intent into `CallIntent`.
- Grade importance into `CallImportance`.
- Decide whether it may act, given `AgentAuthority`.
- Produce an `EscalationDecision` with a structured reason.
- Produce a `CallSummary` at the end.

Every one of those outputs is a typed structured object, validated on the way out. An
unparseable or invalid model response is a handled failure with a defined fallback, never a
crash and never a silently coerced value.

### Tools
Small, explicit, individually tested, each doing one thing:

- `get_user_preferences` — read-only, returns the normalised context.
- `get_caller_context` — what is known about this caller.
- `request_human_escalation` — requires a structured reason and an urgency.
- `record_call_outcome` — writes the structured summary.
- `end_call` — terminates, with a reason.

Tools are the only way the agent affects the world. There is no path from a model response
to a side effect that does not pass through a tool, and every tool validates its arguments
against the domain types before acting.

### Authority enforcement
Authority is checked in code, not in the prompt. A prompt is guidance; a check is a
guarantee, and the difference matters when the input is an unknown caller who may be
adversarial.

- Every tool invocation is checked against `AgentAuthority` before execution.
- A denied action returns a typed refusal the agent can respond to, and is recorded.
- The refusal path is tested with deliberately unsafe requests, including ones phrased as
  instructions to the assistant. Content arriving over a phone call is data, never
  instruction, and the tests assert that.

### Escalation policy
A separate, deterministic component — not the model. It takes importance, authority,
preferences, and call rules, and returns a decision. It is pure, has no I/O, and is fully
table-tested, so escalation behaviour can be reasoned about and changed without touching
the model.

The model proposes. The policy decides.

### Prompts
Versioned templates in files, language-aware (D-017), with no user preference hard-coded
into them. Every preference reaches the prompt through the Phase 3 context builder.

### Evaluation
A scenario suite with fixed inputs and expected decisions, runnable against any configured
model, reporting pass rate per scenario class. It gates changes to prompts and policy: a
prompt edit that lowers the rate fails.

## Explicitly out of scope

- Speech. The agent consumes text and produces decisions.
- Telephony and orchestration. The agent requests escalation; Phase 8 performs it.
- Learning or memory across calls.

## Tests required

| Kind | Must prove |
| --- | --- |
| Unit | Escalation policy, table-driven across importance, authority, rules and hours, including every boundary. 100% branch coverage. |
| Unit | Each tool validates arguments, enforces authority, and is side-effect-free when denied. |
| Unit | Invalid, truncated, and adversarial model output is handled with the defined fallback. |
| Unit | Instructions embedded in caller speech do not cause tool execution. Written as a deliberate attempt to break it. |
| Integration | Deterministic scenarios end to end against a scripted LLM: routine call resolved; escalation requested; unsafe request refused; authority change alters the outcome for otherwise identical input. |
| Evaluation | The scenario suite runs against a real model and its pass rate is recorded in the verification report. |

## Acceptance criteria

1. Deterministic scenarios produce the expected decisions, repeatably.
2. Escalation rules are testable without a model in the loop.
3. Unsafe or unauthorised actions are refused before execution, not after.
4. Changing a user preference materially changes the decision for identical input, proven
   by a test that asserts the difference.
5. Swapping the LLM provider requires no change to agent, tool, or policy code.
6. The agent never reaches a side effect except through a validated tool.
7. Escalation policy has 100% branch coverage.
8. The evaluation suite runs and its results are recorded.
9. Coverage meets the D-020 floors.

## Risks and open questions

- **Prompt injection from the caller.** The caller is an untrusted party speaking directly
  into the model's context. This is the central security property of the phase: authority is
  enforced in code, tools validate their own arguments, and the refusal tests are written
  adversarially. Reviewed again in Phase 12.
- **Non-determinism.** Model output varies. Mitigated by deterministic tests using a
  scripted provider for logic, and a separate evaluation suite with a pass-rate threshold for
  the real model. Logic correctness never depends on a live model.
- **Small local models.** D-007 permits them, and tool-calling reliability varies. The
  evaluation suite reports per-model results so a user can see whether their configured model
  is adequate before trusting it with a call.
