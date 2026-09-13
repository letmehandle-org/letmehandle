# Phase 8 — Call orchestration

**Goal:** one component owns a call's life, and it is correct under concurrency, duplication,
failure and disconnection.

This is the most critical code in the project. It is where a bug means a caller hears
silence, a user's phone rings for nothing, or a call never ends.

## In scope

### The orchestrator
A single `CallOrchestrator` composing the ports from Phase 1 and the components from Phases
3 to 7. It owns the state machine and nothing else owns any part of it.

```
RECEIVED → ROUTING → PASSTHROUGH → COMPLETED
RECEIVED → ROUTING → REJECTED
RECEIVED → ROUTING → AGENT_HANDLING → COMPLETED
                   → AGENT_HANDLING → ESCALATION_REQUESTED → HUMAN_RINGING
                                                           → HUMAN_JOINED → COMPLETED
                                                           → AGENT_HANDLING (not reached)
                   → AGENT_HANDLING ← ESCALATION_REQUESTED (the dial refused)
any → FAILED
```

- **Capability-driven routing.** Deterministic rules from phase 3 decide pass-through,
  assistant, or rejection before the assistant is engaged, and the transport's capabilities
  decide which of those outcomes are available at all. A transport that cannot stream audio
  offers no assistant path; a transport that cannot bridge offers no escalation path. The
  routing code reads capabilities and never a transport name.

  There is **one** orchestrator. Not one per transport, not a base class with two subclasses
  that override the interesting parts — those are the same mistake wearing different clothes,
  and both end with business logic duplicated in two places that drift.
- **Escalation.** The agent's request is executed: the human is dialled and bridged, the
  notification is dispatched, and the assistant continues per policy while the phone rings.
- **De-escalation.** If the human does not answer, or declines, the assistant resumes with
  the outcome, rather than the call dying.

### Unavailable transitions are unreachable

A state transition that the configured transport cannot perform must be impossible to attempt,
not merely guarded at the point of use. The available transitions are derived from the
transport's capabilities when the call is created, so escalating on a transport that cannot
bridge is not a runtime error — there is no path to it.

This is the difference between a check that someone can forget and a shape that cannot be
built wrong, and it is why the capability model is worth its cost.

### Correctness properties
Each is a named, individually tested requirement:

- **Idempotency.** Every externally triggered transition carries an event id, and repeating
  it is a no-op. Providers duplicate; this is assumed, not hoped against.
- **Concurrency.** State changes for one call are serialised. Two events arriving together
  cannot interleave into an impossible state. Tested with genuinely concurrent tasks.
- **Race conditions.** Explicitly covered: caller hangs up while the human is ringing; human
  answers as the caller hangs up; escalation requested twice; agent ends the call while
  escalation is in flight; provider callback arrives after teardown.
- **Timeouts.** Every wait is bounded — human ring time, agent decision time, speech
  response, provider calls, and the call itself. A timeout is a transition, not an exception that
  escapes. A call still held when `CALL_MAX_DURATION_SECONDS` (four hours by default) has passed
  is ended as failed, because a report of it ending that never arrives — a handset's app killed or
  offline — cannot be told from a long call; and an account holds at most five live calls, a
  sixth being recorded as failed on arrival, so a handset reporting new calls in a loop holds a
  handful of runs rather than thousands.
- **Partial provider failure.** Speech fails mid-call; the transport rejects a bridge;
  notification fails (D-016: never blocks escalation); LLM is unavailable. Each has a defined
  degraded behaviour, and each is tested.
- **Cleanup.** Every terminal path releases the speech session, the media stream, the call,
  and the timers. There is one teardown routine and every path reaches it.

### Persistence
- Call session state is durable, so a restart mid-call does not lose the record.
- Transcript persistence with the D-014 retention, encrypted at rest, with a purge job that
  is tested and observable.
- The structured summary is written at completion, outliving the transcript.

## Explicitly out of scope

- New product features. This phase composes what exists.
- Mobile changes. Phase 9.
- Horizontal scaling. If the concurrency design later needs a distributed lock, that is an
  adapter behind an interface, decided when the requirement is real.

## Tests required

| Kind | Must prove |
| --- | --- |
| Unit | Every transition, legal and illegal. Every timeout. Every partial-failure branch. 100% branch coverage, no exceptions. |
| Concurrency | Interleaved events on one call produce a legal state, run repeatedly to catch ordering flakiness. Each named race above has its own test. |
| Idempotency | Every externally triggered transition, replayed, changes nothing. |
| Integration | Full flows against fakes for every port: pass-through; assistant-only; escalation answered; escalation unanswered; caller hangs up mid-escalation; speech failure mid-call; notification failure with escalation still succeeding. |
| Integration | Restart mid-call: state is recovered or the call is failed cleanly, never left dangling. |
| Property | Random legal event sequences never reach an undefined state and always terminate. |
| Capability | The full orchestrator suite runs against **each** transport capability set. A transport that cannot stream never enters the agent path; one that cannot bridge never enters escalation; neither produces an error, because neither transition exists. |
| Static | No module under the orchestrator names a transport. Asserted by a test, because this is the rule convenience breaks first. |
| Data | Transcripts are encrypted at rest; the purge deletes exactly the expired rows and nothing else. |

## Acceptance criteria

1. Every state transition in the diagram is implemented and tested.
2. Branch coverage of the orchestrator is 100%.
3. Every named race condition has a test that fails without its fix.
4. Duplicate events are provably idempotent.
5. Every wait is bounded and every timeout is a defined transition.
6. Every partial provider failure has a defined, tested degraded behaviour.
7. Notification failure never prevents or delays an escalation.
8. Every terminal path releases every resource, proven by assertion.
9. A restart mid-call leaves no call in an indeterminate state.
10. There is one orchestrator, and no transport-specific business logic anywhere.
11. No `if transport is X` comparison exists outside bootstrap.
12. A transition the configured transport cannot perform is unreachable rather than rejected.
10. Transcripts are encrypted at rest and purged on schedule.
11. Coverage meets the D-020 floors.

## Risks and open questions

- **The temptation to spread state.** Convenience will suggest letting the agent or an
  adapter nudge call state directly. It is the failure this phase exists to prevent; the
  orchestrator is the only writer, and a test asserts no other module imports the state
  mutator.
- **Two transports, one state machine.** The risk is a state machine shaped around the more
  capable transport, with the other bolted on. Mitigated by running the whole suite against
  each capability set from the first test, rather than adding the second one later.
- **Concurrency tests that pass by luck.** Repetition and deterministic scheduling where
  possible; any test that has ever flaked is treated as a defect in the code until proven
  otherwise.

### Known limits

Accepted for now, and recorded so they are not mistaken for behaviour anybody chose:

- **Recovery assumes one instance.** Starting ends every call storage holds as unfinished, which
  is right only when the starting process is the only one. During a rolling deploy a new instance
  would fail the calls an old one is still carrying, and end them at their transport.
- **The notification is sent before the dial.** An escalation starts the notification and then
  dials, so a user can be told about a ring that the transport then refuses. D-016 makes the
  notification safe to arrive without a ring, but the user is still told of one that never came.

## Verification report

```
PHASE 8 VERIFICATION

Planned tasks:        complete, except real calls on a provisioned number
Acceptance criteria:  all automated criteria passed; real-call confirmation held
Unit tests:           passed   make verify: backend 3087 passed, 14 skipped
Integration tests:    passed   orchestrated calls over the simulated provider and PostgreSQL
E2E tests:            passed   make e2e, whose scenarios run the orchestrator end to end
Concurrency:          passed   each named race repeated; orchestration suites run 10 times clean
Property:             passed   seeded random event sequences per capability set, all terminal
Coverage:             100.00%  backend, including 100% branches in application/orchestration
Lint / Format:        passed   ruff, prettier, eslint
Typecheck:            passed   mypy --strict, tsc
Static analysis:      passed   import-linter; one-writer test for call state; transport-name test
Application runs:     yes      the lifespan starts the orchestrator after the transport and stops
                               it first; startup refuses a transport without storage and keys
Docs updated:         D-029 amended, D-033, D-014 amended, this plan's diagram and known limits
Known issues:         see known limits under Risks
```

## Acceptance criteria, each with its evidence

| # | Criterion | Evidence |
| --- | --- | --- |
| 1 | Every transition implemented and tested | the assistant, edge and every-line orchestration tests; domain transition tables |
| 2 | 100% branch coverage of the orchestrator | coverage report |
| 3 | Every named race has a test that fails without its fix | `test_races_and_repeats.py`; the defects an adversarial review reproduced each have a test watched failing first |
| 4 | Duplicate events idempotent | a whole call with every event doubled records the same states and requests |
| 5 | Every wait bounded, every timeout a transition | ring, judgement, speech open, provider calls, storage, summary, shutdown |
| 6 | Partial provider failures degrade as defined | speech fails, will not open or close; dial refused; model unavailable; storage refused |
| 7 | Notification failure never delays escalation | notification storage down, held or raising, the ring proceeds |
| 8 | Every terminal path releases every resource | leftover tasks, runs, sockets and legs counted after each test |
| 9 | Restart leaves no call indeterminate | recovery ends unfinished calls at the provider and records them failed |
| 10 | One orchestrator, no transport-specific logic | transport-name test; both capability sets run through the same suites |
| 11 | No transport comparison outside bootstrap | transport-name test |
| 12 | An impossible transition is unreachable | no escalation step exists on a plan that cannot bridge |

## Reviews, and what they found

An adversarial review reproduced six defects against a green suite, and whole-system scenarios
found three more. Among them: a speech session failing to close stopped teardown halfway; a
restart left the previous process's calls up at the provider; handing over while the user's
phone rang silenced the caller and a busy user meant the caller was hung up on; a write was
acknowledged before it committed; an important contact's call was recorded as from an unknown
caller. Each is fixed with a test that failed first.
