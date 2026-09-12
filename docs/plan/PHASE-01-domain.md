# Phase 1 — Domain model and core contracts

**Goal:** the product expressed in types and rules, with no knowledge of any vendor.

This is the phase that decides whether the rest of the project stays replaceable. Every
later phase is an adapter plugged into what is defined here.

## In scope

### Domain types — `src/letmehandle/domain/`

Modelled as immutable value objects and explicit enumerations. No type carries a database
id as its identity concept, and no type carries a provider-specific field.

- `User` — identity, locale, the phone number that is the account key.
- `UserPreferences` — the normalised container for everything Phase 3 collects.
- `CallRules` — the deterministic rules evaluated before the agent is involved: known
  callers, blocked categories, working hours, quiet hours.
- `Caller` — what is known about the other party at the point of decision. Explicitly
  models "unknown", because unknown is the common case and must not be a null.
- `CallSession` — the aggregate root. Owns state, participants, and the transcript buffer.
- `CallState` — the enumeration and its legal transitions.
- `CallIntent` — what the caller wants, as classified.
- `CallImportance` — the graded judgement that feeds escalation.
- `EscalationDecision` — whether a human is required, why, and with what urgency. Carries
  the reason as structured data, not prose, so it can be asserted in tests and rendered in
  the app.
- `AgentAuthority` — what the assistant is permitted to do on this user's behalf. A
  capability set, not a boolean.
- `CallSummary` — the durable outcome record.
- `CallOutcome`, `Participant`, `ParticipantRole`.

### State machine

`CallState` transitions are declared in one table and enforced in one place:

```
RECEIVED → ROUTING
ROUTING  → PASSTHROUGH | AGENT_HANDLING | REJECTED
AGENT_HANDLING → ESCALATION_REQUESTED | COMPLETED | FAILED
ESCALATION_REQUESTED → HUMAN_RINGING | AGENT_HANDLING | COMPLETED
HUMAN_RINGING → HUMAN_JOINED | AGENT_HANDLING | COMPLETED
HUMAN_JOINED → COMPLETED
PASSTHROUGH → COMPLETED
any → FAILED
```

Terminal states accept no transition. An illegal transition raises a typed domain error
naming both states; it never silently no-ops and never logs-and-continues.

### Ports — `src/letmehandle/domain/ports/`

Abstract interfaces with no implementation and no vendor type in any signature. Audio
crosses these boundaries as a domain-owned frame type carrying its encoding and sample
rate, so an adapter converts at the edge rather than leaking a codec into the core.

**`SpeechProvider`** — `connect`, `send_audio`, `receive_events`, `update_context`,
`interrupt`, `close`, `capabilities`. Modelled as an async session object with an explicit
lifecycle, so that closing it is a call rather than a hope. Events are a closed union:
audio out, transcript fragment, speech started, speech ended, error.

**`CallTransport`** — the port by which a call exists at all (D-004). Named for what it is
rather than for a vendor, because a programmable telephony account and a platform's own call
screening service are the same concern served two ways.

Operations: `observe_incoming`, `screen`, `answer`, `stream_audio`, `inject_audio`,
`add_participant`, `remove_participant`, `terminate`, `capabilities`.

Transports differ in kind, so the capability model is what callers actually depend on:

```
can_screen_before_ringing     the transport sees a call before the handset rings
can_stream_call_audio_to_ai   the caller's audio can reach the speech session
can_inject_ai_audio           the agent's audio can reach the caller
can_bridge_human              a third party can be added to the call that already exists
supports_three_way_call       caller, human and agent can be present at once
supports_native_ringing       the platform's own ringing and call UI are used
```

Every operation is reachable only through the capability that permits it. A caller that has
not checked a capability cannot invoke the operation it guards — that is a property of the
types, not a runtime check, so a transport can never be asked for something it does not have.

No domain code branches on which transport is configured. If something needs to know, the
answer is a capability, not a name.

**`LLMProvider`** — `complete`, `complete_structured`, `capabilities`. Structured output is
a first-class method, because every decision the agent makes is a typed object and parsing
prose into one is where reliability goes to die.

**`NotificationProvider`** — `send`, `capabilities`. Delivery is best-effort by D-016 and
the return type says so: it reports an outcome, and no caller may treat failure as fatal.

**`VoiceProvider`** — `list_voices`, `preview`, `resolve_voice`, and the capability flags in
D-009. `resolve_voice` implements the fallback chain, in the domain, once.

**`OTPProvider`**, **`Clock`**, **`IdGenerator`** — small ports so that time and identity are
injected. No domain code calls `datetime.now()` or generates a uuid inline; every test can
therefore control both.

### Contract test suite — `tests/contracts/`

One parametrised suite per port, expressing what any implementation must satisfy. A new
adapter proves itself by passing the existing suite rather than by shipping bespoke tests.
Run in Phase 1 against in-memory fakes, and against every real adapter from Phase 5 on.

## Explicitly out of scope

- Any adapter. No AWS, no telephony vendor, no HTTP client.
- Persistence. Repository interfaces are defined; no implementation, no table, no migration.
- The orchestrator. Phase 8 composes these pieces; Phase 1 only defines them.
- Prompt text. The agent's prompts are Phase 6.

## Tests required

| Kind | Must prove |
| --- | --- |
| Unit | Every legal transition is accepted; every illegal transition raises, named; terminal states are terminal. Table-driven over the full cartesian product of states, so a new state cannot be added without deciding its transitions. |
| Unit | Value objects reject invalid construction. An escalation decision cannot exist without a reason; authority cannot be widened by mutation; a caller is either identified or explicitly unknown. |
| Unit | `resolve_voice` returns the right voice at each step of the fallback chain, including when every step fails. |
| Contract | Each port's suite passes against an in-memory fake, including the failure paths: a speech session that errors mid-stream still closes; a transport that cannot bridge is rejected at the capability check rather than at the call. |
| Unit | Capability gating: for each capability, a fake transport declaring it false makes the guarded operation unreachable, and the failure is typed and names the capability. |
| Static | import-linter proves `domain/` imports nothing from `adapters/`, `api/`, or any third-party SDK. |

## Acceptance criteria

1. `domain/` has no import of any vendor SDK, HTTP framework, or database driver, proven
   by a failing build when one is added.
2. Every port has a contract suite, and every suite passes against a fake.
3. Every legal and illegal state transition is covered by an explicit test.
4. An illegal transition produces a typed error naming both states.
5. Escalation decisions are structured, not prose, and are constructible only with a reason.
6. Domain coverage is 100% branch.
7. No port signature mentions a vendor concept, a codec name, a webhook, or an HTTP status.
8. Adding a new `CallState` without declaring its transitions fails a test.
9. `CallTransport` declares the full capability set, and every operation is gated by the
   capability that permits it.
10. No domain module refers to a transport by name. Asserted by a test that greps the domain
    layer for provider names, because this is the rule most likely to be broken by
    convenience and the one that costs most to undo.

## Risks and open questions

- **Designing ports before an adapter exists.** The usual failure is inventing a shape no
  real provider fits. Mitigation: each port is validated against the documented API of at
  least two real providers before it is accepted, and the contract suite is written from
  the interface rather than from the first implementation.
- **Audio frame modelling.** Telephony commonly delivers 8 kHz mu-law while speech models
  commonly want 16 kHz PCM. The frame type carries encoding and rate and conversion lives in
  the adapter; if that proves wrong, the decision to fix is a resampling port, not a leak.
