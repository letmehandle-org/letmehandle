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
any → FAILED
```

- **Routing.** Deterministic rules from Phase 3 decide pass-through, assistant, or rejection
  before the assistant is engaged. A known important caller never waits for a model.
- **Escalation.** The agent's request is executed: the human is dialled and bridged, the
  notification is dispatched, and the assistant continues per policy while the phone rings.
- **De-escalation.** If the human does not answer, or declines, the assistant resumes with
  the outcome, rather than the call dying.

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
  response, provider calls. A timeout is a transition, not an exception that escapes.
- **Partial provider failure.** Speech fails mid-call; telephony rejects a bridge;
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
10. Transcripts are encrypted at rest and purged on schedule.
11. Coverage meets the D-020 floors.

## Risks and open questions

- **The temptation to spread state.** Convenience will suggest letting the agent or an
  adapter nudge call state directly. It is the failure this phase exists to prevent; the
  orchestrator is the only writer, and a test asserts no other module imports the state
  mutator.
- **Concurrency tests that pass by luck.** Repetition and deterministic scheduling where
  possible; any test that has ever flaked is treated as a defect in the code until proven
  otherwise.
