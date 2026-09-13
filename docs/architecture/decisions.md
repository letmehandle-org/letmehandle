# Decision record

Every entry here is a decision that is expensive to reverse. Phase plans cite these by id
rather than restating them, so a decision changes in one place.

Status values: `accepted`, `superseded by D-0NN`, `revisit at phase N`.

---

## D-001 — Monorepo, two applications, no meta-runner

**Accepted.** `apps/backend` (Python) and `apps/mobile` (React Native) in one repository.
pnpm workspaces for JavaScript, uv for Python, and a root `Makefile` as the single entry
point (`make setup`, `make verify`, `make test`).

No Turborepo or Nx. With two applications a task graph is configuration to maintain rather
than time saved. Revisit if `packages/` ever holds more than two members.

## D-002 — `packages/` stays empty until something is genuinely shared

**Accepted.** A package is created when a second consumer exists, not in anticipation of
one. The first expected member is a TypeScript client generated from the backend's OpenAPI
schema, which lands when the mobile app first calls an authenticated endpoint (phase 2).
Generated, never hand-written: a hand-maintained mirror of a schema drifts silently.

## D-003 — Hexagonal boundaries: domain, ports, adapters

**Accepted.** The domain layer expresses the product. It imports no vendor SDK, no HTTP
framework, and no database driver. Every outside capability is reached through a port
(an explicit interface) implemented by an adapter under `adapters/`.

This is enforced mechanically, not by convention: an import-linter contract fails the
build if a vendor package is reachable from domain code. A rule with no test is a comment.

## D-004 — `CallTransport` is a port; no transport mechanism is part of the domain

**Accepted.** The port is named `CallTransport` rather than `TelephonyProvider`, because a
telephony vendor is one way a call can reach the product and not the general case. The
domain knows that a call exists, that it may be screened, that audio may flow in and out,
that a third party may be added, and that the call ends. It does not know whether that is
served by programmable telephony, a native platform dialer, SIP, or a carrier integration.

Consequences that follow from this and are binding:

- Call forwarding, number provisioning, webhook shapes, and platform service classes are
  adapter concerns. They appear in no domain type and in no core mobile state.
- Platform limitations are capability flags on the transport, never branches in domain
  logic. There is no `if transport == "twilio"` anywhere outside bootstrap.
- A future carrier, IMS, or SIP transport must be addable without editing the domain.

## D-005 — Two transports are implemented, chosen by capability, not by name

**Superseded the single-adapter decision.** Transports differ in kind, not only in vendor,
so one implementation cannot represent the product:

| Transport | Where it is the default | What it can do |
| --- | --- | --- |
| `AndroidNativeCallTransport` | Android | screen before ringing, allow, reject, silence, read native call state |
| `TwilioCallTransport` | iOS | stream call audio, inject agent audio, dial a human, bridge into the live call |

Selection happens once, in bootstrap or a factory, from the platform and the configuration.
Core logic never learns which was chosen; it asks what the transport can do.

Transports declare, at minimum:

```
can_screen_before_ringing     can_stream_call_audio_to_ai    can_inject_ai_audio
can_bridge_human              supports_three_way_call        supports_native_ringing
```

A transport declares a capability only where the platform genuinely provides it. Android's
call screening does not give an application the audio of a SIM call, so
`AndroidNativeCallTransport` declares `can_stream_call_audio_to_ai` false — and the product
therefore offers no AI conversation on that path, rather than offering one that cannot work.

SIP, carrier and IMS transports remain documented extension points with no implementation.
Half-built adapters are worse than absent ones: they imply a capability that is not there.

## D-006 — Realtime speech and decision-making are separate ports

**Accepted.** `SpeechProvider` owns the conversation: streaming audio in, audio out,
interruption, session context. `LLMProvider` owns judgement: intent, importance, whether
the agent may act, whether a human is required.

Keeping these together would mean the model that talks is also the model that decides what
the assistant is permitted to do. Those have different failure modes and different
substitution needs, and the authority decision must remain auditable independently of the
audio stream.

## D-007 — The primary LLM abstraction is an OpenAI-compatible endpoint

**Accepted.** Configuration is `base_url`, `api_key`, `model`, and optional headers. That
one shape covers hosted APIs, aggregators, dedicated inference providers, and locally run
servers, which means a self-hoster needs no code change and no vendor account to run this
project.

Vendor-native SDK adapters are optional implementations of the same port, never the
default path.

## D-008 — Realtime speech: one protocol, no vendor

**Amended.** The first `SpeechProvider` implementation speaks the OpenAI Realtime-compatible
websocket protocol — audio in, audio out and events over one connection — against an endpoint
supplied by configuration. No vendor is chosen. A hosted service or a self-run server that speaks
the protocol is a configuration change; a service speaking a different protocol is a second
adapter behind the same port.

A protocol rather than a vendor for two reasons. The conversation is speech to speech, so
turn-taking and barge-in are the model service's job rather than a pipeline this project would
have to own and tune. And the endpoint may not exist yet: the service is expected to be chosen or
built later, and an adapter written against one vendor's SDK would have to be rewritten when it is.

A second adapter speaks the ElevenLabs Agents protocol, and `SPEECH_PROVIDER` chooses between
them. It is the same port with a different honest answer to one question: that service does not
resume a dropped conversation, so reconnecting starts a new conversation whose prompt carries the
instructions and a bounded recent history. The realtime protocol restores the same things into a
fresh session. Neither hides the difference from the caller's side of the port, and both run the
same contract suite.

Supported languages, voices, audio formats and whether it supports barge-in are declared as
capabilities rather than assumed by callers. Because a compatible server decides its own voices,
the voice catalogue is configuration, never a list written into the adapter.

## D-009 — Voice selection is capability-driven; nothing implies a capability it lacks

**Accepted.** `VoiceProvider` is separate from `SpeechProvider` and declares:
`supports_builtin_voices`, `supports_voice_preview`, `supports_custom_voice`,
`supports_voice_cloning`, `supports_local_inference`, `supports_realtime_streaming`.

The mobile UI renders from those flags. If the configured provider cannot clone a voice,
the training flow is not rendered at all — not disabled, not "coming soon", not present.

Resolution order when selecting the voice for a call:

```
configured cloned voice → selected persona voice → provider default voice
```

Each step falls through on unavailability, so a revoked or failed custom voice degrades to
a working call rather than a failed one.

No cloning-capable provider ships in the first release. The interface and the fallback
chain do, so adding one later is an adapter and a config value.

## D-010 — Identity is the phone number; OTP delivery is a port

**Accepted.** The phone number is the product's primary key in the real world, so it is the
account identity. `OTPProvider` has a mock implementation that is the default in
development and test, so no contributor needs a paid account to run the suite or sign in
locally. A mock OTP provider refuses to start when the application is configured for
production.

## D-011 — Authentication is self-hosted

**Accepted.** Tokens are issued and verified by this application against its own database.
No managed identity service is required to run the project. Hosted identity may later be
added behind the same port; it is never the only path.

## D-012 — User-scoped single tenancy

**Accepted.** One deployment serves many users. Every personal resource carries `user_id`
and isolation is enforced in the repository layer, with tests that assert one user cannot
read another's rows.

There is no organisation or tenant concept. This is a personal product; teams and shared
assistants are not requirements, and `tenant_id` on every table today would be speculative
structure. Revisit only if a team-shaped requirement actually arrives.

## D-013 — Call audio is never persisted

**Accepted.** Realtime audio is streamed and discarded. No recording is written to disk or
object storage by default or by configuration flag in the first release.

## D-014 — Transcripts: seven day default retention, encrypted at rest, user-configurable

**Accepted.** A transcript is retained so the agent has conversational context, so a user
can check what was said, and so failures are diagnosable. It is encrypted at the
application layer, not merely by disk encryption, so a database dump is not a transcript
dump. A scheduled purge deletes expired rows, and the purge is tested.

Retention is a user-facing setting with a documented floor and ceiling. The structured call
summary is retained separately and outlives the transcript.

*Amended:* who called is sealed alongside transcripts and summaries — the caller's number and
display name on the call record, under the same cipher and key id, bound to the user and the
call. The summary already sealed who the caller was taken to be; a plain column beside it on
the call would leave a dump saying who called whom all the same, and a phone number is as
personal as anything said on the line. The category stays readable, being a classification
rather than an identity, and history decrypts the caller per row.

*Amended:* escalation contexts are sealed the same way. The caller's label, what was established
and what the caller needs are one ciphertext under the transcript keys, bound to the user, the call,
the reason and the moment the escalation was raised; the reason, status, delivery and times stay
readable. Together those words are an account of the call as personal as its transcript, and they
are kept for as long as the call is, so they are held to the same rule. The sealed record in full is
therefore transcripts, summaries, the caller on the call record, and escalation contexts.

## D-015 — Notifications: direct APNs and direct FCM, one adapter each

**Accepted.** Two adapters behind one `NotificationProvider` port. The iOS path does not
route through a third party, which matters both for latency on a time-critical escalation
alert and for the privacy claim this project makes.

## D-016 — The escalation notification supplements the call; it never replaces it

**Accepted.** When the agent needs the human, the authoritative event is the phone ringing
through the telephony adapter. The push notification carries context for that ring.

Therefore: the notification must be safe to arrive before the ring, after the ring, twice,
or never. Delivery failure degrades the experience and must never block or cancel the
escalation itself.

## D-017 — English only, no hard-coded language anywhere

**Accepted.** One shipped locale. Every mobile string resolves through the i18n layer from
the first screen. Agent language is configuration and prompts are language-aware templates.
Speech and voice providers declare supported languages as capabilities. The user profile
carries a preferred locale from the first migration that creates it.

Adding a language must be translation work, never architectural work.

## D-018 — Bare React Native CLI

**Accepted.** `ios/` and `android/` are committed and owned. Native module freedom is a
requirement for a product whose future depends on platform telephony integration.

## D-019 — Branching and history

**Accepted.**

```
feature/* | fix/* | refactor/* | docs/*  →  pull request  →  main
```

`main` is always buildable. No direct commits. Conventional Commits. Small, focused commits
within a branch; the branch is squash-merged, so each pull request is kept to one logical
change and each commit on `main` stays revertible on its own. Releases are git tags cut
from `main`.

## D-020 — Coverage gates

**Accepted.**

| Area | Floor |
| --- | --- |
| Backend | 98% |
| Backend domain and call orchestration | 100% branch, by intent |
| Mobile hooks, state, services, API clients, business logic | 90% |

Excluded from the mobile floor: purely presentational components, generated code, and
platform glue where a unit test asserts the mock rather than the behaviour.

Coverage is a floor, not a goal. A test that exists only to move the number is a defect.

## D-021 — What must never reach a tracked file

**Accepted.** Enforced by `scripts/disclosure_audit.py` and `scripts/pii_audit.sh`, wired
into pre-commit, commit-msg, pre-push, and CI. Layered deliberately: each layer has a
different bypass, and the bypasses do not overlap.

Never tracked, in any file, commit message, pull request title or body:

- Credentials, tokens, API keys, private keys, connection strings.
- Cloud account identifiers, subscription identifiers, provider account identifiers,
  resource names, or any string that identifies a specific deployed resource.
- Personal data of any kind: names, email addresses, phone numbers, postal addresses,
  device identifiers.
- Anything said in a working session that is not a technical requirement.

`.env.example` lists variable names with empty values and a comment for each. It never
carries a real value, including a "harmless" one.

## D-022 — Preferences are stored as one versioned document, not a table per section

**Accepted.** `user_preferences` holds a `JSONB` document and the schema version it was written
in. Onboarding progress is a separate table.

Preferences are read and written whole — the application composes a complete set and saves it —
and they are never queried across users. A dozen joined tables would buy nothing and cost a
migration every time a preference is added, which in a phase that adds preferences is a
migration per week.

The version beside the document is what makes this safe rather than sloppy. Without it, adding a
field leaves every existing row ambiguous: absent because the user declined, or absent because
the field did not exist when they answered. With it, a reader supplies today's defaults for what
the document does not mention and a migration can tell the two apart.

Reading is forgiving and writing is exact. A value this version does not recognise is dropped —
it was written by a newer deployment, and refusing to load somebody's settings over a field they
never set would lock them out of their own account. A value that is *corrupt* is not dropped: a
malformed time or phone number raises, because hours that silently disappear mean a phone
ringing at three in the morning with nothing anywhere to say why.

Onboarding progress is separate because it changes on every step while preferences change
rarely, and because a skipped step has no preferences to write. Keeping them together would mean
writing a whole document to record a tap.

## D-023 — Absent means "leave it"; empty means "clear it"

**Accepted.** Every section of a preferences update is optional. A section the client did not
send is left exactly as it was; a section sent with an empty value is cleared.

Without both, one of two things is impossible: either a screen cannot save one section without
knowing the others, or a user can add an important contact and never remove the last one. The
rule is stated once, in `PreferencesService.apply`, and the API layer does not get to have an
opinion about it.

`PUT /v1/preferences` is the deliberate counterpart: it builds from the defaults, so a section
it does not mention is reset. A caller that means to replace everything says so rather than
relying on having remembered every section.

## D-024 — The routes an application has depend on what its providers can do

**Accepted.** The voice router is built from the configured provider. A provider that does not
declare `preview` leaves the application with no preview route: asking for one is a 404 from the
router, and the path is absent from the generated OpenAPI schema and therefore from the typed
client the mobile app compiles against.

The easier alternative is to register every route always and refuse inside the handler. It fails
in three places at once. The route appears in the schema, so a client generator produces a method
for it; a method that exists gets called; and somewhere a button is drawn for a thing that can
only ever return an error. D-009 already says a capability that is false makes a control absent
rather than disabled — this is the same rule one layer down, applied to the surface the control
is drawn from.

The shipped configuration exercises it immediately. `BuiltInVoiceProvider` declares `preview`
true only when it is constructed with sample audio, and nothing in this phase can synthesise
any: there is no speech provider until the telephony work. So the deployment ships a catalogue
with no samples, declares `preview` false, registers no preview route, generates no client
method, and draws no control. The day a provider ships samples, all five change together and
none of them needs editing.

The cost is that the schema is no longer a single fixed document — two deployments can have
different APIs. That is true of the product either way; this only makes it legible.

## D-025 — A stored choice is a model; what a provider offers is a port

**Accepted.** `VoiceSelection` — the cloned voice and the persona voice a user picked — lives in
`domain/models/`, beside every other preference. The catalogue, the capabilities and the sample
audio live in `domain/ports/voice.py`.

They share a subject and nothing else. One is something the user stored, read back and edited
like a quiet-hours window; the other is a description of an external system that this deployment
happens to be configured with. Keeping the selection in the port made every module that merely
stores a preference import a provider interface, which is how a port stops being a boundary.

The fallback chain — cloned voice, then chosen voice, then the provider's default — stays in the
port's module, because it is the one piece of logic that needs both halves, and it is written
once so that no caller invents its own order. Silence is the outcome it exists to prevent: an
unavailable voice makes a call sound different, never makes it not happen.

## D-026 — The agent runs on Strands, behind an application port

**Accepted.** Judgement on a call — what the caller wants, how much it matters, whether the
assistant may act, whether the user is needed — is produced by an agent built on the Strands
Agents SDK. Nothing outside `adapters/agent/` imports it. The application sees a `CallAgent`
port, and the model is configured in exactly the shape D-007 describes: a base URL, a key, a
model and optional headers, handed to the SDK's OpenAI-compatible model.

Tools are application objects, not framework functions. Each validates its arguments against
domain types and checks the user's grant before it does anything, inside the tool itself, so the
guarantee does not depend on the framework calling a hook. The adapter only presents them to the
SDK. The model proposes; the escalation policy in `domain/policy/` decides.

**This supersedes the `LLMProvider` port** from phase 1. It offered free text and structured
output and nothing for tool use, and an agent loop needs tool use. Routing the SDK through it
would have meant growing it into a second agent framework; leaving it beside the SDK would have
left an interface nothing implements. It is removed with its contract suite. D-006 still holds —
speech and judgement are separate ports — with `CallAgent` as the judgement one.

Swapping the model is configuration. Swapping the SDK is a new adapter behind `CallAgent`.

Tools that would change the call's course only ask. `request_human_escalation` and `end_call`
write down what the model asked for, and once the model has finished — outside the bound on its
time — the application acts on it once: the escalation the policy makes of the most pressing
reading first, then the ending, only if the rules still allow it. A hang-up can never cancel an
escalation the rules require.

**Accepted trade: one ring per call, and no limit across calls.** The escalation service reaches
the user at most once per call for each level of urgency, so the phone rings immediately at most
once per call. A caller who persuades the model that their call is urgent can have it ring that
once, and nothing yet stops the same caller doing it again on the next call. The policy bounds what one call can cost the user;
limits across calls — per caller, per number, per night — belong to a later hardening phase, not to
this one.

## D-027 — A streaming call is a conference from the moment it is answered

**Accepted.** On the streaming transport every inbound call is placed in a conference as soon as
it is answered. The assistant joins that conference as a participant of its own — a separate leg
whose only job is to carry the bidirectional media stream to the speech session — and the user,
when the policy calls for them, is dialled into the same conference.

The alternative that looks simpler — streaming on the caller's own leg, then moving the caller
into a conference when the user is needed — fails the requirement this product exists for. A leg
carries one bidirectional stream, and moving the caller ends it, so the assistant is gone at the
moment the user arrives. Starting as a conference means the caller's leg is never touched after
it is answered: nobody redials, nobody is transferred, and the assistant can stay, fall silent
while still listening, speak only to the user, or leave, each by changing one participant. On the
port, answering such a call under program control therefore means bringing the assistant into the
conference the caller is already in.

The cost is a mixer in the audio path and an extra leg on every call. The mixer's buffer is set to
its smallest, and the latency it adds is measured in this phase rather than assumed.

What the provider's documentation does not settle — exactly what a participant's inbound audio
contains, whether a held participant hears anything, the frame size — is verified on the first
real call and recorded in the verification report, not guessed at in code.

## D-028 — A handset's screening decision is made on the handset

**Accepted.** Android gives a call screening service five seconds from `onScreenCall` to respond,
and then rings regardless. No decision that needs the backend can be relied on inside that, so
the handset decides: the app keeps a snapshot of the user's deterministic call rules, written
whenever the preferences change, and the screening service evaluates it locally. A caller is
never refused on rules the handset does not have: no snapshot, one older than seven days or dated
more than five minutes ahead of the handset's clock, one in a format the build does not read, a failed evaluation or an exhausted time budget all let the call
ring.

The backend represents the handset as `AndroidNativeCallTransport`, whose events the handset
reports afterwards over an authenticated route, stored per user and idempotent by the handset's
event id. That changes what screening means on the port. `SupportsScreening` states which
decisions a transport can apply and how long the platform allows, and the decision taken is
carried on the call's incoming event; there is no screening command, because one sent from the
backend would arrive after the phone had rung. Answering is gated by
`can_answer_under_program_control` for the same kind of reason: a handset's call is answered by
the person holding it, and a port that offered `answer` on every transport would let one claim a
call was taken while it was still ringing.

What the platform does not give a screening service is not claimed: callers in the user's
contacts and callers withholding their number are never shown to it and always ring, and the
handset does not classify callers, so only important contacts and the `unknown` category apply
there.

*Amended:* the handset records calls only while an account is signed in. The app switches recording
on when a signed-in session starts and sign-out switches it off in the same step that forgets the
account's rules and unreported calls, under the lock every recording takes. A call with nobody
signed in is let ring, as it is with no rules, and nothing about it is kept: it is nobody's to
report, and on a shared phone it is somebody else's, which kept events would have reported to
whoever signed in next. Stamping each stored event with the account it was recorded under and
dropping the rest at sending was not chosen: it would still keep callers' numbers on the handset
for an account that does not exist, and needs an identity for "nobody" that not recording does not.
An install that was signed in before this starts recording the next time the app is opened.

## D-029 — One orchestrator, one sequential run per call, and a plan derived from capabilities

**Accepted.** A single `CallOrchestrator` owns every call's life. Each live call is a *run*: an
inbox of inputs — transport events, the agent's judgements, speech failures, timer expiries —
processed one at a time by that run alone. Nothing else changes a call's state. Two inputs for one
call cannot interleave, because only one is ever being handled; two calls never wait on each other.

**The plan comes before anything happens.** When a call arrives, the run derives a plan from the
transport that carried it: whether the assistant can hold a conversation on it, whether the user can
be bridged in, whether a screening decision was already made on the handset. The steps that plan
does not include are never constructed — there is no escalation step on a transport that cannot
bridge, so escalating there is not a refused request but a path that does not exist.

**Routing is one pure function.** Given the caller, the user's preferences and the plan, it returns
pass-through, the assistant, or rejection. It is the one place the routing rules live, so a change to
those rules is a change to that function and its table.

**The agent proposes; the run acts.** The run implements the agent's `CallActions`. An escalation
moves the call to ringing the user, dispatches the notification without waiting on it (D-016), and
bounds the ring. An unanswered, busy, failed or machine-answered ring returns the call to the
assistant with that outcome in its context, rather than ending the call.

**Every wait is bounded and every bound is a transition.** A ring that is not answered, a judgement
that takes too long, a speech session that will not open and a provider call that hangs each move the
call to a defined state; none is an exception left to escape.

**One teardown.** Every ending — rejected, completed, failed, the caller hanging up, the process
stopping — reaches the same routine: stop the conversation and the speech session, cancel the
timers, end the call at the transport, release the escalation service's memory of the call, write the
summary (the agent's if it recorded one, otherwise the fallback built from the call's facts), store
the final state, and mark the escalation context ended.

**State is durable; a live call is not resumable.** The call is stored on every transition. After a
restart the audio stream and the speech session are gone, so a call found unfinished is ended at its
transport where that is possible and recorded as failed, never left in an indeterminate state.

*Amended:* the summary teardown writes is the model summariser's for a call the assistant handled,
with the agent's recorded outcome, when it recorded one that holds, laid over it; any other call, and
one whose summariser does not answer within teardown's summary bound, is summarised by the fallback
built from the call's facts. The summariser is given that same bound, so the two cannot disagree.
The final state is stored with the summary, as described below, and a call whose final state could
not be stored is given no summary.

*Amended:* the call itself is a wait. A run arms a bound on the call's whole life when it is
admitted, `Bounds.duration`, configured as `CALL_MAX_DURATION_SECONDS` (four hours by default), and
its expiry ends the call as FAILED through the one teardown: a transport's report of a call ending
can be lost, as a handset's is when its app is killed or offline, and nothing else can tell such a
call from a long one. An account holds at most five live calls; a call arriving beyond that is
recorded, moved straight to FAILED and let go at its transport, and is given no owner, so it takes
none of the account's room. Both hold on every transport, because neither asks which one it is.

*Amended:* an ending is the one move not stored the moment it is made. It is stored with the summary,
as teardown's last write, once the call has been let go at its transport; a process that stops
part-way through a teardown therefore leaves the call unfinished, for the next start to end and
summarise, rather than ended with no summary that anything would ever write.

*Amended:* a call is recorded at the moments things happened to it, not the moments the run heard.
A transport that reports its calls after the fact — the handset, which may be offline for hours —
sets `occurred_at` on each event, and what that event causes is stamped with it: the call's start,
the moves routing makes on arrival, somebody joining or leaving, the ending. An event without one,
as every streaming event is, is stamped with the clock. A reported moment more than five minutes
ahead of the clock (the skew D-028 allows a rules snapshot) is recorded as now, and one earlier
than the call's last recorded move as that move, so history never runs backwards. Timers — the
ring, the call's lifetime — and anything the run decides itself stay on the clock: a call reported
hours late is given its whole bound from when it was heard of, not ended on arrival.

## D-030 — Hours are when the assistant answers; none means around the clock

**Accepted. Supersedes the separate working-hours and quiet-hours windows of phase 1.** The design
asks one question — *when should the assistant work?* — and answers it with one ring on a clock and
one line underneath: *outside these hours, calls ring you.* Two windows answered two questions
nobody asks: working hours only ever fed the model a phrase, and quiet hours described when not to
be disturbed, which the phone's own do-not-disturb already does.

`CallRules.active_hours` is a `TimeWindow` or nothing. Nothing is the default and means always, so a
user who never opens the hours screen has an assistant that answers 24/7, which is what the product
promises before anyone configures it.

- **Routing.** Outside the window the assistant answers nothing: a call it would have taken rings
  the user instead. A call the rules reject is still rejected, and one that would ring still rings.
  Written once, in `domain/policy/routing.py`, and applied after the order in D-031.
- **Escalation.** The hours no longer defer anything. A call the assistant is still on after its
  hours end is one that began inside them, and by then the user's phone rings for calls anyway, so
  the policy reaches them immediately. `EscalationUrgency.WHILE_CONVENIENT`, and the escalation
  service's upgrade from it, stay: today's policy does not choose it, and whether anything should is
  a separate decision.
- **Notifications.** `respect_active_hours` replaces `respect_quiet_hours`.
- **Agent context.** `PreferenceContext.in_active_hours` replaces `in_quiet_hours` and
  `in_working_hours`. The model is given a resolved answer, never a window.

**Reading older documents (D-022).** Preferences move to version 4; version 3 belongs to the privacy
section. A document written before 4 is read as around the clock, whatever windows it held, and nothing is written back until the user next
saves. Neither older window meant "the assistant answers now", and turning quiet hours into
assistant hours would ring somebody through exactly the nights they asked to be left alone. A stored
window that is present but unreadable still raises: that is corruption, not an older shape.
`respect_quiet_hours` is read as `respect_active_hours`.

## D-031 — Whether a caller is a contact is decided where the address book is

**Accepted.** The design sorts calls into two lanes: *your contacts ring you; everyone else meets
the assistant.* Transports differ in where the call is first seen (D-005), so they differ in what
they can know about a contact, and the rule is stated per transport rather than pretended equal.

**The address book never leaves the device.** Not uploaded, not synced, and not hashed.
Hashing is rejected explicitly, not overlooked: the space of phone numbers is small enough that a
hash of one — salted or keyed with anything the server holds — is reversed by enumerating
numbers, so an uploaded set of hashes is an uploaded address book with an extra step. Presenting
it as privacy would be the misleading kind of half-built (D-005). A stored contact list would
also be personal data of people who agreed to nothing (D-021), held for every user (D-012).

What the server may know is what the user marks: **important contacts**, capped at 200, each with
a label the user wrote. Marking one is a deliberate act on a single person, the same as today.

How each transport decides `known_contact`:

| Transport | Where the call is first seen | A caller is a contact when |
| --- | --- | --- |
| Android native (`can_screen_before_ringing`) | on the phone | the platform has already decided: a caller in the device's contacts is never shown to the screening service and rings (D-028), so the lane holds without the app reading the address book at all |
| Streaming (`can_stream_call_audio_to_ai`) | on the server | the number is an important contact; nothing else is knowable there |

So on the handset "your contacts" is exactly the address book, enforced by the platform, and on a
streaming transport it means the contacts the user marked. The app says so on the streaming path
rather than draw the same lane and quietly mean less. A phone may offer "mark
these contacts as important" as a user action that sends only the numbers chosen; it never sends
the rest.

**One precedence, evaluated identically on the device and on the server.** It is written once, as
`domain/policy/routing.py`; the handset's evaluator mirrors it from the versioned rules snapshot
(D-028), and the server's router calls it. The handset never reaches the first step — the platform
rings a withheld number without asking — and applies the rest to what it is shown:

1. A withheld number: `anonymous_posture`.
2. An important contact: that contact's own `posture`.
3. A blocked category: reject. `known_contact` is never blocked — `CallRules` refuses a category
   that is both blocked and postured, and the lanes posture it.
4. The category's posture (`known_contact` included, by the table above), else `default_posture`.

Then the user's hours (D-030): outside them, a call the order sent to the assistant rings the user
instead. Rejected stays rejected; ringing stays ringing.

**Device events report what was decided, not who was asked about.** A device-side screening event
carries the category and the posture applied. It does not carry a contact's name from the address
book; the only labels the server ever holds are the ones the user typed for important contacts,
and those are never read to a caller.

**The lanes are a preset, not a new default.** Two lanes are written by the client as ordinary
call-handling rules (`known_contact` passes through, default and anonymous go to the assistant,
`spam` is blocked). `CallRules` keeps its cautious defaults, because call handling is still the
one step with no default safe to assume on somebody's behalf.

## D-032 — Onboarding asks four things

**Accepted.** Setup is call handling, hours, what the assistant may do, and when the user is
told — the four steps the design draws. Introduction, important contacts and personality are no
longer onboarding steps. They remain preferences, edited from settings: the agent still reads
formality, verbosity, topics, facts and important contacts.

Progress recorded against a removed step is ignored on read rather than refused (D-022): the
step no longer exists, so having answered it neither advances nor blocks anybody. Call handling
stays unskippable.

## D-033 — Whose call a call is, the transport side says

**Accepted.** Every call is recorded for, routed by and escalated to one user, and the transport's
events do not say which. How a call reached the product is the transport's business (D-004), so
each transport's side answers it through an application port, `CallOwnership`, chosen in bootstrap
with the transport, and the orchestrator asks without knowing which answered.

A handset reports on behalf of the account it signed in as, and its reports are scoped to that
account when they are accepted, so a reported call is the reporting account's. A streaming call is
the caller dialling the user's own number and the user's carrier forwarding it to the account's
number, which every user shares and which therefore names nobody; the carrier's `ForwardedFrom`
names the user's line, and the call is the call of the user who signed in with that number.

A call nobody owns — dialled at the account's number directly, forwarded from a line no user has, or
arriving while storage cannot say — is let go at its transport and recorded nowhere: there is nobody
to record it for and nobody to put it through to.

Whether a carrier sends `ForwardedFrom` on a conditionally forwarded call is verified on the first
real call, like the other provider behaviours phase 7 left to it. Assigning numbers to users, which
would make it unnecessary, is a provisioning feature and not decided here.


## D-034 — Setup asks for call forwarding only where calls arrive forwarded

**Accepted.** A streaming call reaches the product only when the user's carrier forwards it
(D-033), and the product cannot set that up: it is a setting on the user's line. So the backend
says where to forward, and setup asks for it — on the deployments that need it and nowhere else.

**The number.** Bootstrap decides it once: on the streaming transport, the first configured
telephony number; on the handset transport or none, nothing. It reaches the rest of the
application as a capability, `CallForwarding`, never as a transport's name. `GET /v1/me` carries
it as `call_forwarding: {"number": "+E164"}`, meaning forward unanswered and busy calls there, or
`null` where nothing needs forwarding.

**The step.** `call_forwarding` is declared in the one order, right after `call_handling`: where
calls go is settled before the hours they are handled in. Which declared steps a deployment asks
is an `OnboardingFlow` built from that capability, so the domain reads no configuration and no
screen decides a step's position. The step cannot be skipped. Skipped, no call ever arrives, and a
setup that then reports itself complete tells somebody the product works when it cannot.
Answering it is the user's word that forwarding is on; the backend cannot see a carrier setting,
and whether a forwarded call arrives is verified by the call.

**Progress stays valid across deployments.** It is stored as the steps recorded, and read against
the flow. An answer to `call_forwarding` recorded where it was asked is ignored where it is not,
as a removed step's is (D-032). A deployment that starts forwarding asks it of everybody,
including those who had finished: finished meant finished for calls that no longer arrive.

**Recording it where it is not asked is a 422, `step_not_asked`,** and nothing is stored. Not a
409: nothing about the user's state would make it succeed on another try. It is the same answer a
removed or invented step gets — the request names something that does not exist here.

## D-035 — What callers said is kept out of screenshots, as far as each platform allows

**Accepted.** Call history, a call's summary, what was said and an escalation are other people's
words and the user's circumstances. They are kept out of screenshots, screen recordings and the
app switcher's snapshot, by what each platform actually offers rather than by one mechanism
pretended to be both:

| Platform | What it does | Where |
| --- | --- | --- |
| Android | `FLAG_SECURE` on the window: screenshots and recordings are refused and the recents card is blank | only while one of those screens is mounted, counted so a transcript opened over a summary keeps both protected |
| iOS | a cover drawn over the whole app as it resigns active, so the switcher's snapshot shows nothing | the whole app, always |

iOS gives an app no supported way to refuse a screenshot, and no per-screen hook before the
switcher's snapshot is taken, so the app does not claim either: it covers everything, which is
cheap because every screen in it is about the user's calls. The Android flag is not applied app-wide
because it also blanks the setup screens, where a user sending a screenshot to someone helping them
is the ordinary case.

The native side is a module on Android and nothing on iOS; the JavaScript asks the module if it is
there and does nothing if it is not (D-005). The guard is a counter rather than a toggle, so
closing the top screen never unprotects the ones beneath it.

## D-036 — Signing in is defended in layers, and a session ends only when the server says so

**Accepted.** Two requirements pull against each other. Somebody who has signed in should never be
asked for their number again without cause, and the one route that does not need a session — sending
a code to a number — must not be a way to spend this deployment's money, bomb a stranger's phone,
or guess one's way into an account.

### Staying signed in

- **Only a 401 ends a session on the phone.** No signal, a timeout, a 5xx or a rate limit while
  restoring or renewing keeps the stored session: the app opens signed in and retries. Ending a
  session over a train tunnel is how somebody is asked for their number for no fault of their own.
- **A rotated refresh token is honoured again for two minutes** (`REFRESH_REUSE_LEEWAY`). The server
  rotates a token and answers; the phone writes the new one to its keychain. An app killed between
  the two comes back with the old token, which reuse detection would otherwise read as theft and
  revoke the whole sign-in. Presented within two minutes, it gets a fresh pair and nothing is
  revoked; after that, reuse still revokes the family.
- **Sessions last ninety days and slide.** Every renewal starts the lifetime again, so a phone that
  opens the app within ninety days of the last time stays signed in indefinitely. Signing out, and
  deleting the account, still end it at once.

### Sending codes

Each layer answers a different attack and is counted where that attack cannot reset it:

| Layer | Stops | Default | Counted in |
| --- | --- | --- | --- |
| Allowed calling codes | premium-rate and unserved destinations; most SMS pumping | any (production should list its countries) | configuration |
| Per source | one place asking for codes to many numbers | 20 an hour | the rate limiter |
| Resend cooldown | bombing one phone | 30 s, 60 s, 2 min, then 5 min | the database |
| Per number | the same, slower | 5 an hour, 10 a day | the database |
| Wrong codes per number | guessing, across new codes | 10 in 24 h, then locked — even the right code is refused | the database |
| Verifications per source | one place guessing at many numbers' codes | 60 an hour | the rate limiter |
| Deployment budget | attacks spread across numbers and sources | 500 an hour, 100 per calling code | the database |

- **Only the newest code works.** Sending one supersedes every open code to that number, so asking
  for more codes never opens more to guess at: five guesses per code, against one code at a time.
- **Every refusal says when to come back** (`Retry-After`), and every code sent says when another
  may be asked for (`resend_after_seconds`), so the app counts down instead of retrying into a
  refusal. Refusals are counted as `auth.challenge.refused` by outcome, which is what to alert on.
- **The budget is a circuit breaker.** Past it, codes stop for everybody until the hour rolls on.
  That costs sign-ins for a while, which is recoverable; a pumping attack costs money, which is not.
- **Who is asking** is the connection's peer, unless that peer is a configured trusted proxy, in
  which case it is the nearest address in `X-Forwarded-For` that is not one of ours; IPv6 is counted
  by /64. Behind a load balancer with no proxies configured, every client looks like the balancer,
  so a deployment behind one must set `TRUSTED_PROXY_CIDRS`.
- **Nothing tells an attacker whether a number has an account.** Limits, locks and refusals apply
  to every number alike, and a refused country is refused for everybody in it.

### Not built yet, in the order they would help

1. **Device attestation** (Play Integrity, App Attest) on the challenge route, so codes are sent
   only for requests from a genuine install.
2. **Line-type lookup** before sending, refusing premium-rate and unassigned numbers the allowlist
   cannot see.
3. **The SMS provider's own fraud guard**, once a production OTP provider exists.
4. **A shared rate limiter.** The per-source limits are per process (the limiter says so); the
   per-number and deployment limits are already shared, because they are counted in the database.

## D-037 — Sign-in codes are the application's, sent as a text message

**Accepted.** Production needs a provider that delivers a sign-in code to a real handset; until one
existed the mock was the only provider and a production deployment could not start (D-010). The
first is `twilio_sms`: the application generates the code, stores only its salted hash, and the
adapter sends it in one text message from the telephony provider's Messaging API.

**Not a hosted verification service.** Such a service generates, sends and checks the code itself,
so the application would never hold one — a real advantage, and the reason it was considered
first. It was rejected because it does not fit what sign-in already guarantees, and fitting it
would move those guarantees into the vendor:

- The port's contract is that the code is the product's: its length, alphabet and lifetime (D-010,
  `OTPProvider.send`). A verification service decides those and checks the code, so the port would
  become "start a check" and "ask whether this code passes", and the challenge row would hold no
  hash to verify against.
- The attempt limit holds because a guess is counted under a row lock in the same unit of work
  that verifies it (review F1). Verification by a remote call cannot be inside that lock, and two
  limits — ours and the service's — that count differently are one limit nobody can state.
- One verification path serves every provider, so the path the mock exercises in every test and
  on every contributor's machine is the path production runs. A second path taken only in
  production is the one that breaks unseen.
- A code is short-lived and hashed with scrypt, and it is held in memory only while it is sent.
  What a hosted service would add over that is the provider's fraud screening, which a deployment
  can have on its account either way.

**Its own account variables.** `SMS_ACCOUNT_ID`, `SMS_AUTH_TOKEN` and `SMS_FROM_NUMBER`, rather than
the `TELEPHONY_` ones: a deployment whose calls arrive on a handset has no telephony account and
still needs codes delivered, and a credential used only to send texts can be revoked without
touching the one that carries calls. The same account's values may be given to both. All three are
required when the provider is chosen, and the process refuses to start naming whichever are
missing; the token is a secret and is never rendered.

**Failures say who can fix them.** A number the provider will not deliver to — not a number, not a
mobile, opted out, unroutable — is `UnreachableNumberError`, answered `422 number_unreachable`,
and counts against the number like any code requested. Any other provider failure is a
`ProviderError`, answered `503 provider_unavailable`; the request is rolled back, so a code that
was never sent does not use up the number's hourly allowance. Neither response says whether the
number has an account. The adapter logs that a code was sent or refused and the provider's error
code, never the number or the code.

The wording of the message is a per-locale template (D-017); a number signing in has no account and
so no locale, and gets the default. Whether the provider delivers to a real handset is verified
with a real account; the adapter's requests and error mapping are tested against a simulated
message API.

**Amended: a send that may have gone out counts.** A provider that refuses before sending — an
error answer, or a connection that never opened — rolls the challenge back, because nothing was
sent and an outage is nobody's attempt. A request the provider accepted but never answered — a
timeout, or a connection lost after sending — may have been delivered, so the challenge is kept and
counts against the cooldown and every budget. The client is told `provider_unavailable` with the
wait before another code, so a slow provider cannot be used to send codes nobody counts.

## D-038 — Observability records structure, and a failing provider costs the feature that needs it

**Accepted.** What the backend says about itself — log lines, metrics, spans, readiness and
diagnostics — carries states, timings, stages, failure kinds and identifiers of calls and requests,
and never a number, a token or anything anybody said. Each is held to that mechanically rather than
by review:

- **Logs** pass one set of processors, structlog's and the standard library's alike. The last of
  them removes any field named like a number, a token or words, however deeply nested, clears text
  of anything shaped like a number or a signed token, and logs an exception as its type, causes and
  frames. A test reads every log call in the product for such a field by name.
- **Metrics** are declared beside the code that records them, with the values each label may take.
  A recorder refuses anything undeclared, and a test over the registry proves every label bounded.
- **Spans** begin through a tracer port. The default sends nowhere; OpenTelemetry, over OTLP, is an
  adapter chosen when `TRACING_OTLP_ENDPOINT` is set. Attribute keys are a fixed list, values are
  tokens, and a call id that could be a number is carried as `untraceable`.
- **A call's timeline** — each state it entered, each stage that failed, each dependency it went
  without — is stored with the call, in the same unit of work, and deleted with it.
  **Diagnostics** read it by call id alone, behind a token of their own (`DIAGNOSTICS_TOKEN`), and
  do not exist without one.

**One failure taxonomy.** Every error the product defines states its `FailureKind`, and
`classify` decides from the kind whether it is retryable, shown to the user, needs attention (an
error-level line) or is a defect. A test fails an error added without a kind.

**Retries only where repeating is safe.** A retryable failure is tried again, with jittered
backoff, only through `retry_idempotent`: ending a call at the transport and storing the whole
call at teardown. Dialling the user is never retried; a second dial is a second ring.

**A circuit per dependency.** Telephony, speech, the model and each push platform. A dependency
failing repeatedly is not asked for a cool-off, and the product takes the degraded path at once:

| Dependency | While its circuit is open |
| --- | --- |
| Speech | A call the assistant would take is put through to the user instead (routing's own fallback), marked degraded. |
| Model | Judgements are not asked; the assistant keeps talking. Summaries are written from the call's facts. |
| Telephony | Requests are refused without waiting; a call that cannot be answered fails, and ending one is still tried. |
| Push, per platform | That platform's devices are recorded unavailable; the ring still happens (D-016). |

An open circuit does not make a process unready. Every process shares the same providers, so taking
one out of rotation moves its calls to another that fails them the same way. Readiness reports each
circuit by role, never by vendor, and whether rate limits are shared across processes.

