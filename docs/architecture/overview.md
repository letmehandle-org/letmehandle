# Architecture overview

## The shape

Three layers, and the dependency arrow only points one way.

```
   api/            HTTP and webhooks. Translates requests into application calls.
     │
   application/    Use cases. Composes domain objects and ports. No framework, no vendor.
     │
   domain/         The product. Types, rules, state, and the port interfaces.
     ↑
   adapters/       Implementations of the ports. Every vendor SDK lives here and nowhere else.
```

`domain/` imports nothing from `adapters/`, `api/`, or any third-party SDK. Adapters depend
on the domain's interfaces; the domain never learns what implements them.

This is checked by import-linter in `make verify`, so it is a property of the build rather
than a habit. Adding a vendor import to a domain module fails the build.

## Ports

A port is an interface the product needs from the outside world. Each declares capabilities,
so callers ask what a provider can do rather than assuming.

| Port | Responsibility |
| --- | --- |
| `SpeechProvider` | realtime spoken conversation: audio in, audio out, interruption, context |
| `LLMProvider` | judgement: intent, importance, structured decisions |
| `CallTransport` | how a call exists: observe, screen, answer, stream, inject, add a participant, terminate |
| `VoiceProvider` | how the assistant sounds: catalogue, preview, custom voices |
| `NotificationProvider` | reaching the user's device |
| `OTPProvider` | delivering a sign-in code |
| `Clock`, `IdGenerator` | time and identity, injected so tests control both |

Every port has a contract test suite. An implementation proves itself by passing that suite,
which means a simulator used in tests and a real adapter used in production are held to the
same standard — and divergence between them shows up as a contract failure rather than as a
production surprise.

## A call

```
  caller
    │
    ▼
  CallTransport ──────────────────► CallOrchestrator ◄──── CallRules, UserPreferences
    │  audio                             │
    │                                    ├─► pass through to the user
    │                                    ├─► reject
    │                                    │
    ▼                                    ▼
  SpeechProvider ◄──── context ──── Agent ──► EscalationPolicy
    │  conversation                      │         │
    │                                    │         ▼
    │                                    │    escalation decision
    │                                    │         │
    │                                    │         ├─► CallTransport.add_participant
    │                                    │         │     dials the user, joins the live call
    │                                    │         └─► NotificationProvider
    │                                    │               context for the ring
    ▼                                    ▼
  call ends ──────────────────────► CallSummary
```

The orchestrator is the only writer of call state. The agent proposes; a deterministic policy
decides; the orchestrator acts.

## Why it is split this way

The assistant that talks and the assistant that decides are different concerns with different
failure modes. Keeping them apart means the authority decision is auditable without replaying
audio, and either half can be replaced without touching the other.

The transport a call arrives on is not part of the product. Two are implemented — the
platform's own call screening on Android, programmable telephony on iOS — and a SIP trunk or a
carrier integration would be the same port with another adapter.

They differ in what they can do, not merely in who provides them, and that is exactly why the
core asks about capabilities rather than names:

| | screen before ringing | stream call audio | inject agent audio | bridge a human |
| --- | --- | --- | --- | --- |
| Android native | yes | no | no | no |
| Programmable telephony | no | yes | yes | yes |

A transport that cannot stream audio offers no agent conversation; one that cannot bridge
offers no escalation into a live call. The orchestrator derives which transitions exist from
those flags when a call begins, so an unsupported path is unreachable rather than guarded.
Selection happens once, in bootstrap. Nothing downstream of it knows which transport it has.

## Decisions

Numbered, with reasoning, in [`decisions.md`](decisions.md). Phase plans cite them by id so a
decision lives in one place and its change is visible in history.
