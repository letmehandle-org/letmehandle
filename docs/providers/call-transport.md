# CallTransport

How a call reaches the product. Two transports differ in kind rather than in supplier (D-005), so
the core asks what a transport can do and never which one it is. This page documents the
streaming transport; the on-device transport has its own section when it lands.

Interface: `apps/backend/src/letmehandle/domain/ports/call_transport.py`
Contract suite: `apps/backend/tests/contracts/call_transport.py`
Adapter: `adapters/transport/twilio/`, chosen by `TELEPHONY_PROVIDER=twilio`

## The streaming transport

Declares `can_stream_call_audio_to_ai`, `can_inject_ai_audio`, `can_bridge_human` and
`supports_three_way_call`. It never sees a call before the call connects, so it declares no
screening.

### The shape of a call (D-027)

```
caller ──► conference "call-<call id>" ◄── assistant leg ──► media websocket ──► speech session
                     ▲
                     └──────────── user leg (dialled when the policy asks)
```

1. A call arrives. The number's voice webhook answers it straight into a conference of its own:
   no beep, a silent wait, the smallest jitter buffer, never recorded, and the conference ends when
   the caller leaves. The caller's leg is never touched again.
2. `answer` dials the assistant into the conference as a participant whose destination is a
   provider-side application. The application's voice webhook returns a bidirectional stream to
   this service's media websocket.
3. `add_participant` dials the user into the same conference, with voicemail detection.
4. `set_assistant_presence` chooses what the assistant does while the user is there — stay, listen
   only (muted), speak only to the user (coaching them), or leave (removed). Before the user joins,
   and after the last one leaves, the assistant is audible to the caller.
5. `terminate` ends the conference, and the caller's leg in case the conference never started, and
   cancels any leg still ringing. It is safe to call more than once.

### What the orchestrator hears

| Event | Participant | Outcome | When |
| --- | --- | --- | --- |
| `incoming` | | | The call arrived; carries the caller |
| `answered` | | | The caller is in their conference |
| `participant_joined` | `assistant` / `user` | `answered` for the user | A leg joined the conference |
| `participant_unreachable` | `assistant` / `user` | `no_answer`, `busy`, `failed`, `answered_by_machine` | A leg was dialled and never joined |
| `participant_left` | `assistant` / `user` | | A leg left; the call stands |
| `ended` | | | The caller left, the conference ended, or `terminate` ran — once |
| `failed` | | | Work a callback started could not finish (applying a presence, say) |

A dial's outcome is an event rather than a return value: a phone rings for as long as it rings, and
nothing should be waiting on it. None of these leaves the caller in silence without the
orchestrator hearing about it.

### Audio

The media websocket carries base64 μ-law at 8 kHz. `audio_source(call)` and `audio_sink(call)` are
the phase 5 source and sink, so a `Conversation` runs over a call exactly as it runs over the
harness. The sink converts whatever the speech session produces at its own edge, paces itself to at
most 400 ms ahead of playback, and its `discard` sends the stream's `clear`. Arriving audio waits
in a bounded queue that drops the oldest frame when a listener falls behind.

### Callbacks

Every HTTP callback and the websocket handshake is checked against `X-Twilio-Signature` before
anything in it is read, over the URL built from `TELEPHONY_WEBHOOK_BASE_URL` — not the Host the
request arrived with, which behind a tunnel is not what was signed. A genuine signature from another
account is refused too.

The provider duplicates, reorders and drops callbacks. Repeats are recognised by the idempotency
token header and by the provider's own identifiers (conference and sequence number; call, sequence
number and status). State is resolved by sequence number, not arrival: a join arriving after the
leave that followed it is stale. A leg reported completed without having joined is given two
seconds for a delayed join or leave to arrive before it is reported unreachable.

Handlers change state and return; anything that needs the network runs as a task the transport
owns, and every such task, socket and stream is released with its call.

## Configuration

| Variable | Meaning |
| --- | --- |
| `TELEPHONY_PROVIDER` | `twilio`, or empty for no streaming calls (the routes then do not exist) |
| `TELEPHONY_ACCOUNT_ID` | The account identifier the REST API authenticates as |
| `TELEPHONY_AUTH_TOKEN` | The auth token. Signs every callback; never logged |
| `TELEPHONY_NUMBERS` | Numbers calls are placed from, comma-separated E.164 |
| `TELEPHONY_APP_ID` | The provider-side application the assistant joins through |
| `TELEPHONY_WEBHOOK_BASE_URL` | The public URL the provider calls, with no query or fragment |

When `TELEPHONY_PROVIDER` is set, the process refuses to start naming every variable missing.

## Setting up an account

In the provider's console, with `BASE` standing for `TELEPHONY_WEBHOOK_BASE_URL`:

1. **The number.** Voice webhook: `BASE/telephony/voice/incoming`, method `POST`.
2. **The application.** Create a TwiML application. Voice URL: `BASE/telephony/voice/assistant`,
   method `POST`. Its identifier is `TELEPHONY_APP_ID`.
3. Status callbacks and the media websocket URL are set by the transport itself on every call.

### Developing against real callbacks

The provider must reach your machine. Run any HTTPS tunnel to the backend's port and set
`TELEPHONY_WEBHOOK_BASE_URL` to the tunnel's public URL, exactly as the tunnel presents it. Point
the number and the application at that URL. Signatures are checked against it, so a mismatch —
a different path prefix, a trailing slash the tunnel adds — is rejected with `403` and logged as
`telephony.webhook.rejected`.

No account is needed for the test suite: `tests/support/simulated_twilio.py` answers the REST API,
signs every callback exactly as the provider does, opens real websockets to the application on
loopback, and can duplicate, reorder and drop callbacks, fail a dial, and hang up either party.

## Verified on the first real call, not here

The documentation does not settle these, and the code tolerates either answer where it can.
Each is recorded in the phase 7 verification report once observed:

- What the assistant leg's inbound track contains: the conference mix without the assistant's own
  audio is expected.
- Whether a participant created through the REST API sends its conference callbacks to the URL
  given at creation, the caller's conference TwiML, or both (both are set; repeats are inert).
- Whether custom parameters on an `app:` destination reach the application's voice webhook. When
  they do not, the assistant leg is recognised by its call identifier instead.
- Whether the idempotency token is the same on every retry of one callback.
- When voicemail detection reports `AnsweredBy` for a conference participant, and whether the leg
  joins the conference before it does.
- Whether setting `Coaching=false` with no call to coach is accepted when returning to `stay`.
- The frame size of arriving media, and the latency the conference mixer adds with the small
  jitter buffer.
- Whether the websocket handshake is signed with a trailing slash.
