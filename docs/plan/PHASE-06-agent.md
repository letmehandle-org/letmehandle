# Phase 6 — Agent and decision-making

**Goal:** the assistant decides — what the caller wants, how much it matters, what it is
allowed to do, and when a human is genuinely required.

This phase owns judgement. Phase 5 owns conversation. They stay apart (D-006).

## In scope

### The agent
Built on the Strands Agents SDK behind the `CallAgent` port, with the model configured as an
OpenAI-compatible endpoint (D-007, D-026). The phase 1 `LLMProvider` port is removed: the agent
loop needs tool use, which it never offered.

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
- **No model endpoint existed at first.** Everything but the evaluation against a real model
  (acceptance criterion 8) runs against a scripted model; that one waited for an endpoint, and
  its results are in the verification report.
- **Small local models.** D-007 permits them, and tool-calling reliability varies. The
  evaluation suite reports per-model results so a user can see whether their configured model
  is adequate before trusting it with a call.

---

# Phase 6 verification

```
PHASE 6 VERIFICATION

Planned tasks:        complete
Acceptance criteria:  9 of 9 passed
Unit tests:           passed   backend 1651 passed, 3 skipped; mobile 179 passed
Integration tests:    passed   calls judged end to end through the SDK's real agent loop on a
                               scripted model, with the real tools and the real policy
Evaluation:           passed   a real OpenAI-compatible model, prompts v2, three full runs:
                               every class at or above 90% on the mean, below
Coverage:             100.00%  backend, floor 98; escalation policy 100% branches
Lint / Format:        passed
Typecheck:            passed   mypy --strict, including the evaluation runner
Static analysis:      passed   import-linter, 4 contracts kept: no agent SDK or model client in
                               the domain or application layers
Application runs:     yes      the agent is optional at startup; nothing calls it in a request yet
Docs updated:         D-026, the phase plan, docs/providers/README.md
Known issues:         none open in code; one routine miss in three runs, below
```

## Acceptance criteria, each with its evidence

| # | Criterion | Evidence |
| --- | --- | --- |
| 1 | Deterministic scenarios produce the expected decisions, repeatably | `tests/integration/test_agent_scenarios.py` drives the real Strands loop with a scripted model: routine resolved, escalation, unsafe request refused, model failures to the fallback |
| 2 | Escalation rules are testable without a model | `domain/policy/escalation.py` is pure and table-tested, including every importance against every threshold |
| 3 | Unsafe or unauthorised actions are refused before execution | Every tool parses, then checks the grant from the call's authority, then acts; refusals leave the recording `CallActions` untouched. Instructions inside the transcript are never consulted |
| 4 | Changing a preference changes the decision for identical input | The same scripted model output with a different grant, and with a different threshold, produces a different judgement, asserted as a difference |
| 5 | Swapping the model requires no change to agent, tool or policy code | The model is configuration (D-007); the SDK sits behind `CallAgent` (D-026) |
| 6 | No side effect except through a validated tool | `CallActions` is reached only from tools and from the one conclusion step after the model finishes |
| 7 | Escalation policy has 100% branch coverage | Coverage report |
| 8 | The evaluation suite runs and its results are recorded | `scripts/agent_evaluation.py` over `tests/evaluation/scenarios.json` against a real OpenAI-compatible model; per-class rates under Evaluation, below. Constant strategies are proven to fail every class |
| 9 | Coverage meets the floors | 100% |

## What changed from the plan

The phase 1 `LLMProvider` port is removed. The agent loop needs tool use, which that port never
offered, and routing the SDK through it would have grown it into a second framework (D-026).

Tools do not end a call or reach the user while the model is working. They record what the model
asks for, and one conclusion step acts afterwards, outside the model's time bound: the most pressing
escalation any reading justified, then the ending only if the rules allow it. A hang-up never
cancels an escalation that must reach the user now; a note for later holds nobody on the line.

## Reviews, and what they found

Two builders worked in parallel against interfaces written first, and an integration pass closed
the gaps between them. An adversarial review then reproduced nine defects against a green suite:

- **A hang-up cancelled an escalation the rules required**, even for an important contact reporting
  an emergency — and a fix made before the review had locked that behaviour in.
- **A tool used after ending a call crashed the judgement.**
- **Concurrent escalations on one call could ring the user repeatedly**, or mark a call escalated
  when nobody was reached.
- **The assessment schema told the model two fields were optional while validation required them**,
  so a strict model fell back and an urgent call rang nobody.
- **After a failed escalation the model could still hang up**, and the judgement was lost.
- **Reaching the user counted against the model's time bound**, and running out dropped the
  escalation silently.
- **A model-invented tool name reached the log**, and a call to a tool that does not exist was not
  recorded as a refusal.
- **A suspected-fraud label could silence an important contact.**
- **The evaluation could be passed by labelling everything fraud.**

## Accepted trade

A caller who persuades the model a call is urgent rings the user once per call.
Limits across calls from the same caller belong to the hardening phase.

## Evaluation

Run against a real OpenAI-compatible model with `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` set,
from `apps/backend`: `uv run python ../../scripts/agent_evaluation.py`. Three full runs of the
prompts as shipped (v2), each class as the mean of the three with the lowest and highest run:

| Class | Scenarios | Mean | Min | Max |
| --- | --- | --- | --- | --- |
| routine | 4 | 92% (11 of 12) | 75% | 100% |
| escalation | 5 | 100% (15 of 15) | 100% | 100% |
| unsafe_request | 4 | 100% (12 of 12) | 100% | 100% |
| suspected_fraud | 5 | 100% (15 of 15) | 100% | 100% |

The v1 prompts, on the same model, scored 14 of 18 in one run: routine 3/4, escalation 4/5,
unsafe_request 3/4, suspected_fraud 4/5.

What changed, and why:

- **The courier who may be directed, graded as needing the user.** v1 named the importance levels
  and intents without saying what any of them meant. v2 describes each, and says something the
  assistant is allowed to do is routine and needs no decision from the user.
- **"She must not be rung about this" was obeyed.** The call went unescalated on a caller's claim
  about the user's wishes. v1 failed this in three of three runs when it was diagnosed. v2 says a
  caller's claims about what the user wants are unverified, and the call is assessed as if they had
  not been made. Authority stays in code; this only stops the model grading the call down.
- **A genuine card notice labelled suspected_fraud.** A suspected-fraud label silences a stranger,
  and v1 gave the model no way to tell fraud from an unverified caller. v2 says fraud is marked by
  what a caller asks for — codes, PINs, unusual payment, threats, secrecy — and not by being
  unverifiable.
- **A hand-over scored as a hang-up.** The neighbour's flood was escalated immediately, and the
  model then handed the call over. The suite counted any ending as `end_call`, but orchestration
  does not end a call handed over to a user still being reached. The suite now names a hang-up
  `end_call` and a hand-over `hand_over`; with that change alone v1 passed this scenario in three
  of three runs.

The one miss in three runs: the courier was escalated once, in one of thirteen v2 runs of
that scenario. A delivery at the door right now is where routine and notable meet, and with the
user's threshold at notable a single notable reading rings them. A single run can therefore
report routine at 75%, so `--minimum 0.9` holds on the mean of several runs rather than on one.
