# Failure handling

What the backend does when something it depends on fails: how a failure is classified, where it is
tried again, how a failing provider is isolated, and what the product still does without each one.
The decision is D-035; how to investigate one call is `diagnosing-a-call.md`.

## One taxonomy

Every error the product defines states its kind (`domain/failures.py`). The kind decides the rest:

| Kind | Retryable | Shown to the user | Needs attention | Raised for |
| --- | --- | --- | --- | --- |
| `timeout` | yes | no | no | a bounded wait running out |
| `unavailable` | yes | no | no | a provider or the database not reachable, or saying not now |
| `refused` | no | no | no | a provider answering no: a call that does not exist, a dial refused |
| `circuit_open` | no | no | no | a request not made, its dependency's circuit being open |
| `invalid` | no | yes | no | a value the rules do not allow, a step not offered here |
| `not_permitted` | no | yes | no | not signed in, or not allowed |
| `rate_limited` | no | yes | no | asked too often; told when to ask again |
| `not_found` | no | yes | no | nothing of that id for whoever asked |
| `conflict` | no | yes | no | already recorded, or the call already over |
| `sealed` | no | no | yes | ciphertext that would not open: a key missing, a record altered |
| `defect` | no | no | yes | anything else: a mistake in the product |

"Needs attention" is what an `error`-level log line means: somebody running the deployment has to
act. Every other handled failure is a `warning`, counted by kind. A library's exception is a
`defect` unless the adapter that called the library translated it; the database's edge raises
`StorageUnavailableError` for a lost connection.

`tests/unit/domain/test_failures.py` fails when an error is added without a kind.

## Retries

Only where the failure is retryable **and** the operation is safe to repeat, through
`retry_idempotent` (`application/resilience/retry.py`): three attempts, the wait between them
drawn evenly from nothing up to 100 ms doubled per attempt, at most 1 s.

| Operation | Retried | Why |
| --- | --- | --- |
| Ending a call at the transport | yes | Ending an ended call changes nothing; a call left up is a caller left on a line. |
| Storing the call as it ended | yes | It stores the whole call again; without it there is no summary. |
| Dialling the user | **never** | A second dial rings their phone twice. |
| Answering a call | no | A slow answer is already a caller waiting; the call fails instead. |
| A judgement | no | The next thing the caller says is judged afresh. |
| A push delivery | no | Bounded by a five-second deadline for every device at once (D-016). |

## Circuits

One per dependency, by role: `telephony`, `speech`, `model`, `push_ios`, `push_android`
(`application/resilience/circuit.py`). Five failures in a row open a circuit; for 30 s every request
to that dependency is refused without being made; then one request is let through as a trial, and
its success closes the circuit while its failure opens it again.

A timeout or an unreachable provider counts as a failure. A provider answering no — a device token
it does not know, a call already gone — counts as the provider working. A defect counts as neither.
A push service answering that it could not deliver counts as a failure.

## Degraded modes

What the product does while each dependency is failing, and where each is tested:

| Dependency | What still happens | Tested by |
| --- | --- | --- |
| **Speech** | A call the assistant would take is put through to the user's phone, as routing does for any call the plan has no assistant for, and marked `degraded: speech`. A call already with the assistant whose speech session fails ends as `failed`. | `TestSpeechFailing`, `test_a_call_put_through_because_speech_was_failing_says_so` |
| **Model** | The assistant keeps talking; judgements that fail change nothing, and while the circuit is open none is asked. Summaries are written from the call's facts, and marked `degraded: summary`. | `TestTheModelFailing`, `test_the_model_unavailable_at_a_judgement_leaves_the_caller_with_the_assistant` |
| **Telephony** | Requests fail within the provider bound, and at once while the circuit is open. A call that cannot be answered fails. An escalation whose dial fails returns the call to the assistant, which is told; a call put straight through whose dial fails ends as `failed`. Ending a call is still tried, three times. | `TestTelephonyFailing` |
| **Push, per platform** | The ring still happens (D-016). That platform's devices are recorded `unavailable` without being sent to; the other platform is unaffected; the app fetches the context itself. | `TestAPushServiceFailing` |
| **Database** | A live call carries on; each failed write is counted and marked, and the next write stores the whole call. A call nobody can be found for is let go. Readiness answers `503`. | `test_a_final_save_refused_once_is_tried_again_and_the_call_summarised`, `test_readiness_is_degraded_without_a_database` |

The tests named are under `apps/backend/tests/`: the orchestration ones in
`unit/application/orchestration/` (`test_observed_calls.py`, and `test_teardown_holes.py` for the
final save), push in `unit/application/escalation/test_dispatch.py`, readiness in
`integration/test_health.py`, and the model's whole-system scenario in
`e2e/test_provider_faults.py`.

## Health

- `GET /health` is liveness and touches nothing.
- `GET /health/ready` is `503` only when the database does not answer. It also reports each
  circuit under `dependencies` and whether rate limits are `shared` or `per_process`. An open
  circuit does not fail readiness: every process shares the providers, so taking this one out of
  rotation would only move its calls to another that fails them the same way.

Neither carries configuration: no host, no vendor, no URL.

## Observability output

- **Logs:** structured, one pipeline for the product's lines and every library's, scrubbed last
  (`observability/scrubbing.py`). A call's lines carry `call_id` and the `correlation_id` it
  arrived on.
- **Metrics:** recorded to the log and kept in process for `/diagnostics/metrics` as percentiles.
  Each is declared with every value its labels take (`observability/catalogue.py`).
- **Traces:** off unless `TRACING_OTLP_ENDPOINT` names a collector's OTLP/HTTP traces URL. The
  default tracer checks every span and sends it nowhere, and nothing else changes.
- **Diagnostics:** `/diagnostics/...`, behind `DIAGNOSTICS_TOKEN`, absent without it.
