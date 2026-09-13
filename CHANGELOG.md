# Changelog

Notable changes, in the format of [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioned by release date.

## Versioning

- A version is the date it was released: `2026.9.13` for 13 September 2026, month and day without
  leading zeros. A further release on the same day adds a counter: `2026.9.13-2`.
- Any release may change something public — the HTTP API, configuration variables, the provider
  ports, the database schema — and says what to do under **Changed** or **Removed**.
- Migrations only move forward, and the backend image carries them.
- Only the latest release receives fixes.

The full policy, and how a release is cut, is in
[`docs/development/workflow.md`](docs/development/workflow.md#versioning). Each pull request adds its
line under **Unreleased**; a release moves those lines under its version, and the release notes are
taken from that section.

## [Unreleased]

### Added

- A third speech adapter, for GPT-Live, chosen with `SPEECH_PROVIDER=gpt_live` (D-040).

## [2026.9.13]

The first public release. Every part of a call is built and tested end to end against simulated
providers. Not yet run against a real call, a real model or a real device, and without a sign-in
provider that sends a real text message: **no deployment of this version is safe to expose.**

### Added

**Foundation**
- Build plan covering phases 0 to 15, with per-phase scope, tests and acceptance criteria, and the
  architecture decision record.
- Disclosure and secret audits over the tree and the whole history, wired into commit, push and CI.
- Backend: FastAPI application with typed configuration validated at startup, structured logging
  with a request correlation id, liveness and readiness endpoints, and architectural boundaries
  enforced by import-linter.
- Mobile: bare React Native application with typed navigation, design tokens and translation from
  the first screen; native builds for both platforms in CI (#17, #30).
- Local development stack, backend image, and CI covering lint, types, tests, coverage, migrations,
  the image, dependency review and native builds; code scanning when a release is published (#37).

**Domain and contracts** (#21)
- Calls and their state machine, callers, users, preferences and deterministic call rules, agent
  authority, intent and importance, escalation decisions and call summaries.
- Provider ports with declared capabilities — `CallTransport`, `SpeechProvider`, `VoiceProvider`,
  `NotificationProvider`, `OTPProvider`, `Clock` and `IdGenerator` — each with a contract suite.

**Accounts and preferences** (#22, #25, #31, #35)
- Phone-number sign-in with one-time codes, rotating refresh tokens with reuse detection, rate
  limits, and a mock code provider that accepts `123456` and refuses to run in production.
- Preferences stored as one versioned document and changed a section at a time: call handling,
  important contacts, the assistant's hours, topics, what it may volunteer, its authority, and
  notifications.
- Contact routing shared by the server and the handset, and a four-step setup whose progress is
  kept on the server.
- A generated TypeScript client, with a build that fails when it drifts from the backend.

**Voices and speech** (#26, #27)
- Voices offered from configuration, with capabilities that decide which routes exist.
- Realtime spoken conversation over two protocols — OpenAI Realtime-compatible and ElevenLabs
  Agents — with barge-in, context changed mid-call, bounded reconnection, and a harness for a live
  conversation through the microphone.

**The agent** (#33)
- An agent on the Strands Agents SDK behind the `CallAgent` port, on any OpenAI-compatible
  endpoint, with tools that check the user's grant themselves.
- A deterministic escalation policy: the model proposes, the policy decides.
- An evaluation suite, run against a configured model, reporting pass rates by class of call.

**Calls** (#34, #36)
- Two call transports behind one port: a conference-first streaming transport over programmable
  telephony, and call screening on the user's Android handset reported to the backend.
- One orchestrator for every call, with a plan derived from the transport's capabilities, bounded
  waits, one teardown, and recovery of calls a stopped process left unfinished.
- Escalation into the live call: the user's phone is dialled into the conversation, with a push
  notification to iOS or Android saying why, and the assistant takes the call back when the user
  does not answer.
- Call history with summaries written by the model or, failing that, from the call's facts;
  transcripts encrypted at rest and purged on each user's schedule.
- Call forwarding setup, asked only where calls arrive forwarded.
- Whole-system scenarios against a simulated telephony provider, speech service, model and push
  services (`make e2e`).

**Mobile app** (#28, #32)
- The brand, the app icon and launch screens, and the designed screens for sign-in, setup and
  settings.

**Release readiness**
- `make sample-env`, which writes a configuration that runs with mock providers and no paid
  account; the development stack now migrates its database before starting the backend.
- A configuration reference generated from the settings, with checks that it, `.env.example` and
  the development stack match the settings.
- Architecture documentation for the call flow and call transports, with the capability matrix and
  the state diagram checked against the code by tests; a page and a worked example for every
  provider port; testing, workflow, verification and demonstration guides.
- A third-party licence report, and a link check over the documentation.
- A release workflow that publishes the backend image and takes its notes from this file.

### Security
- A security review of the whole backend (`docs/security/review.md`), with nine findings fixed:
  concurrent code guesses counted once, a refresh token replayed during its own exchange, unbounded
  request bodies, no rate limit on signed-in routes, caller numbers and sign-in challenges kept
  longer than needed, escalation contexts left behind or stored unsealed, and no account deletion
  (#36).
- Telephony callbacks and the media websocket are checked against the provider's signature over
  the configured URL, and a stream must present a one-time token (#34).
- The threat model, what is not defended, and a guide to running a deployment securely.

### Known limitations
- The only sign-in code provider is the mock. A production configuration refuses to start with it.
- No real call, real speech service, real model or real device has been through the product; the
  open questions are listed in `docs/providers/call-transport.md`.
- The mobile app's call history and escalation screens are unfinished, and screens that show call
  content are not yet excluded from screenshots.
- Observability beyond structured logs, and a dependency vulnerability audit, are not yet in place.

[Unreleased]: https://github.com/letmehandle-org/letmehandle/compare/v2026.9.13...HEAD
[2026.9.13]: https://github.com/letmehandle-org/letmehandle/releases/tag/v2026.9.13
