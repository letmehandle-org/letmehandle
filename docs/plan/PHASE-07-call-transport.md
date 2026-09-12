# Phase 7 — Call transport

**Goal:** real calls reach the product on both platforms, each through the transport that
platform actually supports, behind one interface.

Two transports are implemented (D-005). They differ in kind rather than in vendor, which is
why the capability model from phase 1 exists and why neither name appears outside bootstrap.

## In scope

### `TwilioCallTransport` — the streaming path, default on iOS

Capabilities declared: `can_stream_call_audio_to_ai`, `can_inject_ai_audio`,
`can_bridge_human`, `supports_three_way_call`. Not `can_screen_before_ringing`.

- **Call shape (D-027).** Every inbound call is answered into a conference. The assistant joins
  as its own participant, whose leg carries the bidirectional media stream; the user is dialled
  into the same conference. The caller's leg is never redirected after it is answered.
- **Inbound.** A call arrives and is answered under program control.
- **Media.** Bidirectional audio between the call and the phase 5 audio source and sink. This
  is where narrowband call audio meets the speech model's expected rate; conversion happens at
  the adapter's edge and the domain sees only audio frames.
- **Agent audio.** The agent's speech is injected into the live call.
- **Bridging.** `add_participant` dials the user's real phone number and joins them to the
  call already in progress. The caller is never disconnected, never transferred to a new call,
  and never asked to redial. This capability is the reason this transport exists.
- **Three-party state.** Caller, agent and human present simultaneously. Whether the agent
  remains audible after the human joins is policy read from preferences, not a property of the
  transport. The transport offers the four outcomes the conference allows — stay, listen only,
  speak only to the user, leave — and the policy chooses among them.
- **The user's leg.** An unanswered, busy or failed dial, and a voicemail picking up, are
  outcomes the transport reports distinctly; none of them may leave the caller in silence.
- **Webhooks.** Signature verification on every callback, rejecting unverified requests before
  parsing. Idempotency by provider event id — the provider will duplicate, so this is a
  requirement rather than a precaution. Out-of-order and late events are resolved by state,
  not by arrival order. Handlers return promptly and hand work to the orchestrator.

### `AndroidNativeCallTransport` — the on-device path, default on Android

Capabilities declared: `can_screen_before_ringing`, `supports_native_ringing`. Not
`can_stream_call_audio_to_ai`, not `can_inject_ai_audio`, not `can_bridge_human`.

That declaration is the important part of this phase. The platform's call screening gives an
application the caller's identity before the handset rings; it does not give it the audio of a
SIM call. This transport therefore claims no AI conversation, and the product offers none on
this path rather than offering one that cannot work.

- **`CallScreeningService`.** A Kotlin implementation receiving a call before it rings,
  consulting the user's rules through the bridge, and responding: allow, reject, or silence.
- **`InCallService`.** Implemented only where a decision genuinely needs it. It requires the
  default-dialer role, which is a large thing to ask of a user, so it is not taken on for
  convenience.
- **Native call state.** Ringing, active, and ended surfaced as the same domain events the
  streaming transport produces, so the orchestrator sees one vocabulary.
- **The bridge.** A typed Kotlin-to-TypeScript boundary. Screening decisions must be returned
  within the platform's deadline, so the rule evaluation available to it is the deterministic
  one and never a model call.
- **Permissions and roles.** Requested with an explanation, declined gracefully, and the
  product degrades to a documented reduced behaviour rather than breaking.

### Selection

One factory, in bootstrap, choosing a transport from the platform and configuration. Android
defaults to the native transport, iOS to the streaming one; both are overridable by
configuration, because a user who wants AI answering on Android should be able to choose the
streaming path.

Nothing downstream of that factory knows which was chosen.

### Configuration

Numbers, credentials, and account identifiers are environment configuration only. They never
appear in a tracked file, a test fixture, a log line, or a document (D-021).

### Local development

A documented tunnelling setup for real callbacks, and a simulator replaying recorded callback
sequences — duplicates and reordering included — so the full suite runs in CI with no account
and no phone number. An instrumented Android harness for the screening path.

## Explicitly out of scope

- SIP, carrier and IMS transports. Documented extension points, no implementation.
- Outbound calls initiated by the user.
- Call recording (D-013).
- The orchestration state machine. Phase 8 owns it; this phase provides the primitives and a
  thin driver sufficient to prove them.
- Any attempt to capture SIM-call audio on Android.

## Tests required

| Kind | Must prove |
| --- | --- |
| Contract | The phase 1 `CallTransport` suite passes against **both** transports. One suite, two implementations — that is what proves the abstraction holds rather than describing one vendor. |
| Capability | Each transport's declared capabilities match what it can actually do. An operation invoked against a transport that declares it false is unreachable, and the failure names the capability. |
| Unit (streaming) | Signature verification including a tampered payload; idempotency by event id; event-to-domain mapping for every event type; audio conversion round-tripped in both directions. |
| Unit (native) | Screening decisions for allow, reject and silence; the deadline is met; a missing role or permission degrades as documented; native call state maps to the same domain events. |
| Integration | Against the callback simulator: inbound answered, media streamed, participant added and removed, every termination path cleaned up, duplicates inert, reordering resolved. |
| Integration (native) | Instrumented Android tests over the screening service and the bridge. |
| Manual | A real call on each platform. Streaming: pass-through, agent-handled, escalation dialling a real handset and joining the live call. Native: screening before ringing, allow, reject, silence. Recorded in the verification report. |

## Acceptance criteria

1. Both transports implement `CallTransport` and pass the same contract suite.
2. Each declares its capabilities honestly, proven against actual behaviour.
3. The streaming transport answers a call, holds a conversation, dials a human, and joins them
   to the existing call without the caller redialling.
4. The native transport screens a call before the handset rings and can allow, reject or
   silence it.
5. The native transport declares no audio capability, and no code path attempts to obtain
   SIM-call audio.
6. Transport selection happens once, in bootstrap. No module outside it names a transport.
7. Every disconnect path cleans up every resource, per participant, on both transports.
8. Duplicate callbacks are idempotent; unverified webhook requests are rejected.
9. The full automated suite runs in CI with no account and no phone number.
10. No credential, number, or account identifier appears in any tracked file.
11. Coverage meets the D-020 floors.

## Risks and open questions

- **The temptation to branch.** Two transports with genuinely different abilities will invite
  `if this is the native one`. The capability model exists to absorb that, and a test asserts
  no transport name appears outside bootstrap.
- **Android role requirements.** Call screening needs a role the user must grant, and the
  default-dialer role is a larger ask still. What the product does when either is refused is
  designed here, not discovered later.
- **Number provisioning.** A number must exist before the streaming transport's manual
  verification. Operator work, not code; it blocks only the manual criteria.
- **Media latency budget.** Transport time plus the phase 5 speech round trip determines
  whether conversation feels natural. Measured and reported; an unacceptable total is a finding
  for phase 13, not a reason to weaken the architecture.
- **Narrowband audio.** Call audio will degrade recognition relative to the phase 5 harness.
  Measured, reported, treated as a known characteristic.
