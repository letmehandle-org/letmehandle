# Phase 7 — Telephony provider

**Goal:** a real inbound call reaches the assistant, and a real human can be pulled into
that same live call without the caller redialling.

## In scope

### The adapter
The first `TelephonyProvider` implementation (D-005), entirely within `adapters/telephony/`.

- **Inbound.** A call arrives and is answered under program control.
- **Media.** Bidirectional audio streams between the call and the `SpeechProvider` session.
  This is where 8 kHz telephony audio meets the speech model's expected rate; conversion
  happens at the adapter edge and the domain sees only audio frames.
- **Pass-through.** A call routed to the user directly, with no assistant involvement.
- **Bridging.** `add_participant` dials the user's real phone number and joins them to the
  call that is already in progress. The caller is never disconnected, never transferred to a
  new call, and never asked to redial. This is the capability the whole product rests on and
  the reason this adapter was chosen.
- **Three-party state.** Caller, assistant and human can be present simultaneously. Whether
  the assistant remains audible after the human joins is policy, read from preferences, not
  a property of the adapter.
- **Termination and cleanup.** Any participant leaving is handled: caller hangs up, human
  hangs up, human never answers, assistant fails. Every path releases the media stream, the
  speech session, and the call resources.

### Webhooks
- Signature verification on every inbound callback, rejecting unverified requests before any
  parsing. Verification is not optional and not configurable off.
- Idempotency by provider event id. A duplicate delivery is recognised and ignored; the
  provider will duplicate, so this is a requirement rather than a precaution.
- Out-of-order and late events are handled by state, not by arrival order.
- Callbacks return promptly and hand work to the orchestrator; no long processing inside a
  webhook handler.

### Configuration
Numbers, credentials, and account identifiers are environment configuration only. They never
appear in a tracked file, a test fixture, a log line, or a document (D-021). `.env.example`
names the variables with empty values.

### Local development
A documented tunnelling setup so a developer can receive real callbacks locally, and a
simulator that replays recorded callback sequences — including duplicates and out-of-order
delivery — so the full suite runs in CI with no account and no phone number.

## Explicitly out of scope

- Outbound calls initiated by the user.
- Any second telephony adapter. Native dialer, SIP and carrier paths remain documented
  interfaces with no implementation (D-005).
- Call recording (D-013).
- The orchestration state machine. Phase 8 owns it; Phase 7 provides the primitives and a
  thin driver sufficient to prove them.

## Tests required

| Kind | Must prove |
| --- | --- |
| Unit | Webhook signature verification, including rejection of a tampered payload; idempotency by event id; event-to-domain mapping for every event type; audio conversion in both directions, round-tripped. |
| Contract | The Phase 1 `TelephonyProvider` suite passes against the real adapter, including its capability declarations. |
| Integration | Against the callback simulator: inbound answered; media streamed; participant added and removed; each termination path cleans up; duplicate callbacks change nothing; out-of-order delivery resolves correctly. |
| Manual | A real call from a real phone: pass-through works; assistant-handled works; escalation dials a real handset and joins it to the live call; the caller hears continuity throughout. Recorded in the verification report. |

## Acceptance criteria

1. An inbound call enters the system and is answered.
2. The pass-through path connects caller to user with no assistant involvement.
3. The assistant-handled path holds a spoken conversation with the caller.
4. Escalation dials the user's real phone number.
5. The human joins the call that already exists.
6. The caller is never asked to redial and is never dropped during escalation.
7. Every disconnect path cleans up every resource, proven for each participant.
8. Duplicate callbacks are idempotent.
9. Unverified webhook requests are rejected.
10. The full automated suite runs in CI with no telephony account.
11. No credential, number, or account identifier appears in any tracked file.
12. Coverage meets the D-020 floors.

## Risks and open questions

- **Number provisioning is a prerequisite.** A number must exist before manual verification.
  It is operator work, not code, and it blocks only the manual criteria.
- **Media latency budget.** Telephony transport plus speech round trip determines whether the
  conversation feels natural. Phase 5's baseline plus this phase's transport time is measured
  and reported; if the total is unacceptable, that is a finding for Phase 13, not a reason to
  weaken the architecture.
- **Audio quality at 8 kHz.** Narrowband telephony audio will degrade recognition relative to
  the Phase 5 harness. Measured, reported, and treated as a known characteristic.
