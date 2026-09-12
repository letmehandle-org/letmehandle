# CallTransport

How a call reaches the product. Two transports differ in kind rather than in supplier (D-005), so
the core asks what a transport can do and never which one it is: the streaming transport carries
a call's audio and can add the user to it, and the on-device transport screens a call on the
user's handset before it rings.

Interface: `apps/backend/src/letmehandle/domain/ports/call_transport.py`
Contract suite: `apps/backend/tests/contracts/call_transport.py`, run against both transports
Adapters, chosen in bootstrap by `TELEPHONY_PROVIDER`:

- `adapters/transport/twilio/` — `TELEPHONY_PROVIDER=twilio`
- `adapters/transport/android_native/` — `TELEPHONY_PROVIDER=android_native`

## Capabilities

| Capability | Streaming | On-device |
| --- | --- | --- |
| `can_answer_under_program_control` | yes | no — the person holding the handset answers |
| `can_screen_before_ringing` | no — it never sees a call before it connects | yes |
| `can_stream_call_audio_to_ai` | yes | no — the platform never hands over a SIM call's audio |
| `can_inject_ai_audio` | yes | no |
| `can_bridge_human` | yes | no |
| `supports_three_way_call` | yes | no |
| `supports_native_ringing` | no | yes |

An operation behind a capability is reached by narrowing, and each narrowing raises
`CapabilityNotSupportedError` naming the capability on a transport that does not declare it:

| Narrowing | Protocol | Operations |
| --- | --- | --- |
| `answering` | `SupportsAnswering` | `answer` |
| `screening` | `SupportsScreening` | `screening_decisions`, `screening_deadline` |
| `audio_streaming` | `SupportsAudioStreaming` | `stream_audio`, `inject_audio`, `audio_format`, `audio_source`, `audio_sink` |
| `bridging` | `SupportsBridging` | `add_participant`, `remove_participant` |
| `three_way` | `SupportsThreeWayCall` | `set_assistant_presence` |

`name`, `capabilities`, `events` and `terminate` are on every transport. Every event uses the one
vocabulary below; a transport reports only the kinds it can observe.

## The streaming transport

### The shape of a call (D-027)

```
caller ──► conference "call-<call id>" ◄── assistant leg ──► media websocket ──► speech session
                     ▲
                     └──────────── user leg (dialled when the policy asks)
```

1. A call arrives. The number's voice webhook answers it straight into a conference of its own:
   no beep, a silent wait, the smallest jitter buffer, never recorded, and the conference ends when
   the caller leaves. The caller's leg is never touched again. The dial's action,
   `/telephony/voice/caller-left`, is requested on the caller's own leg when their time in the
   conference is over, so the call ends even when every conference callback saying so is lost.
2. `answer` dials the assistant into the conference as a participant whose destination is a
   provider-side application. On this transport that is what answering under program control
   means: the caller was answered on arrival, and answering puts the assistant on the call. The application's voice webhook returns a bidirectional stream to
   this service's media websocket.
3. `add_participant` dials the user into the same conference, with voicemail detection.
4. `set_assistant_presence` chooses what the assistant does while the user is there — stay, listen
   only (muted), speak only to the user (coaching them), or leave (removed). Before the user joins,
   and after the last one leaves, the assistant is audible to the caller.
5. `terminate` ends the conference, and the caller's leg in case the conference never started, and
   cancels any leg still ringing. It is safe to call more than once. A dial still being placed
   finishes first, so its leg can be cancelled too. When the provider refuses one step the rest
   are still tried and the call is released, and then the first refusal is raised. Shutting the
   service down terminates every call in progress the same way, for at most five seconds, and
   then releases whatever is left.

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
account is refused too. A body over 64 KiB is refused with `413` before it is read, by its declared
length or as it arrives, so a forged callback is never held in memory to find out it was forged.

The handshake's signature is the same for every call, and a leg's call identifier is no secret, so
neither decides which leg a socket carries. The assistant's instructions carry a random token as a
stream parameter; the stream's `start` must present it, it is compared in constant time, and it is
good for one `start`. A socket that has not sent `start` five seconds after the handshake is closed.

The provider duplicates, reorders and drops callbacks. Repeats are recognised by the idempotency
token header and by the provider's own identifiers (conference and sequence number; call, sequence
number and status). State is resolved by sequence number, not arrival: a join arriving after the
leave that followed it is stale. A leg reported completed after it joined has left, whether or not the conference's leave
for it ever arrives. A leg reported completed without having joined is given two
seconds for a delayed join or leave to arrive before it is reported unreachable — unless it is a
user's leg known to have been answered, which is reported as having joined and left. A join or
leave arriving after a leg was reported unreachable that way corrects it: joined, then left.

Handlers change state and return; anything that needs the network runs as a task the transport
owns, and every such task, socket and stream is released with its call.

## The on-device transport

The decision is taken on the handset (D-028). Android gives a call screening service five seconds
from `onScreenCall` to respond and then rings regardless, so the app keeps a snapshot of the user's
deterministic call rules and the screening service evaluates it locally, within a three-second
budget. No snapshot, a stale or unreadable one, a failed evaluation or an exhausted budget all let
the call ring.

The backend represents the handset as a transport whose events arrive afterwards. The handset
reports what happened to `POST /v1/calls/reports`; reports are stored per user, a repeat of the
same event id counts once, and each accepted report is published as a call event on the handset
transport's feed. Each report in a batch is read on its own: the answer is a `200` listing
`accepted`, `duplicates` and `rejected` — each rejection with its `index` in the batch, its
`event_id` when one could be read, and a `reason` — so one report the handset got wrong is dropped
by itself instead of holding back the rest. A caller number is E.164 as the handset writes it,
`^\+[1-9][0-9]{1,14}$`. A batch that is not a list of at most 100 reports is refused whole with `422`.
One account may send thirty requests a minute, and a request over that is refused with `429` and
`Retry-After`. At most a hundred of one account's events wait on the live feed; past that they
are stored and not published, so one handset reporting in a loop cannot crowd out anybody else's. The route exists whichever transport is configured, so a handset's reports are
never lost to configuration; `TELEPHONY_PROVIDER=android_native` makes that feed the transport the
product reads.

- **Screening.** `screening_decisions()` is allow, reject and silence; `screening_deadline()` is
  the platform's five seconds. There is no screening command: one sent from the backend would
  arrive after the phone rang. The decision taken is carried on the call's `incoming` event.
- **Events.** `incoming` (with the decision and, when the platform shows it, the caller),
  `answered` and `ended` — what a handset can observe about its own call without being the phone
  app. Participant events never occur on this transport.
- **Terminate.** Nobody on a server can hang up a handset's call. `terminate` releases the
  transport's interest: later reports for that call are still stored, and no longer published.

What the platform does not show a screening service is not claimed: callers in the user's contacts
and callers withholding their number always ring on this path.

The handset side — the screening service, the phone-state receiver, the rules snapshot and the
TurboModule — lives in `apps/mobile`, with the payloads on both sides of the bridge defined once
and checked against `apps/mobile/src/calls/wire-examples.json`.

## Configuration

| Variable | Meaning |
| --- | --- |
| `TELEPHONY_PROVIDER` | `twilio`, `android_native`, or empty for none. Only `twilio` mounts the streaming routes |
| `TELEPHONY_ACCOUNT_ID` | The account identifier the REST API authenticates as |
| `TELEPHONY_AUTH_TOKEN` | The auth token. Signs every callback; never logged |
| `TELEPHONY_NUMBERS` | Numbers calls are placed from, comma-separated E.164 |
| `TELEPHONY_APP_ID` | The provider-side application the assistant joins through |
| `TELEPHONY_WEBHOOK_BASE_URL` | The public URL the provider calls, with no query or fragment |

The other variables are the streaming transport's account. When `TELEPHONY_PROVIDER=twilio`, the
process refuses to start naming every one missing; the on-device transport needs none of them.

## Setting up a streaming account

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
