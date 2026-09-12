# Changelog

Notable changes, in the format of [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioned per [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Pre-1.0: the public interface may change in a minor version. What that promises is stated at
the first release.

## [Unreleased]

### Added
- Build plan covering phases 0 to 15, with per-phase scope, tests and acceptance criteria.
- Architecture decision record.
- Disclosure and secret audits, wired into commit, push and CI.
- Backend: FastAPI application with typed configuration validated at startup, structured
  logging with a request correlation id, health and readiness endpoints, and enforced
  architectural boundaries.
- Mobile: React Native application shell with typed navigation, a design-token layer, and
  translation wired from the first screen.
- Local development stack, backend image, and CI covering lint, types, tests, coverage, the
  backend image, and native builds for both platforms.
- Domain model: calls and their state machine, callers, users, preferences and deterministic
  call rules, agent authority, intent and importance, escalation decisions, and call summaries.
- Preferences: call handling, important contacts, working and quiet hours, topics, what the
  assistant may volunteer, authority boundaries and notification choices — stored as one
  versioned document, changed a section at a time without disturbing the others.
- Server-side onboarding progress, so reinstalling resumes where somebody was.
- A deterministic, versioned context builder that turns stored preferences into what the model
  will be told, carrying no phone numbers.
- Mobile onboarding and a settings surface where every value is editable afterwards.
- Authentication: phone-number identity with one-time codes, rotating refresh tokens with
  reuse detection, rate limits, and a mock code provider that refuses to run in production.
- The first migration: users, devices, refresh tokens and one-time challenges.
- Mobile sign-in: welcome, number, code and profile screens, a session restored on cold start,
  and an API client that renews once for concurrent requests.
- A generated TypeScript client, with a build that fails when it drifts from the backend.
- Provider ports with declared capabilities — `CallTransport`, `SpeechProvider`, `LLMProvider`,
  `VoiceProvider`, `NotificationProvider`, `OTPProvider`, `Clock` and `IdGenerator` — each with
  a contract suite any implementation must pass.
