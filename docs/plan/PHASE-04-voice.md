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

---

# Phase 4 verification

```
PHASE 4 VERIFICATION

Planned tasks:        complete, with two deviations recorded below
Acceptance criteria:  6/7 passed, 1 passed in part (criterion 1: selection yes, preview no)
Unit tests:           passed   backend 891 passed, 3 skipped; mobile 179 passed, 17 suites
Contract tests:       passed   the VoiceProvider suite against the built-in provider twice —
                               with sample audio and without
Integration tests:    passed   against a real PostgreSQL: the voice API end to end, per-user
                               isolation, sign-out and sign-in, a deleted account's token, and a
                               cloned voice revoked while a request holds the row lock
E2E tests:            passed   the mobile suite drives the application tree against a stateful
                               fake backend: open settings, choose, clear, refusal, retry, and
                               the fallback chain as the screen reports it
Coverage:             100.00%  backend, floor 98
                      98.2% statements / 92.9% branches mobile, floor 90
Lint:                 passed   ruff check; eslint --max-warnings 0; prettier --check
Format:               passed
Typecheck:            passed   mypy --strict; tsc --noEmit
Static analysis:      passed   import-linter, 3 contracts kept
Build:                passed   generated API types match the backend schema
Application runs:     yes      no migration: preferences are one versioned document (D-022),
                               now at version 2
Manual verification:  none beyond the automated suites
Docs updated:         decision record (D-024, D-025), docs/providers/voice.md, the phase plan
Known issues:         none open; one review finding declined, with the reason below
Commits created:      24
```

## Acceptance criteria, each with its evidence

| # | Criterion | Evidence |
| --- | --- | --- |
| 1 | A user selects and previews a built-in voice | **In part.** Selection: `TestChoosing` over HTTP and the mobile `choosing one` suite. Preview: not in the shipped configuration — see the first deviation. The preview route and its behaviour are proven with a provider given samples, in `TestPreviewFollowsTheProvider` |
| 2 | The selection persists and is used by the speech session in Phase 5 | Persists: `test_a_choice_survives_signing_out_and_back_in`. Used: `GET /v1/preferences/voice` returns `resolved_voice_id` from `resolve_voice`, the one function Phase 5 will call |
| 3 | The provider abstraction is covered by the contract suite | `VoiceProviderContract`, run against `BuiltInVoiceProvider` in both configurations it ships in |
| 4 | Unsupported capabilities fail with a typed error, never a vendor exception | The contract asserts `CapabilityNotSupportedError` for every voice when `preview` is not declared, and a `DomainError` — never a `KeyError` — for an unknown voice |
| 5 | No interface element implies cloning while no cloning provider is configured | `offers no way to train or clone a voice`, asserted in the mobile suite; the provider declares `cloning` and `custom_voice` false permanently |
| 6 | The fallback chain is proven at every step | The contract: nothing chosen, a chosen voice honoured (deliberately not the default), an unavailable choice falling through, a missing locale refused. The mobile suite: withdrawn, stepped past, and a working clone not reported as a problem |
| 7 | Coverage meets the floors | 100% backend; 98.2% / 92.9% mobile |

## Deviations from the plan

**No preview in the shipped configuration.** The plan says a user previews a voice. A preview is
synthesised audio, and there is nothing to synthesise it with until the speech provider arrives
in Phase 5. The built-in provider therefore declares `preview` only when it is given sample audio,
ships with none, and the application registers no preview route, generates no client method and
draws no control (D-024). The mechanism is built and proven with a provider that has samples. What
is missing is audio, and the audio-session behaviour the plan asks for — silent switch, another app
playing — has nothing to play; it moves to Phase 5 with the first sample.

**No migration.** The plan lists one. Preferences are a single versioned JSON document (D-022), so
the voice is a field in it and the document version moved from 1 to 2 instead.

## An adversarial review, and what came of it

The suite was green at 100% coverage when an agent was set to break it, with the instruction that
a suspicion without a reproduction does not count. It reproduced seven defects. Each fix shipped
with a test written from the reproduced failure.

- **A revoked cloned voice was resurrected.** Choosing a voice read the stored clone before the
  write took its lock and wrote it back afterwards. The resolution chain puts a clone first, so a
  caller would have heard a voice the user had deleted. The halves of the selection are now
  carried separately and merged under the lock — the shape call handling and hours already use for
  the same reason. The test holds a competing row lock so the race is deterministic.
- **The preview route said "There is no such voice" about a voice it had just listed.** A provider
  holding a sample for one voice declared the capability truthfully and could serve only that one.
  Previewability is now per voice, in the catalogue, and the refusal says there is nothing to play.
- **A deleted account's token still read the catalogue.** The voice routes checked the signature
  and not the account, unlike every other route.
- **The stored version never moved.** A row written at 1 stayed at 1 after gaining a version-2
  field, which is the ambiguity the version exists to remove. Every write now stamps the current
  version.
- **A contract assertion could not fail.** It checked that choosing the first voice resolved to it,
  and the first voice was also the default. A provider ignoring every selection passed.
- **The preview contract checked one voice**, which is why the per-voice defect above shipped.
- **A full `PUT /v1/preferences` erased the chosen voice**, having no field to carry it. Found
  independently while the review ran, and fixed first.

The mobile work had its own review before it was merged. A helper nothing but a test called was
removed, and its compile-time check on the generated schema moved into that test. The screen
compared the answering voice with the chosen one alone, so a working clone would have been
reported as the user's choice being ignored. A retry test found the button and never pressed it.

**Declined:** adding a unique id to access tokens. Two sign-ins in the same second produce the same
token, but those tokens are genuinely equivalent — same subject, same expiry — and nothing revokes
access tokens individually. The only harm found was a test asserting they differed, which was wrong
and was removed.

## What this phase deliberately does not do

The custom-voice lifecycle — consent, recording, creation, retraining, deletion — is specified in
`docs/providers/voice.md` and not built, because no provider can clone a voice. The consent and
retention design is there for Phase 12 to review before any provider that needs it exists.

A cloned voice has a field and a place in the resolution chain, and no route sets it. That is right
while nothing can create one, and it means a stale clone could not be cleared from the app today;
the screen at least says when one is being stepped past.
