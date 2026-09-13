# Phase 14 — Full system end to end

**Goal:** the scenarios that define the product are automated, and they pass.

## In scope

### Harness — `tests/e2e/`
- The whole system under test: backend, database, and controllable stand-ins for every
  external provider, driving real code paths through real ports.
- The streaming transport is driven by the callback simulator from phase 7, extended to
  reproduce duplication, reordering, delay and mid-call disconnection.
- The native transport is driven by an instrumented Android harness and, for the orchestration
  scenarios, by a fake declaring the same capability set.
- Speech is driven by a scripted provider producing deterministic turns and injectable
  failures.
- The LLM is scripted for logic scenarios. A separate, non-blocking run exercises a real
  model for behavioural confidence.
- Every scenario asserts the full outcome: final state, participants over time, the summary
  produced, the notifications dispatched, and that no resource leaked.

### Scenarios
Each is automated, named, and independently runnable.

- **A — Routine commercial call.** The assistant handles it completely. No escalation. A
  summary is generated with the correct intent and outcome.
- **B — Delivery driver needing the user.** The assistant starts the call, determines a human
  is required, escalation fires, the handset rings, the notification carries correct context,
  the human answers and joins the existing call, all three parties behave correctly, the call
  ends, the summary records that a human joined and when.
- **C — Important known caller.** Routing passes the call straight to the user with no
  assistant involvement and no model invocation. Asserted by the model never being called.
- **D — Unsafe request.** The caller asks for something outside the assistant's authority,
  including an attempt to instruct the assistant directly. The action is refused before
  execution and the call escalates or ends per policy.
- **E — Speech provider failure.** Failure at connect, mid-utterance, and during
  reconnection. Each degrades as documented; the caller is not left in silence; resources are
  released.
- **F — Duplicated telephony callback.** Every callback type delivered twice, and out of
  order. State is unchanged by the duplicate and correct despite the reordering.
- **G — User does not answer the escalation.** Ring times out. The assistant resumes and
  concludes the call; the summary records the unanswered escalation.
- **H — Caller hangs up during escalation.** The ring is cancelled, the bridge is abandoned,
  everything is cleaned up, and the user is not left with a phone ringing for a call that no
  longer exists.

### Transport-specific scenarios

Every scenario above runs against each transport capability set that can express it. These are
additionally specific to one:

- **T1 — Native screening, allowed.** A call is screened before ringing, the rules allow it, the
  handset rings natively, and the activity records the decision.
- **T2 — Native screening, rejected.** The rules reject it, the handset never rings, and the
  rejection appears in activity with its reason.
- **T3 — Native screening, silenced.** The call is silenced rather than rejected and is
  recorded as such.
- **T4 — Native screening under a deadline.** The decision is returned within the platform's
  window; a slow rule path degrades to the safe default rather than missing the deadline.
- **T5 — Native, role withdrawn.** The screening role is revoked while the product is running.
  Degradation is graceful, stated in the interface, and recorded.
- **T6 — Streaming, full escalation.** Scenario B end to end on the streaming transport: agent
  conversation, escalation, ring, notification, human joins the live call, three parties
  behave, summary records the join.
- **T7 — Capability mismatch.** Escalation is requested on a transport that cannot bridge. The
  transition is unreachable rather than failing, and the non-bridging handoff is taken instead.

Additional scenarios covering combinations that the earlier phases identified as risky:
escalation requested twice; notification failure with a successful escalation; restart
mid-call; LLM unavailable at the decision point.

### Manual verification
A documented script for what automation cannot reach: a real inbound call, a real escalation
to a real handset, and both push platforms on physical devices. Executed and recorded in the
verification report.

## Explicitly out of scope

- Load and performance testing.
- Chaos testing beyond the injected failures listed.
- Testing provider internals.

## Tests required

Every scenario above, automated, deterministic, and run in CI with no vendor account. A
scenario that cannot be automated is stated as such with its reason and moved to the manual
script — it is not quietly dropped.

Each scenario also asserts the negative: no leaked task, connection or timer at the end, and
no sensitive data in anything the run emitted.

## Acceptance criteria

1. Scenarios A through H are automated and pass.
2. Transport scenarios T1 through T7 are automated and pass.
3. Every scenario that can run against both capability sets does so.
2. The additional combination scenarios are automated and pass.
3. Every scenario asserts final state, summary, notifications, and resource cleanup.
4. The suite runs in CI with no vendor credentials.
5. The suite is deterministic. A flaking scenario is treated as a defect in the system, not
   in the test, until proven otherwise.
6. The manual script is executed and recorded.
7. No scenario emits sensitive data.
8. Coverage meets the D-020 floors.

## Risks and open questions

- **Simulator divergence.** Stand-ins may drift from real provider behaviour. Mitigated by
  the Phase 1 contract suites: simulator and real adapter pass the same suite, so divergence
  shows up as a contract failure rather than a production surprise.
- **Flakiness.** An end-to-end suite with timing in it is the natural home of flaky tests. The
  rule is that a flake is a defect until proven otherwise, and it is investigated rather than
  retried.

## Verification report

```
PHASE 14 VERIFICATION

Planned tasks:        automated harness and scenarios complete; manual script written, not run
Scenarios:            A–H, T1, T2, T6, T7 and the risky combinations pass (make e2e)
                      T3 records a silenced report; the handset rules no longer choose silence
                      T4 covered by the JVM deadline tests only; T5 held for a device
Coverage:             100.00%  backend; e2e runs inside make test and make coverage
Stability:            the e2e suite ran fifteen times in a row without a failure
Real model run:       held     no model endpoint configured
Manual verification:  held     docs/testing/manual-verification.md, needs a number and devices
Defects found:        three, each fixed with the scenario that found it
```
