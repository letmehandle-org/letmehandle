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
