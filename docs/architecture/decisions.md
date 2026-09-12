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
malformed time or phone number raises, because quiet hours that silently disappear mean a phone
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
