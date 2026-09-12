# Phase 5 — Realtime speech foundation

**Goal:** a reliable two-way spoken conversation with a model, driven by a local harness,
with no telephony anywhere near it.

## In scope

### The adapter
The first `SpeechProvider` implementation, speaking the OpenAI Realtime-compatible websocket
protocol against an endpoint named in configuration (D-008). It lives entirely in
`adapters/speech/` and exposes nothing beyond the Phase 1 port.

- **Endpoint, model and key are configuration.** No vendor is named in code or defaults. The
  key is read from the environment and never logged.
- **Barge-in is the service's.** The adapter acts on the service's own speech-started signal:
  it cancels the response in flight, tells the service how much of it was actually heard, and
  discards what was queued.
- **Voices are configuration.** A compatible server decides its own voices, so the catalogue
  the voice provider offers is read from settings, and the invented phase 4 list is removed.

- **Session lifecycle.** `connect` establishes the stream; `close` tears it down and is
  idempotent. The session is an async context manager, so the only way to open one without
  closing it is to write code that does not compile past review.
- **Audio in.** Caller audio is pushed as domain audio frames. The adapter converts encoding
  and sample rate at its own edge; no codec name escapes into the core.
- **Audio out.** Model audio is emitted as events on a bounded queue. Bounded deliberately:
  an unbounded queue turns a slow consumer into an out-of-memory failure instead of a
  visible backpressure error.
- **Context updates.** System context can be updated mid-session without a reconnect.
- **Interruption.** Barge-in cancels in-flight model speech and discards queued output.
  Discarding the queue is the part that is easy to miss and the reason interruption feels
  broken when it is missed.
- **Cancellation.** Cancelling the consuming task closes the stream and releases everything.
  Tested by cancelling at several points, including mid-utterance.
- **Reconnection.** Transient failures reconnect with bounded exponential backoff and
  jitter. A reconnect restores session context. A permanent failure surfaces a typed error
  rather than retrying forever.
- **Resource safety.** Every exit path — success, error, cancellation, timeout — releases
  the stream, the queues, and the tasks. Proven by a test that asserts no task and no
  connection survives, not by inspection.

### Independence from the transport

The speech layer must not assume that a call transport supplies audio at all. One of the two
transports implemented in phase 7 cannot (D-005), so an assumption here would become a
platform branch there.

- The session consumes an **audio source** and writes to an **audio sink**, both abstract.
  Neither knows whether frames arrive from a phone call, a microphone, or a file.
- The harness below supplies a microphone source and a speaker sink; phase 7's streaming
  transport supplies a call source and sink. The speech adapter is unchanged between them.
- A speech session can therefore be created, driven, and torn down with no call in existence,
  which is what makes this phase testable before any transport is written.

### Metrics
Recorded from the first session, because latency regressions are invisible without a
baseline: time to first audio out, per-utterance round trip, interruption-to-silence,
reconnection count and duration, and stream error rate. Emitted through the observability
seam that Phase 13 builds on. No transcript content in any metric or log.

### Harness — `scripts/speech_harness.py`
A local command-line tool: microphone in, model out, spoken reply. It uses the port, never
the adapter directly, so it works unchanged against any future speech provider and doubles
as the manual verification for this phase.

Supports injecting system context, forcing an interruption, forcing a disconnect, and
printing the latency summary at exit.

## Explicitly out of scope

- Any call transport, phone number, or call state. Nothing here knows what a call is, and
  nothing here assumes audio comes from one.
- The agent. This phase produces conversation, not decisions.
- Persisting anything. Audio is never written to disk (D-013); transcripts are in memory
  only until Phase 8 owns them.

## Tests required

| Kind | Must prove |
| --- | --- |
| Unit | Session state transitions; the bounded queue applies backpressure rather than growing; interruption discards queued audio; reconnect restores context; backoff is bounded and jittered; a permanent error raises a typed failure. |
| Unit | Cancellation at each stage releases every resource. Asserted by counting live tasks and open streams after the fact. |
| Unit | The session runs against an in-memory audio source and sink, with no call and no transport present, proving the speech layer depends on the abstraction rather than on a call. |
| Contract | The Phase 1 `SpeechProvider` suite passes against the real adapter. |
| Integration | Against an in-process websocket server speaking the protocol, so the real client code runs end to end: a full conversation, an interruption, a mid-stream disconnect and recovery, and a hard failure. No test requires an account to run in CI. |
| Manual | The harness holds a real spoken conversation, interruption works, and the latency summary is recorded in the verification report. |

## Acceptance criteria

1. A spoken conversation runs through the harness, reliably, for several turns.
2. Interruption stops model speech promptly and discards queued audio.
3. Context injected mid-session changes subsequent responses.
4. Closing, cancelling, or failing a session leaks no task, connection, or queue.
5. A transient disconnect reconnects and restores context without ending the conversation.
6. A permanent failure surfaces as a typed domain error.
7. The contract suite passes against the real adapter.
8. Latency metrics are recorded and a baseline is captured in the verification report.
9. CI runs the full suite with no vendor credentials.
10. The speech session works against an audio source and sink that are not a call, proving it
    makes no assumption about where audio comes from.
11. Coverage meets the D-020 floors.

## Risks and open questions

- **Credentials in CI.** Integration tests must not require them. The suite runs against an
  in-process server that implements the same protocol; the real-endpoint run is a manual,
  documented step. The risk that the simulation diverges from reality is mitigated by the
  contract suite being the same one both must pass.
- **No endpoint exists yet.** The service is to be chosen or built later. Everything except the
  manual conversation and the latency baseline (acceptance criteria 1 and 8) can be completed and
  verified without one; those two are held until an endpoint is configured, and the verification
  report says so rather than marking them passed.
- **Protocol dialects.** Compatible servers differ in which events they implement and in older
  versus current event names. The adapter declares capabilities from what the service actually
  supports, and treats an unrecognised event as ignorable rather than fatal.
- **Sample-rate mismatch with a call transport.** Phase 7 introduces 8 kHz narrowband audio.
  The frame type carries its rate from phase 1 and conversion is an adapter concern, so this
  is a known integration point rather than a surprise.
- **A transport that supplies no audio at all.** The Android native transport cannot stream a
  SIM call's audio. That is not a gap in this phase: it means the speech session simply is not
  created on that path, decided by capability in phase 8.

---

# Phase 5 verification

```
PHASE 5 VERIFICATION

Planned tasks:        complete, except the live run the plan names as a manual step
Acceptance criteria:  9 of 11 passed; 1 and 8 held — both need a live speech service
Unit tests:           passed   backend 1251 passed, 3 skipped; mobile 179 passed, 17 suites
Contract tests:       passed   SpeechProviderContract against both adapters, unmodified
Integration tests:    passed   both adapters end to end over a real socket against in-process
                               services that require what the real ones require: a whole
                               conversation, an interruption, a drop and recovery with the latest
                               context, a refused key, and the websocket's failure mapping
E2E tests:            held     a spoken conversation with a live service (criterion 1)
Coverage:             100.00%  backend, floor 98; mobile unchanged at 98.5% statements
Lint:                 passed   ruff check; eslint --max-warnings 0; prettier --check
Format:               passed
Typecheck:            passed   mypy --strict; tsc --noEmit
Static analysis:      passed   import-linter, 3 contracts kept; websockets forbidden in the domain
Build:                passed   backend image; generated API types match the schema
Application runs:     yes      no migration; migrations run without a voice catalogue, the API
                               refuses to start without one
Manual verification:  held     the harness is built; no live endpoint is configured yet
Docs updated:         D-008 amended, docs/providers/speech.md, the phase plan
Known issues:         none open in code; criteria 1 and 8 held, below
Commits created:      52
```

## Acceptance criteria, each with its evidence

| # | Criterion | Evidence |
| --- | --- | --- |
| 1 | A spoken conversation runs through the harness, reliably, for several turns | **Held.** `scripts/speech_harness.py` is built and exercises the port, the use case, a source and a sink. The same path runs end to end in CI; a live service is not yet configured |
| 2 | Interruption stops model speech promptly and discards queued audio | The reader never waits on the consumer, so a caller's speech is acted on while a speaker is still playing: a 10 s reply is cancelled and truncated at 10 ms delivered. End to end, the service must receive a cancel and a truncate after the caller speaks, and a test fails if session-side silencing is deleted |
| 3 | Context injected mid-session changes subsequent responses | `update_context` reaches the service mid-session, and an update made while a replacement is being configured is held and sent once it is live |
| 4 | Closing, cancelling or failing a session leaks nothing | Tasks and connections counted after every exit path, including cancellation mid-handshake, during backoff, and while a replacement is configured; closing is bounded when a service stops reading |
| 5 | A transient disconnect reconnects and restores context | The replacement's first event carries the latest instructions, and a test fails if restoration is deleted. ElevenLabs cannot resume a conversation, so its replacement is a new one told what was said (D-008) |
| 6 | A permanent failure surfaces as a typed domain error | A revoked key ends the conversation with `ConversationFailedError(retryable=False)`; attempts are bounded across replacements that accept and then close; three failed responses in a row end the session |
| 7 | The contract suite passes against the real adapter | Both adapters, unmodified |
| 8 | Latency metrics are recorded and a baseline captured | **Held.** Time to first audio, round trip, interruption to silence, reconnections and stream errors are recorded, and the harness prints a summary. A baseline needs a live service |
| 9 | CI runs the full suite with no vendor credentials | Every test runs against in-process services |
| 10 | The session works against a source and sink that are not a call | `test_a_conversation_runs_with_no_call_transport_or_phone_number_present` |
| 11 | Coverage meets the floors | 100% backend |

## What changed from the plan, and why

**No vendor was chosen.** The plan assumed one managed speech model. The first adapter speaks a
protocol instead, against a configured endpoint, and a second speaks the ElevenLabs Agents
protocol; `SPEECH_PROVIDER` chooses (D-008). The voice catalogue became configuration, which
removed the invented phase 4 list.

**Test sources live in memory.** The plan said file-backed. A file source would be the one place
audio touched a disk (D-013).

## Reviews, and what they found

An adversarial review ran against a green suite at 100% coverage and reproduced ten defects.
Integration found three more before it. Every fix shipped with a test written from the
reproduction, and each was shown to fail without its fix.

- **Barge-in was seconds late.** The reader waited for room in a bounded queue, so a consumer
  playing at the speed of speech left the caller's interruption unread behind queued audio. Only
  audio is bounded now, by duration, and control events never wait.
- **A service that accepted and then dropped every connection was reconnected to forever.**
  Attempts now carry across replacements until one delivers something real.
- **Debug logging printed the key and what a caller said**, through the websocket library.
- **A context update made during a reconnect was lost**, and the restored session ran on the old
  instructions.
- **A failed response was silence**, with no event and no metric.
- **Closing hung for half a minute** when a service stopped reading.
- **A metrics label the production recorder refuses** would have raised on the first stream
  error; the test recorder accepted anything.
- **The simulated service could never end a turn**, because it treated only exact zeros as silence.
- **Caller transcription was never requested**, so a real service would have recorded one side.
- **Two tests passed with their feature deleted**, including an interruption test that never
  interrupted anything.

## Held until a live service is configured

Criteria 1 and 8. For ElevenLabs, the agent must allow overrides for the prompt, first message,
language and voice, keep the transcript and interruption events enabled, and the key must be
permitted to use the Agents platform — `docs/providers/speech.md` lists the steps. Then:

```
cd apps/backend && uv sync --group harness && uv run python ../../scripts/speech_harness.py
```

and the latency summary it prints is recorded here.
