# LetMeHandle

[![CI](https://github.com/letmehandle-org/letmehandle/actions/workflows/ci.yml/badge.svg)](https://github.com/letmehandle-org/letmehandle/actions/workflows/ci.yml)
[![Licence: MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)

An AI agent that answers your phone calls, understands what the caller wants, applies your
preferences, resolves the routine ones on its own, and pulls you into the same live call
when it genuinely needs you.

> **Status: early construction.** The repository is public from its first commit because the
> architecture is the point and it should be reviewable from the start. It does not yet
> answer calls. [`PLAN.md`](PLAN.md) states exactly what is built, what is being built, and
> what is not.

## What it is meant to do

A call arrives. Deterministic rules decide whether it goes straight through to you, is
rejected, or is handled. If it is handled, the assistant talks to the caller in real time,
works out the intent, and either resolves it or decides a human is required. When a human is
required your phone rings, a notification tells you why before you answer, and answering
joins you to the conversation that is already happening. The caller never redials.

Afterwards you get a short summary, not a transcript dump.

## What it is not

- Not a voicemail transcriber. It holds the conversation.
- Not an app-to-app calling product. It is built around real phone calls.
- Not finished. See the status note above.

## Replaceable by design

Every external capability is a port with adapters behind it, and the domain layer imports
none of them. That is enforced by the build, not by convention.

| Port | What you can swap |
| --- | --- |
| `SpeechProvider` | the realtime speech model |
| `CallAgent` | the model that makes decisions — any OpenAI-compatible endpoint, hosted or local |
| `CallTransport` | how calls physically reach the system — programmable telephony, the platform's own call screening, or a future SIP or carrier integration |
| `VoiceProvider` | how the assistant sounds |
| `NotificationProvider` | how you are alerted |
| `OTPProvider` | how sign-in codes are delivered |

Providers declare their capabilities. The product adapts to what yours can actually do, and
never presents a feature your provider does not support.

Call transports differ in kind rather than only in vendor, so this matters most there. Android
can screen a call before the handset rings but cannot hand an application the audio of a SIM
call; programmable telephony can stream that audio and bridge a second person into a call
already in progress. Both are the same interface, and the product asks what a transport can do
rather than which one it is.

See [`docs/providers/`](docs/providers/) to implement one.

## Getting started

Requirements: Python 3.12, Node 22, pnpm 9, Docker, and — for the mobile app — Xcode or
Android Studio.

```bash
git clone https://github.com/letmehandle-org/letmehandle.git
cd letmehandle
make setup
cp .env.example .env
make up
curl localhost:8000/health
```

Full instructions, including the mobile toolchain, are in
[`docs/development/setup.md`](docs/development/setup.md).

## Documentation

- [`PLAN.md`](PLAN.md) — how this is being built, phase by phase
- [`docs/architecture/overview.md`](docs/architecture/overview.md) — the shape of the system
- [`docs/architecture/decisions.md`](docs/architecture/decisions.md) — why it is shaped that way
- [`docs/providers/`](docs/providers/) — writing a provider
- [`docs/architecture/security.md`](docs/architecture/security.md) — the threat model, and what is not defended
- [`docs/development/self-hosting-security.md`](docs/development/self-hosting-security.md) — running a deployment securely
- [`SECURITY.md`](SECURITY.md) — reporting a vulnerability
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — how to contribute

## Privacy

Calls are not recorded. Transcripts are encrypted at rest and deleted on a schedule each user
controls, defaulting to seven days. The structured summary outlives the transcript. What the
project does and does not protect against is written down rather than implied — see
[`docs/architecture/security.md`](docs/architecture/security.md). Before deploying it anywhere
reachable, read
[`docs/development/self-hosting-security.md`](docs/development/self-hosting-security.md).

## Licence

MIT. See [`LICENSE`](LICENSE).
