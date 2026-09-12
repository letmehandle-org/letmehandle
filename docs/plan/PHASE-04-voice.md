# Phase 4 — Voice configuration

**Goal:** the user chooses how the assistant sounds, and the interface never implies a
capability the configured provider lacks.

## In scope

### Provider model
`VoiceProvider` (Phase 1) is implemented for the first time. Capabilities are declared, not
inferred (D-009):

```
supports_builtin_voices    supports_voice_preview     supports_custom_voice
supports_voice_cloning     supports_local_inference   supports_realtime_streaming
```

The first implementation is the built-in voice set of the realtime speech provider. It
declares cloning as unsupported. No cloning-capable provider ships in this phase.

### Resolution and fallback
The chain from D-009, implemented in the domain and tested at every step:

```
configured cloned voice → selected persona voice → provider default voice
```

A cloned voice that is missing, revoked, or failing resolves down the chain. A call never
fails because a voice is unavailable.

### Backend
- Migration for voice configuration, scoped to the user.
- `GET /v1/voices` — the catalogue the configured provider actually offers, plus its
  capabilities, so the client renders from the server's truth rather than a bundled list.
- `GET /v1/voices/{id}/preview` — a short sample, generated or streamed by the provider.
- `GET|PUT /v1/preferences/voice`.
- Endpoints for the custom-voice lifecycle exist in the API surface only when the configured
  provider declares the capability. They are not registered otherwise, so an unsupported
  call is a 404 rather than a runtime error inside an adapter.

### Mobile
- Voice selection with preview playback and the correct audio-session behaviour, including
  with the silent switch engaged and with another app playing.
- The screen renders from the capability flags. When cloning is unsupported, the training
  flow is absent from the interface entirely — not greyed out, not labelled as unavailable.
- When a cloning-capable provider is configured, the flow that becomes available is:
  explicit consent, sample recording or upload, creation, use, retrain, delete. That flow is
  designed and specified in this phase and built when such a provider is implemented.

## Explicitly out of scope

- Implementing a cloning-capable provider. It is an adapter plus configuration, added later
  without touching this phase's code.
- Storing voice samples. Nothing here records or retains the user's voice, so this phase
  adds no biometric data surface.
- Per-caller or per-context voices.

## Tests required

| Kind | Must prove |
| --- | --- |
| Unit | The fallback chain at each step, including total failure; capability gating decides UI and route registration; an unsupported operation raises a typed error rather than a vendor exception. |
| Contract | The Phase 1 `VoiceProvider` suite passes against the real implementation, including its declared-unsupported paths. |
| Integration | Catalogue reflects the configured provider; preview returns playable audio; selection persists and is scoped to the user; custom-voice routes are absent when unsupported. |
| Mobile | The training flow does not render when the capability is false, asserted as a test rather than checked by eye; preview plays and stops correctly; selection persists. |
| E2E | Select a voice, sign out, sign in, the selection holds. |

## Acceptance criteria

1. A user selects and previews a built-in voice.
2. The selection persists and is used by the speech session in Phase 5.
3. The provider abstraction is covered by the contract suite.
4. Unsupported capabilities fail gracefully with a typed error, never a vendor exception.
5. No interface element implies voice cloning while no cloning provider is configured.
6. The fallback chain is proven at every step.
7. Coverage meets the D-020 floors.

## Risks and open questions

- **Preview cost and latency.** Generating a sample per tap is wasteful. Cached per voice
  and locale, with the cache key including the provider, so switching providers cannot serve
  a stale sample.
- **Consent for a future cloning flow.** Voice is biometric data in several jurisdictions.
  The consent and retention design is specified in this phase and reviewed in Phase 12
  before any provider that needs it is implemented.
