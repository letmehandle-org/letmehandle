# Phase 7 — Call transport

**Goal:** real calls reach the product on both platforms, each through the transport that
platform actually supports, behind one interface.

Two transports are implemented (D-005). They differ in kind rather than in vendor, which is
why the capability model from phase 1 exists and why neither name appears outside bootstrap.

## In scope

### `TwilioCallTransport` — the streaming path, default on iOS

Capabilities declared: `can_answer_under_program_control`, `can_stream_call_audio_to_ai`,
`can_inject_ai_audio`, `can_bridge_human`, `supports_three_way_call`. Not
`can_screen_before_ringing`.

- **Call shape (D-027).** Every inbound call is answered into a conference. The assistant joins
  as its own participant, whose leg carries the bidirectional media stream; the user is dialled
  into the same conference. The caller's leg is never redirected after it is answered.
- **Inbound.** A call arrives and is answered into its conference. Answering under program
  control, on the port, brings the assistant into that conference.
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
`can_answer_under_program_control`, not `can_stream_call_audio_to_ai`, not `can_inject_ai_audio`,
not `can_bridge_human`.

That declaration is the important part of this phase. The platform's call screening gives an
application the caller's identity before the handset rings; it does not give it the audio of a
SIM call. This transport therefore claims no AI conversation, and the product offers none on
this path rather than offering one that cannot work.

**Where the decision is made (D-028).** Android gives a screening service five seconds from
`onScreenCall` to respond, after which it ignores the response and rings. A decision cannot make
a round trip to the backend inside that, so it is made on the handset: the app keeps a snapshot
of the user's deterministic call rules, rewritten whenever the preferences change, and the
screening service evaluates it locally. The backend holds `AndroidNativeCallTransport`, which
represents the handset as a transport whose events arrive afterwards over an authenticated API.
That adapter is what the Python contract suite runs against; the same properties are proven on
the handset by the Kotlin and TypeScript suites.

- **`CallScreeningService`.** A Kotlin implementation, bound through the call-screening role,
  that reads the caller's number and presentation, evaluates the rules snapshot through a pure
  rule evaluator, and responds within a three-second budget: allow, reject, or silence. When the
  budget runs out, evaluation fails, there is no snapshot, or the snapshot is older than seven
  days or in a format the build does not read, the call rings. A caller is never refused on
  rules the handset does not have or cannot vouch for.
- **What the platform does not show a screening service.** Callers in the user's contacts
  (unless the app holds the contacts permission, which it does not ask for) and callers who
  withhold their number. Both always ring on this path, so the anonymous posture cannot be
  applied here. The handset does not classify callers: it applies important contacts by number
  and treats everybody else as `unknown`. `handle_with_agent` rings, because this path has no
  assistant to hand a call to. Quiet hours silence a call that would otherwise ring.
- **`InCallService`.** Not implemented. It requires the default-dialer role, which means
  replacing the phone app, and no decision this phase needs depends on it: screening and the
  call state below cover allow, reject, silence and incoming, answered and ended.
- **Native call state.** Observed without the dialer role through the phone-state broadcast,
  which Android still delivers to a manifest receiver and which needs only `READ_PHONE_STATE`.
  It carries no number and no identifier, so it is joined to the screening decision that
  preceded it. Reported as the shared vocabulary: incoming (with the decision), answered, ended.
  Not visible: a call waiting behind one in progress, and anything beyond the decision itself
  when the permission is refused.
- **Reporting.** The handset keeps unreported events durably and the app sends them to
  `POST /v1/calls/reports` whenever it runs. Reports are stored per user, idempotent by the
  handset's event id, scoped so one account's handset can never speak for another's call, and a
  report arriving after its call ended is stored and not replayed.
- **The bridge.** A TurboModule with string payloads whose documents — the snapshot in, call
  reports out — have one definition each side and one shared set of examples both test suites
  read.
- **Permissions and roles.** Requested with an explanation, declined gracefully. Without the
  role nothing is screened and every call rings as it would without the product; without the
  phone-state permission screening still works and only the decision is reported.

### Selection

One factory, in bootstrap, choosing a transport from configuration: `TELEPHONY_PROVIDER` is
`twilio` for the streaming transport, `android_native` for the on-device one, or empty for none.
The application builds one on-device transport and hands the same instance to the report route's
container and to the factory, so the transport chosen is the feed the handset's reports reach.
The report route is mounted whichever is chosen, and reports are stored either way.

The choice is per deployment in this phase. Choosing per user — Android defaulting to the native
transport, iOS to the streaming one, and a user who wants AI answering on Android choosing the
streaming path — needs one consumer reading both feeds, which is the phase 8 orchestrator's to
build.

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
| Unit (native) | Screening decisions for allow, reject and silence; the deadline is met, with the call ringing when it is not; a missing or stale snapshot rings; a missing role or permission degrades as documented; native call state maps to the same domain events. Kotlin on the JVM and TypeScript under Jest. |
| Integration | Against the callback simulator: inbound answered, media streamed, participant added and removed, every termination path cleaned up, duplicates inert, reordering resolved. |
| Integration (native) | Instrumented Android tests over the screening service and the bridge; the backend's report ingestion against a real database: authentication, isolation between users, idempotency, event mapping. |
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

## Verification report

```
PHASE 7 VERIFICATION

Planned tasks:        complete, except the calls made by hand on real numbers and handsets
Acceptance criteria:  9 of 11 passed; 3 and 4 held on a real call, each proven against simulators
Unit tests:           passed   backend 2020 passed, 14 skipped (make verify); mobile 233 passed;
                               Android JVM 84 passed (./gradlew :app:testDebugUnitTest)
Integration tests:    passed   whole calls replayed against the simulated provider, duplicated,
                               reordered and delivered concurrently; call reports against Postgres
Contract tests:       passed   one CallTransport suite, run against both transports
Instrumented tests:   held     CallScreeningInstrumentedTest is written; not run in this report
Coverage:             100.00%  backend, floor 98
Lint / Format:        passed   ruff, prettier, eslint
Typecheck:            passed   mypy --strict, tsc
Static analysis:      passed   import-linter contracts kept; the transport-names test
Build:                passed   backend image, debug APK
Application runs:     yes      with no transport, with the handset transport, and with the
                               streaming transport against the simulated provider
Docs updated:         D-027, D-028, docs/providers/call-transport.md, docs/providers/README.md
Known issues:         none open in code; criteria 3 and 4 held, below
Commits:              62 on the branch, one change each
```

## Acceptance criteria, each with its evidence

| # | Criterion | Evidence |
| --- | --- | --- |
| 1 | Both transports implement `CallTransport` and pass one contract suite | `tests/contracts/call_transport.py`, run by `test_twilio_call_transport_contract.py` and `test_android_native_transport_contract.py` |
| 2 | Capabilities declared honestly | The contract suite invokes every operation; one a transport declares unsupported fails naming the capability |
| 3 | The streaming transport answers, converses, dials a human and joins them | Proven against the simulated provider in `tests/integration/test_twilio_call_flow.py`. **Held** on a real call: no number is provisioned |
| 4 | The native transport screens before ringing and can allow, reject or silence | Kotlin tests over the rules, the deadline and the service. **Held** on a real handset and on the instrumented run |
| 5 | The native transport declares no audio and nothing obtains call audio | Capabilities false; a test refuses a recording permission in the merged manifest |
| 6 | Selection happens once, in bootstrap | `test_transport_names_stay_in_bootstrap.py`, case-insensitive and substring-matched |
| 7 | Every disconnect path cleans up, per participant | Termination under the call's lock, every step attempted; shutdown ends live calls; the caller's own dial ending ends the call |
| 8 | Duplicates idempotent; unverified webhooks rejected | Event-id idempotency; signature verification with tampered and repeated parameters; one-time stream tokens |
| 9 | The suite runs in CI with no account or number | The simulated provider; CI green |
| 10 | No credential, number or account identifier tracked | Disclosure audit in `make verify` and CI; fixtures from ranges reserved for fiction |
| 11 | Coverage meets the floors | 100% |

## What changed from the plan

A streaming call is a conference from the moment it is answered (D-027), so bringing the user in
adds a leg rather than moving a call. A handset decides its own screening from a snapshot of the
rules and reports what it decided (D-028); the backend is told, never asked, because it cannot
answer inside the ringing deadline. Choosing a transport per user is left to the orchestrator.

## Reviews, and what they found

An adversarial review reproduced sixteen defects against a green suite, all fixed with a test
that failed first. Among them: a call ended while a dial was in flight left that leg ringing; a
provider refusal during termination left the call held forever; a media stream could attach to
any leg that guessed its path; a user who answered could be reported unreachable; one invalid
report refused a whole batch, and a handset then resent it forever; an unreadable stored event
stopped a handset recording any later one.
