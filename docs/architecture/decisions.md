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

## D-004 — Call transport is a port; no transport mechanism is part of the domain

**Accepted.** The domain knows that a call exists, that audio flows in and out, that a
third party can be added to a live call, and that the call can end. It does not know
whether that is served by programmable telephony, SIP, a native dialer, or a carrier
integration.

Consequences that follow from this and are binding:

- Call forwarding, number provisioning, and webhook shapes are adapter concerns. They do
  not appear in domain types, and they do not appear in the mobile app's core state.
- Platform limitations (iOS forbids third-party call answering; Android permits a default
  dialer role) are capability flags on the adapter, never branches in domain logic.
- A future carrier/IMS or on-device adapter must be addable without editing the domain.

## D-005 — One telephony adapter is implemented; the rest are documented extension points

**Accepted.** Programmable telephony is the first and only implemented adapter, because it
is the only option where a third party can provably be bridged into an already-live call
today. Other adapters named in the architecture (native dialer, SIP, carrier) are
documented interfaces with no implementation.

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

## D-008 — Realtime speech: one implemented adapter, capability-declared

**Accepted.** A managed bidirectional streaming speech model is the first `SpeechProvider`
implementation. Its supported languages, voices, sample rates, and whether it supports
barge-in are declared as capabilities rather than assumed by callers.

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
