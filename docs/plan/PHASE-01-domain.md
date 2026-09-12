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

---

# Phase 1 verification

```
PHASE 1 VERIFICATION

Planned tasks:        complete
Acceptance criteria:  10/10 passed
Unit tests:           passed   484 passed, 3 skipped (uv run pytest)
Integration tests:    passed   included above: import boundaries, and the architectural
                               check that the domain names no provider or platform
Contract tests:       passed   every port's suite, run against in-memory implementations.
                               The three skips are capability-gated: a screening-only
                               transport skips the audio and bridging behaviours, which is
                               the suite working as designed rather than coverage missing
E2E tests:            not applicable — nothing is wired to anything yet
Coverage:             100.00%  domain layer, 802 statements, 112 branches, no partials
                      100.00%  backend overall, floor 98
Lint:                 passed   ruff check
Format:               passed   ruff format --check
Typecheck:            passed   mypy --strict, 75 source files
Static analysis:      passed   import-linter, 3 contracts kept
Build:                passed   nothing new to build; the application still starts
Application runs:     yes      unchanged from phase 0; the domain is not yet wired in
Manual verification:  none required. There is no runtime behaviour in this phase: every
                      claim it makes is a claim about types and rules, and each is asserted
Docs updated:         docs/providers/README.md now lists each port, its interface and its
                      contract suite; the decision record already carried D-004 and D-005
Known issues:         none
Commits created:      9
```

## Acceptance criteria, each with its evidence

| # | Criterion | Evidence |
| --- | --- | --- |
| 1 | The domain imports no vendor SDK, framework or driver | import-linter contract, proven to fail when one is added (`test_import_boundaries.py`) |
| 2 | Every port has a contract suite, passing against a fake | `tests/contracts/`, seven ports, run against in-memory implementations that really are bounded, interruptible and closeable |
| 3 | Every legal and illegal transition is covered | The full cartesian product of states, not a hand-written list: 100 pairs, each asserted to move or to raise naming both states |
| 4 | An illegal transition produces a typed error naming both states | `IllegalTransitionError` carries `current` and `requested` as attributes |
| 5 | Escalation decisions are structured and constructible only with a reason | `EscalationDecision` refuses both halves of the mistake: escalating without a reason, and not escalating with one |
| 6 | Domain coverage is 100% branch | 802 statements, 112 branches, zero partial |
| 7 | No port signature mentions a vendor, a codec, a webhook or an HTTP status | Asserted by the architectural test over every domain module, with two exemptions each recorded with its reason |
| 8 | Adding a `CallState` without declaring its transitions fails | `test_every_state_declares_its_transitions` |
| 9 | `CallTransport` declares the full capability set, each operation gated by it | Six capabilities; operations that depend on one are unreachable without narrowing |
| 10 | No domain module refers to a transport by name | `test_domain_names_no_provider.py`, which also proves its own search works |

## Decisions made while building, and why

- **Capability gating is two mechanisms, not one.** The static half is that an operation
  depending on a capability does not exist on `CallTransport`: it lives on a protocol reached
  through `screening`, `audio_streaming` or `bridging`, so a caller that has not checked cannot
  name the method. The runtime half is the narrowing itself, which catches the one thing types
  cannot — a transport that declares a capability it has not implemented. Both were needed: a
  fake that lied about bridging was written specifically to prove the second.
- **The state machine forbids self-transitions.** Idempotency belongs to the orchestrator, but
  a state machine that permits a move to the state it is already in makes a duplicated provider
  callback look like a real second transition. The mistake is not made available.
- **`PhoneNumber.__str__` is masked.** Every accidental disclosure of a number that this
  project has to worry about arrives through an f-string in a log line. Reading the real value
  is `.value`, which is a deliberate act a reviewer can see.
- **Three-way state needed no special case.** The user is a participant with a role, so a call
  with a caller, an agent and a human is the ordinary shape of the aggregate rather than a
  branch in it.
- **Two justified exemptions to the no-provider-names rule**, both recorded in the test rather
  than quietly excluded: the wire format the LLM port describes is named after the service that
  first published it, and naming it is what lets a self-hoster point at their own server; and a
  device genuinely belongs to a platform, which is a fact about the device rather than the
  domain branching on a supplier.

## What was found and fixed

- **`stream_audio` was declared as a coroutine returning an iterator**, which would have made
  every implementer write an async generator and every caller await before iterating. Found by
  the type checker against the contract suite, before any adapter existed to inherit it.
- **Three modules named a platform or a provider**, including the transport port's own
  docstring. The architectural test caught them on its first run, which is the argument for
  writing it in the same phase as the rule it enforces.
