# Call transport

How a call reaches the product, what each way of reaching it can do, and how another would be
added. The operational detail of each implemented transport — webhooks, payloads, console setup —
is in [`docs/providers/call-transport.md`](../providers/call-transport.md). This page is the
design.

## The port

`CallTransport`, in
[`apps/backend/src/letmehandle/domain/ports/call_transport.py`](../../apps/backend/src/letmehandle/domain/ports/call_transport.py).

It is named for the concern, not for a vendor (D-004). A programmable telephony account, a
handset's own call screening service, a SIP trunk and a carrier's network are all ways a call
exists, and they differ in kind rather than in supplier: one sees a call before the handset rings
but never hears it, another hears it but never sees it before it connects. A port named after one
of them would describe that one's shape and force every other into it.

Every transport provides four things: its `name` (for logs and metrics, never a decision), its
`capabilities`, the `events` of its calls, and `terminate`. Everything else depends on a capability
and is reached by narrowing:

| Narrowing | Requires | Operations |
| --- | --- | --- |
| `answering(t)` | `can_answer_under_program_control` | `answer` |
| `screening(t)` | `can_screen_before_ringing` | `screening_decisions`, `screening_deadline` |
| `audio_streaming(t)` | `can_stream_call_audio_to_ai`, `can_inject_ai_audio` | `stream_audio`, `inject_audio`, `audio_format`, `audio_source`, `audio_sink` |
| `bridging(t)` | `can_bridge_human` | `add_participant`, `remove_participant` |
| `three_way(t)` | `supports_three_way_call` | `set_assistant_presence` |

A caller that has not narrowed cannot name the method, which is the static half of the guarantee.
The narrowing raises `CapabilityNotSupportedError` when a transport's declaration and its
implementation disagree, which is the runtime half. `TransportCapabilities` also refuses two
declarations that cannot be true together: injecting audio without hearing the caller, and a
three-way call without being able to add the third party.

## `AndroidNativeCallTransport`

`TELEPHONY_PROVIDER=android_native`. The call is the user's own SIM call, on the user's own
Android phone.

**What the platform provides.** A call screening service is given each incoming call before the
phone rings, with a deadline of about five seconds, and may let it ring, reject it, or silence it.
Afterwards the phone can observe that the call was answered and that it ended.

**What it does not.** An application is never given the audio of a SIM call, cannot put audio onto
one, cannot answer one on the user's behalf, and cannot add a third party to one. So this transport
declares none of those capabilities. Declaring audio it cannot have would put the product in front
of a caller with an assistant that cannot hear them (D-005).

**How it is built.** The decision is made on the handset, within the deadline, from a snapshot of
the user's deterministic rules (D-028); a server round trip would arrive after the phone rang. The
backend represents the handset as a transport whose events arrive afterwards: the phone reports
what happened to `POST /v1/calls/reports`, and each accepted report becomes an event on this
transport's feed. `terminate` cannot hang up anybody's phone; it releases the transport's interest
in the call.

## `TwilioCallTransport`

`TELEPHONY_PROVIDER=twilio`. The call is forwarded by the user's carrier to a programmable
telephony number (D-033, D-034).

- **Streaming.** Each call is answered straight into a conference of its own (D-027). `answer`
  dials the assistant into that conference as a participant whose media is a bidirectional
  websocket stream to the backend: μ-law at 8 kHz, converted at the adapter's edge.
- **Injection.** `audio_sink(call)` is where the assistant's voice goes. It paces itself against
  playback, and its `discard` clears audio already sent, so an interrupted assistant stops talking.
- **Dialling.** `add_participant` dials the user into the same conference with voicemail
  detection. How the dial turned out arrives later, as an event: joined, or unreachable with
  `no_answer`, `busy`, `failed` or `answered_by_machine`.
- **Bridging.** Nobody is transferred. The caller stays where they are, and the user joins them.
  `set_assistant_presence` then chooses whether the assistant stays, only listens, speaks only to
  the user, or leaves.

It never sees a call before the call connects, and it rings nothing natively: the user's phone
rings because the transport dialled it.

## Capability matrix

What each transport declares in code. A test,
`apps/backend/tests/unit/test_capability_matrix_document.py`, builds both transports through
bootstrap and fails when this table and their declarations disagree.

| Capability | `android_native` | `twilio` |
| --- | --- | --- |
| `can_answer_under_program_control` | no | yes |
| `can_screen_before_ringing` | yes | no |
| `can_stream_call_audio_to_ai` | no | yes |
| `can_inject_ai_audio` | no | yes |
| `can_bridge_human` | no | yes |
| `supports_three_way_call` | no | yes |
| `supports_native_ringing` | yes | no |

### What each combination supports

The orchestrator derives a plan for every call from these flags before anything happens to it
(D-029). A step the plan does not contain is never built, so a behaviour below that says "no" is a
path that does not exist, not a request that is refused.

| Product behaviour | Needs | `android_native` | `twilio` |
| --- | --- | --- | --- |
| The user's rules decide a call before the phone rings | `can_screen_before_ringing` | yes, on the handset | no |
| A call the rules put through reaches the user | `can_bridge_human`, or `supports_native_ringing` | yes, it rings where it is | yes, the user's phone is dialled into the call |
| A call the rules reject is refused | nothing | yes, rejected or silenced on the handset | yes, ended at the transport |
| The assistant answers and talks to the caller | `can_answer_under_program_control`, `can_stream_call_audio_to_ai`, `can_inject_ai_audio` | no | yes |
| The agent judges the call as it happens | an assistant step | no | yes |
| The user is brought into the live call when needed | an assistant step and `can_bridge_human` | no | yes |
| The assistant stays, listens, coaches or leaves once the user joins | `supports_three_way_call` | no | yes |
| A summary of what the assistant handled | an assistant step | no; the call is summarised from its facts | yes |
| The user must forward their line to the deployment | not a capability (D-034) | no | yes |

When the user's rules ask for something the plan cannot do, routing falls back in a fixed order
(`application/orchestration/routing.py`): a call to be handled by the assistant is put through
instead, a call to be put through is handled instead, and rejecting is always possible.

## Selection

Once, in `build_call_transports` in
[`apps/backend/src/letmehandle/bootstrap.py`](../../apps/backend/src/letmehandle/bootstrap.py), from
`TELEPHONY_PROVIDER` or `TELEPHONY_LINES` — never both:

| Configured | Transports | Routes they mount |
| --- | --- | --- |
| neither | none: the deployment carries no calls | none |
| `TELEPHONY_PROVIDER=android_native` | the application's one `AndroidNativeCallTransport` | none of its own; handset reports use `POST /v1/calls/reports`, which exists in every deployment |
| `TELEPHONY_PROVIDER=twilio` | one `TwilioCallTransport`, from the `TELEPHONY_*` account variables, serving every region | `/telephony/...` callbacks and the media websocket |
| `TELEPHONY_LINES` | a `TwilioCallTransport` for each line, from that line's entry and token | each line's under `/lines/<name>/telephony/...` |

Bootstrap hands the application a `CallTransportBinding` per line: the transport, its routes, how to
close it, and the `CallOwnership` that says whose call each call is. Nothing downstream learns which
transport it has. A test asserts that no module outside bootstrap and the adapters names one.

**Overriding the default.** In a deployment, set `TELEPHONY_PROVIDER` or `TELEPHONY_LINES`. In code,
`create_app` accepts `telephony` bindings, which is how the end-to-end suite runs the whole
application over a simulated provider — or two; production passes nothing and gets what
configuration chose.

Every transport also needs `DATABASE_URL` and `TRANSCRIPT_ENCRYPTION_KEYS`, and the process refuses
to start without them: calls are recorded as they happen, sealed.

## Lines by region

One deployment can serve users in several countries, each region's calls on a line of its own
(D-041). A user's **region** is read from the country calling code of the number they signed in with
(`domain/models/region.py`: `1` is `US`, `91` is `IN`). A **line** is one provider account, its
numbers, and the regions it serves; `regions=*` serves every region no other line does.

```
 US user's carrier ──forwards──► line "us" (+1 number) ──┐
                                                          ├──► one CallOrchestrator ──► storage
 IN user's carrier ──forwards──► line "in" (+91 number) ──┘         │
                                                                    └─ each call acts on its own line:
                                                                       answer, dial the user, end
```

- **Where each user forwards.** `ForwardingNumbers` gives each region the first number of the line
  serving it, and everybody else the `*` line's. `GET /v1/me` and the setup flow ask it for the
  signed-in user. A user no line serves gets `call_forwarding: null` and is not asked the
  forwarding step; none of their calls can reach the deployment.
- **One orchestrator.** It reads every line's events. A call keeps the line it arrived on: its plan
  comes from that transport, its owner from that line's ownership, and the user is dialled from that
  line — a number in their own country — and the call ended there.
- **Routes.** A line's callbacks are under `/lines/<name>`, and every URL its transport gives the
  provider carries the prefix, so two lines of one provider receive only their own callbacks. The
  line `TELEPHONY_PROVIDER` configures stays at the root.
- **After a restart** every line is asked to end each call left unfinished; the lines that never
  carried it find nothing.
- **Sign-in codes** follow the same region by calling code, through `OTP_PROVIDER_BY_CALLING_CODE`.
- **Shared.** The telephony circuit (D-038) is one for all lines.

## Adding a transport

1. Implement `CallTransport` in `adapters/transport/<name>/`, plus the protocol for every
   capability it declares, and nothing it does not.
2. Pass the contract suite, `apps/backend/tests/contracts/call_transport.py`, unmodified, by adding
   a `test_<name>_call_transport_contract.py` that provides a `transport` fixture.
3. Provide a `CallOwnership` (`application/orchestration/ports.py`) that says whose call each call
   is, from what the transport knows.
4. For a transport that is a provider account taking forwarded calls, add a member to
   `LineProviderName` in `config/telephony_lines.py` and a case to `_line_binding` in bootstrap;
   for another kind, a member of `TelephonyProviderName` and a case in `build_call_transports`.
   Either match is exhaustive, so a member without a case fails type checking.
5. Add its column to the matrix above, and its settings with descriptions; `make verify` fails
   until both are done.

No domain or orchestration code changes. If it seems to need to, the port is missing a capability,
and that is worth an issue before a workaround.

## Extension path: SIP, carrier and IMS

None of these is implemented (D-005). Each is described by what it would declare, because that is
all the rest of the product would ever learn about it.

### A SIP transport

A SIP trunk or a registrar in front of the user's phone, with a media server the backend controls.

| Capability | Declared | Why |
| --- | --- | --- |
| `can_answer_under_program_control` | yes | the server answers the INVITE |
| `can_stream_call_audio_to_ai`, `can_inject_ai_audio` | yes | RTP terminates at the media server |
| `can_bridge_human`, `supports_three_way_call` | yes | a conference or a mixer on the media server |
| `can_screen_before_ringing` | only as the user's registrar | it then receives the INVITE before forking it to the phone |
| `supports_native_ringing` | only as the user's registrar | forking to the registered phone rings it |

As a trunk it is the streaming transport's shape without the vendor. As the registrar of a SIP
phone it declares everything, and that unlocks a combination neither implemented transport offers:
the rules decide before ringing, and the assistant can still take the call and bring the user in.

### A carrier integration

Call screening in the carrier's network, before the call is delivered to the handset.

| Capability | Declared | Why |
| --- | --- | --- |
| `can_screen_before_ringing` | yes | the network decides before it terminates the call |
| `supports_native_ringing` | yes | an allowed call is delivered to the user's phone as usual |
| `can_answer_under_program_control`, audio, bridging | only where the carrier anchors media and exposes it | otherwise the carrier hands over no audio, as a handset does not |

With screening alone, it behaves like the handset transport without an app on the phone. With media
exposed, it declares everything, and the user needs no call forwarding.

### An IMS application server

A service in the mobile network's IMS core, on the call's signalling path for the user's own number.

| Capability | Declared | Why |
| --- | --- | --- |
| `can_screen_before_ringing` | yes | the application server sees the terminating INVITE first |
| `supports_native_ringing` | yes | letting the INVITE continue rings the user's own dialer |
| `can_answer_under_program_control` | yes | the server can answer in the network |
| `can_stream_call_audio_to_ai`, `can_inject_ai_audio` | yes | with a media resource function anchoring the call |
| `can_bridge_human`, `supports_three_way_call` | yes | conferencing in the core |

The one transport that could honestly declare every capability. What becomes available is the whole
product on the user's own number: screening before ringing, the assistant on the call, escalation
into it, and the user's native dialer ringing, with nothing forwarded and no app in the call path.
