# LetMeHandle

[![CI](https://github.com/letmehandle-org/letmehandle/actions/workflows/ci.yml/badge.svg)](https://github.com/letmehandle-org/letmehandle/actions/workflows/ci.yml)
[![Licence: MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)

An AI agent that answers your phone calls, understands what the caller wants, applies your
preferences, resolves the routine ones on its own, and pulls you into the same live call
when it genuinely needs you.

> **Status: 0.1, not ready to deploy.** Every piece of a call is built and tested end to end
> against simulated providers, but no real phone call, real model or real device has been through
> it yet, and there is no sign-in provider that sends a real text message — so no deployment of
> this project is safe to expose to anybody but its developers. [Maturity](#maturity) says exactly
> what that means.

## What it does

A call arrives. Your rules decide whether it goes straight through to you, is rejected, or is
handled. If it is handled, the assistant talks to the caller in real time, works out what they
want, and either resolves it or decides you are needed — by your own threshold, never by the
model's say-so. When you are needed your phone rings, a notification tells you why before you
answer, and answering joins you to the conversation that is already happening. The caller never
redials.

Afterwards you get a short summary, not a transcript dump. Transcripts are encrypted and deleted
on a schedule you set.

How much of that a deployment offers depends on how calls reach it:

- **Programmable telephony**, with your carrier forwarding unanswered and busy calls to it: the
  whole product — the assistant on the call, escalation into it, summaries.
- **Android call screening**, on your own phone: your rules decide each call before it rings —
  let it ring, reject it, or silence it — and the call is recorded. Android does not give an app
  the audio of a phone call, so there is no assistant on this path, and the product does not
  pretend otherwise.

[`docs/architecture/call-transport.md`](docs/architecture/call-transport.md) has the full matrix.

## What it does not do

- Transcribe voicemail. It holds the conversation.
- Make app-to-app calls. It is built around real phone calls.
- Place calls on your behalf. It answers them.
- Record audio. Calls are never recorded, anywhere (D-013).
- Run hosted. There is no service to sign up to; you run it.
- Speak anything but English, yet (D-017).

## Maturity

| | State |
| --- | --- |
| Domain, routing, escalation policy, call orchestration | built; every path tested, 100% of branches |
| Backend API, storage, encryption, retention | built and tested against PostgreSQL |
| Streaming telephony transport | built; tested against a simulated provider, **never on a real call** |
| Realtime speech (two protocols) | built; tested against simulated services, **no live call** |
| Agent and summaries | built; tested with a scripted model, **no real model evaluated** |
| Push notifications (iOS, Android) | built; tested against simulated services, **no real device** |
| Android call screening | built; tested on the JVM and end to end, **no real handset** |
| Sign-in codes | **mock only.** Accepts `123456` and refuses to start in production |
| Mobile app | in progress |
| Observability, dependency audit | in progress |

Where the code met a question only a real provider can answer, it is written down rather than
guessed, in [`docs/providers/call-transport.md`](docs/providers/call-transport.md#verified-on-the-first-real-call-not-here),
and scripted for the first real run in
[`docs/testing/manual-verification.md`](docs/testing/manual-verification.md). [`PLAN.md`](PLAN.md)
has the state of every phase.

## What it costs to run

**Trying it: nothing.** The quick start below needs no account with anybody. Sign-in is mocked,
and every provider a call needs is simulated in the test suite.

**Running it for real**, every cost is somebody else's price and scales with calls:

| What | Paid for |
| --- | --- |
| A server and PostgreSQL | whatever hosts them; the backend is one small container |
| Programmable telephony | a phone number per month, and every minute of every call, including the leg to your phone when you are brought in |
| A realtime speech service | every minute the assistant is on a call; a server you run yourself costs only its hardware |
| A model endpoint | tokens for each judgement and each summary; any OpenAI-compatible endpoint, including one you run |
| Push notifications | free to send; iOS needs an Apple developer account |
| Carrier call forwarding | usually part of your phone plan; check yours |

Android call screening needs none of the telephony, speech or model costs, and offers none of what
they pay for.

## Quick start

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), Node 22, pnpm 9, and Docker with
Compose. The mobile app also needs Xcode or Android Studio.

```bash
git clone https://github.com/letmehandle-org/letmehandle.git
cd letmehandle
make setup        # dependencies and git hooks
make sample-env   # a .env with fresh keys and mock providers, no paid account
make up           # PostgreSQL, migrations, and the backend on port 8000
curl localhost:8000/health
```

`make up` builds the backend image the first time, which takes a few minutes. When port 8000 or
5432 is taken, run every `make` command with `BACKEND_PORT=8100 POSTGRES_PORT=5433` in front.

Then sign in and look around, or run whole simulated calls with `make e2e`:
[`docs/development/demo.md`](docs/development/demo.md). The mobile toolchain and everything else
about working on the project is in [`docs/development/setup.md`](docs/development/setup.md).

## Replaceable by design

Every external capability is a port with adapters behind it, and the domain layer imports
none of them. That is enforced by the build, not by convention.

| Port | What you can swap |
| --- | --- |
| `CallTransport` | how calls physically reach the system — programmable telephony, the platform's own call screening, or a future SIP or carrier integration |
| `SpeechProvider` | the realtime speech service |
| `CallAgent` | the model that makes decisions — any OpenAI-compatible endpoint, hosted or local |
| `VoiceProvider` | how the assistant sounds |
| `NotificationProvider` | how you are alerted |
| `OTPProvider` | how sign-in codes are delivered |

Providers declare their capabilities. The product adapts to what yours can actually do, and
never presents a feature your provider does not support. See [`docs/providers/`](docs/providers/)
to implement one.

## Documentation

- [`docs/development/demo.md`](docs/development/demo.md) — a walkthrough, with no paid account
- [`docs/development/setup.md`](docs/development/setup.md) — setting up, running, troubleshooting
- [`docs/development/configuration.md`](docs/development/configuration.md) — every variable, generated from the code
- [`docs/architecture/overview.md`](docs/architecture/overview.md) — the shape of the system
- [`docs/architecture/call-flow.md`](docs/architecture/call-flow.md) — the life of a call and the escalation sequence
- [`docs/architecture/call-transport.md`](docs/architecture/call-transport.md) — call transports and what each can do
- [`docs/architecture/decisions.md`](docs/architecture/decisions.md) — why it is shaped that way
- [`docs/providers/`](docs/providers/) — writing a provider
- [`docs/development/testing.md`](docs/development/testing.md) — the suites and the coverage gates
- [`docs/development/workflow.md`](docs/development/workflow.md) — branches, commits, releases and versioning
- [`docs/architecture/security.md`](docs/architecture/security.md) — the threat model, and what is not defended
- [`docs/development/self-hosting-security.md`](docs/development/self-hosting-security.md) — running a deployment securely
- [`PLAN.md`](PLAN.md) — how this is being built, phase by phase
- [`CHANGELOG.md`](CHANGELOG.md) — what changed in each release
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — how to contribute
- [`SECURITY.md`](SECURITY.md) — reporting a vulnerability

## Privacy

Calls are not recorded. Transcripts are encrypted at rest and deleted on a schedule each user
controls, defaulting to seven days. The structured summary outlives the transcript. What the
project does and does not protect against is written down rather than implied — see
[`docs/architecture/security.md`](docs/architecture/security.md). Before deploying it anywhere
reachable, read
[`docs/development/self-hosting-security.md`](docs/development/self-hosting-security.md).

## Licence

MIT. See [`LICENSE`](LICENSE). Third-party licences are listed in
[`docs/development/licences.md`](docs/development/licences.md).
