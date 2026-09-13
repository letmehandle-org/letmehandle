# Diagnosing a call

How to find out what happened to one call, and why, from its identifiers alone — without reading
what anybody said on it. Nothing below needs a transcript key, and nothing below shows a number,
a name or a word of the conversation. D-038 records why that is enough, and how it is kept so.

The procedure is run end to end, against the whole system, by the scenario in
`apps/backend/tests/e2e/test_observability.py`.

## What you start from

One of:

- **A call id.** The provider's call identifier for a streaming call, or the handset's own for a
  reported one. The app's call detail is fetched by it (`GET /v1/calls/{call_id}`).
- **A correlation id.** Every error response carries one (`"correlation_id"`), and every response
  returns it in the `x-correlation-id` header. A person reporting a failure quotes it.

Diagnostics must be configured: set `DIAGNOSTICS_TOKEN` (at least 32 characters) on the
deployment. Without it the `/diagnostics` routes do not exist. Every request below sends it:

```
Authorization: Bearer <DIAGNOSTICS_TOKEN>
```

## 1. Is the call still live, and where is it?

```
GET /diagnostics/calls
```

Every call the answering process holds, oldest state first:

```json
{"calls": [{"call_id": "CA…", "state": "human_ringing", "since": "…", "seconds_in_state": 41.2}]}
```

A call whose `seconds_in_state` is longer than that state's bound is stuck in it. The bounds are
`Bounds` in `application/orchestration/ports.py`: a ring 30 s, a judgement 20 s, speech opening
10 s, a provider request 10 s. Several processes each list their own calls; ask each, or read the
timeline below, which is stored.

## 2. What did the call go through?

```
GET /diagnostics/calls/{call_id}
```

The call's stored outline and timeline:

- `state`, `handling` (`assistant` or `passed_through`), `started_at`, `ended_at`, `escalated_at`, `outcome`.
- `participants`: roles (`agent`, `human`) with when each joined and left.
- `escalation`: why the user was asked for, whether it is live or ended, and what became of the
  push (`delivered`, `failed`, `no_devices`, `pending`).
- `marks`, in the order they happened:

| `kind` | `name` | Means |
| --- | --- | --- |
| `transition` | a state: `received`, `routing`, `agent_handling`, `passthrough`, `escalation_requested`, `human_ringing`, `human_joined`, `completed`, `rejected`, `failed` | The call entered that state. |
| `failure` | `<stage>.<kind>`: `answer.timeout`, `dial.refused`, `terminate.unavailable`, `speech.circuit_open`, `judgement.unavailable`, `summary.timeout`, `conversation.lost`, `storage.<write>.<kind>` | That stage failed, by the kind of failure. |
| `degraded` | `speech`, `summary` | The call went without that dependency because its circuit was open: put through to the user instead of the assistant, or summarised from its facts. |

- `live`: where it stands now, when this process holds it.

A `404 not_found` means no call with that id was stored: either it never reached the product, or
nobody owned it — a call nobody owns is let go and recorded nowhere (D-033). Look for it in the
logs, step 3.

### Reading a timeline

- **Ends in `failed` right after `agent_handling`, with `answer.*`:** the transport would not
  answer. With `speech.*`: the speech service would not open.
- **`escalation_requested`, `dial.refused`, back to `agent_handling`:** the user could not be
  dialled; the assistant was told and kept the call.
- **`human_ringing` then `agent_handling` with no `human_joined`:** the user did not answer, was
  busy, or a machine answered. `call.escalation_resolved` (step 4) says which.
- **`failure` marks with `circuit_open`:** that dependency had been failing across calls, not just
  this one. Check readiness (step 5).
- **A `storage.*` failure:** a write failed, and the mark was stored with the next write that did
  not. The call may still have completed; its final state is authoritative.

## 3. The logs

Every line a call's run writes carries `call_id`, and the `correlation_id` of the request that
announced the call. Every request line carries its own `correlation_id`.

- Filter by `call_id` for the call's own account of itself.
- Filter by `correlation_id` to join it to the provider callback or app request that started it.
- `error`-level lines are the ones to act on: a defect, a record that would not open, a circuit
  opening. Everything else is a `warning` with `error` (an exception type) and `kind`.

A call id that could be mistaken for a number is logged and traced as `untraceable`; such a call is
diagnosed by its correlation id and its timeline.

## 4. Latency and counts

```
GET /diagnostics/metrics
```

Counts since the process started, measurements over the most recent thousand per series as
`p50`, `p90`, `p99` and `maximum`, and each circuit's state. The latencies at each boundary:

| Metric | Boundary |
| --- | --- |
| `call.provider_seconds{stage}` | each telephony request: `answer`, `dial` (the bridge), `cancel`, `terminate` |
| `call.speech_open_seconds{outcome}` | opening the speech session |
| `speech.time_to_first_audio_seconds`, `speech.round_trip_seconds` | the speech service's first reply, and each turn |
| `call.judgement_seconds{outcome}` | one agent decision |
| `call.summary_seconds{outcome}` | writing the summary at teardown |
| `escalation.delivery_seconds{platform,provider}` | one push delivery |
| `call.state_seconds{outcome}` | time spent in a state, labelled by the state left |

Counts that explain outcomes: `call.routed`, `call.escalation_resolved`, `call.provider_failed`,
`call.judgement_failed`, `call.degraded`, `call.duplicate_ignored`, `telephony.callback_repeated`,
`escalation.delivery`, `circuit.transition`. Every label and every value a label can take is
declared beside the code that records it (`observability/catalogue.py`).

## 5. The dependencies

```
GET /health/ready
```

No token needed. `checks.database`, each circuit by role under `dependencies` (`closed`, `open`,
`half_open`), and `rate_limits` (`shared` or `per_process`). An open circuit means that
dependency is failing for every call; see `docs/operations/failure-handling.md` for what the
product does meanwhile.

## 6. The trace

With `TRACING_OTLP_ENDPOINT` set, find the trace in your tracing backend by the span attribute
`call.id`. One call is one tree under a `call` span: `call.routing`, `telephony.answer`,
`speech.open`, `agent.judgement`, `call.escalation` with `telephony.dial` and
`notification.delivery` beneath it, and `call.teardown` with `telephony.terminate` and
`summary.write`. Each provider callback is a `telephony.callback` span of its own request, with the
same `call.id`. A failed span has an error status and `failure.kind`, never a message.
