# Phase 13 — Observability and failure handling

**Goal:** an operator can tell what the system is doing, and why a call went wrong, without
reading anyone's conversation.

## In scope

### Logging
- Structured throughout, one configuration point established in Phase 0 and completed here.
- Correlation: a call id and a request id on every line relating to a call, so one call's
  life can be read end to end.
- Levels used meaningfully: an error is something an operator must act on. Everything else
  is not an error.
- The Phase 12 scrubber applies to every line. No transcript content, no caller identity
  beyond what operations require, no credentials.

### Tracing
- Spans across the call path: inbound callback, routing, speech session, agent decision,
  escalation, bridge, teardown. Provider calls are child spans, so provider latency is
  attributable rather than inferred.
- Behind an interface, with a no-op default. A self-hoster who wants no tracing backend runs
  with no tracing backend and nothing degrades.

### Metrics
- Call volume by outcome. Routing distribution. Escalation rate, and how escalations
  resolved: answered, unanswered, declined.
- Latency: speech time to first audio, per-turn round trip, agent decision time, telephony
  bridge time, notification dispatch time. Percentiles, not averages.
- State transition counts and durations, so a state calls get stuck in is visible.
- Failures by provider and by kind. Reconnections. Timeouts. Idempotent duplicates ignored.
- Purge job outcomes.

Metric labels are bounded cardinality and contain no personal data. A phone number is never
a label.

### Failure handling, completed
- One error taxonomy across the backend: what is retryable, what is user-visible, what is
  operator-visible, and what is a defect.
- Retry with backoff and jitter where retrying is correct, and nowhere else. Retrying a
  non-idempotent operation is a defect, and the taxonomy is what prevents it.
- Circuit breaking per provider, so a failing provider degrades the feature that needs it
  instead of the whole system.
- Degraded-mode behaviour stated per provider and tested: what the product still does when
  speech, LLM, telephony or notification is unavailable.
- Health endpoints extended to report provider status without leaking configuration.

### Diagnostics
- A documented procedure for investigating a specific call, using ids alone.
- A redacted call timeline view assembled from state transitions and metrics, sufficient to
  diagnose without reading the conversation.

## Explicitly out of scope

- Hosting a metrics or tracing backend. Interfaces and exporters only.
- Alerting rules, which belong to a deployment.
- Per-user analytics.

## Tests required

| Kind | Must prove |
| --- | --- |
| Unit | The scrubber removes every sensitive field, including nested and in exception context; the error taxonomy classifies every defined error; retry applies only to retryable operations; circuit breaker opens, half-opens and closes. |
| Unit | Metric labels are bounded and contain no personal data. Asserted by a test over the registry, so a new metric cannot introduce one. |
| Integration | A full call emits a coherent trace; the timeline reconstructs the call from ids alone; degraded mode per provider behaves as documented. |
| Negative | No log line, metric label, span attribute or health response contains transcript content or a phone number. Asserted across a full simulated call. |

## Acceptance criteria

1. A call can be traced end to end using its id.
2. No observability output contains conversation content or personal data, proven across a
   full call.
3. Latency is measured at every provider boundary and reported as percentiles.
4. State transitions are counted and timed, and a stuck call is visible.
5. Every provider has a documented, tested degraded mode.
6. Retries apply only where retrying is correct.
7. Circuit breakers isolate a failing provider.
8. Tracing is optional and the no-op default degrades nothing.
9. The diagnostic procedure is documented and was used successfully at least once.
10. Coverage meets the D-020 floors.

## Risks and open questions

- **Useful diagnostics against privacy.** The tension is real: the most useful diagnostic
  data is the conversation. The design answer is that structure — states, timings, decision
  reasons, error kinds — is enough, and Phase 14's failure scenarios are the test of whether
  that claim holds.
- **Cardinality.** An unbounded label is a production outage waiting to happen. Asserted by
  test rather than reviewed by eye.

## Verification report

```
PHASE 13 VERIFICATION

Planned tasks:        complete for the backend; see Known issues for what is held
Acceptance criteria:  10/10 passed, 9 by automation and by a local run rather than a real call
Unit tests:           passed   POSTGRES_PORT=5433 make verify: tests/unit 2627, tests/contracts 175
Integration tests:    passed   make verify: tests/integration 663 (PostgreSQL on 5433), migrations
                               0001→0010 up, down to base and up again, then `alembic check` clean
E2E tests:            passed   make verify runs tests/e2e (26), including test_observability.py
                               (3477 passed, 14 skipped in all; the skips are the live-service runs)
Coverage:             100.00%  backend, floor 98 (make coverage: 10457 statements, 1888 branches)
                      unchanged mobile logic; no mobile code was touched (pnpm test: 237 passed)
Lint:                 passed   make lint (ruff check, ruff format --check, eslint)
Format:               passed   make lint (440 files already formatted)
Typecheck:            passed   make typecheck (mypy --strict over src, tests and the scripts, tsc)
Static analysis:      passed   lint-imports: 5 contracts kept, including the new "Only the tracing
                               adapter knows the tracing SDK"; disclosure audit tree and history
Build:                passed   make api-types-check (the readiness schema changed; regenerated)
Application runs:     yes      uvicorn on a scratch database migrated to 0010: /health/ready reports
                               the database and every circuit; /diagnostics refuses no token (401),
                               lists no live calls, answers 404 for an unknown call; JSON log lines
                               carry correlation ids, the code provider's number masked, and
                               uvicorn's own exception line outlined without its message
Manual verification:  make audit-deps against the live advisory databases: backend clean; the
                      three accepted workspace advisories reported as accepted; with uv off the
                      PATH it exits 2, "could not run, which is not a pass"
Docs updated:         D-035; docs/operations/diagnosing-a-call.md and failure-handling.md;
                      docs/security/data-inventory.md, review.md (suggestions 4 and 5 done);
                      docs/development/self-hosting-security.md; .env.example
Known issues:         1. No real call has been investigated; the procedure is proven against the
                         simulated provider end to end and against a local process (phase 14's
                         manual run is where a real one happens).
                      2. Metrics leave the process as log lines and as /diagnostics/metrics; no
                         metrics exporter to a backend is built (the plan keeps backends out).
                      3. A summariser that fails swallows its own failure and falls back, so the
                         model's circuit counts judgements only; an open circuit still skips it.
                      4. /diagnostics/calls lists the calls of the process that answers; with
                         several processes each is asked, while timelines are stored and shared.
Commits:              ff6c0d0 feat(observability): scrub sensitive fields and values from every log line
                      11279db feat(domain): classify every failure into one taxonomy
                      807c527 feat(observability): declare every metric with the values its labels take
                      2888736 feat(orchestration): trace, time and guard each call's dependencies
                      b5af068 feat(orchestration): store each call's timeline of states and failures
                      9c92548 feat(api): diagnose calls by id behind a token, and report circuits
                      9673d0b test(observability): audit every log call for personal fields by name
                      a35ffbe ci: audit locked dependencies for known vulnerabilities
                      231d209 docs: record the observability decision and how to diagnose a call
                      725caa8 refactor(orchestration): log handled failures at the level of their kind
```

## Acceptance criteria, each with its evidence

| # | Criterion | Evidence |
| --- | --- | --- |
| 1 | A call can be traced end to end using its id | One `call` span per call with `call.id`, every step and provider call beneath it, and each provider callback a `telephony.callback` span with the same id: `TestTheTrace` in `tests/unit/application/orchestration/test_observed_calls.py`, and the whole-system trace in `tests/e2e/test_observability.py`. A call's log lines carry `call_id` and the request's `correlation_id` (`test_logging.py`). Its stored timeline is read by id at `/diagnostics/calls/{id}`. |
| 2 | No observability output contains conversation content or personal data, proven across a full call | `tests/e2e/test_observability.py`: an escalated call's every log line, span attribute, metric, readiness and diagnostics response contain neither number, what was said, what the user was told, a device token nor the access token. The scrubber (`test_scrubbing.py`, nested and in exception context, standard library lines too), the metric registry (`test_catalogue.py`), span attribute checks (`test_tracing.py`) and the log call audit (`test_log_audit.py`) hold it for code not yet written. |
| 3 | Latency measured at every provider boundary and reported as percentiles | `call.provider_seconds{stage}` (answer, dial, cancel, terminate), `call.speech_open_seconds`, the speech adapters' time to first audio and round trip, `call.judgement_seconds`, `call.summary_seconds`, `escalation.delivery_seconds`: `test_every_provider_boundary_and_every_state_a_call_passed_through_is_timed`. p50, p90, p99 and maximum in `/diagnostics/metrics`: `test_in_process.py`, `test_diagnostics_api.py`, and the e2e scenario. |
| 4 | State transitions counted and timed, and a stuck call visible | `call.transition` counts and `call.state_seconds` by the state left; `/diagnostics/calls` lists each live call's state and seconds in it, oldest first (`TestStandings`); the stored timeline marks every transition (`TestTheTimeline`). |
| 5 | Every provider has a documented, tested degraded mode | `docs/operations/failure-handling.md` and D-035, each row naming its tests: speech puts calls through (`TestSpeechFailing`), the model skips judgements and summarises from facts (`TestTheModelFailing`), telephony refuses at once (`TestTelephonyFailing`), push isolates the platform (`TestAPushServiceFailing`), storage carries on (`test_teardown_holes.py`, `test_health.py`). |
| 6 | Retries apply only where retrying is correct | `retry_idempotent` retries only a retryable kind (`test_retry.py`); used for ending a call and the final save; dialling is asked once while terminate is asked three times (`test_ending_a_call_at_the_transport_is_tried_again_and_dialling_never_is`). |
| 7 | Circuit breakers isolate a failing provider | Opens, half-opens with one trial, closes, reopens (`test_circuit.py`); a failing transport's next call is not asked (`test_a_failing_transport_opens_its_circuit...`); one push platform failing leaves the other delivering (`test_its_circuit_opens_and_its_devices_are_not_sent_to_while_it_is_open`). |
| 8 | Tracing is optional and the no-op default degrades nothing | `NoTracer` is the default without `TRACING_OTLP_ENDPOINT` (`test_observability_bootstrap.py`) and every suite but the tracing tests runs with a checking tracer that exports nothing; the OpenTelemetry adapter is behind the port and import-linter forbids the SDK outside it. |
| 9 | The diagnostic procedure is documented and was used successfully at least once | `docs/operations/diagnosing-a-call.md`, run in full by `tests/e2e/test_observability.py` (live calls, timeline by id, measurements, readiness) and by hand against a local process (see Application runs). |
| 10 | Coverage meets the D-020 floors | 100.00% backend statements and branches under `make verify`. |

## What was built, and decisions made on the way

- **The scrubber did not exist.** The plan says "the Phase 12 scrubber"; phase 12 reviewed log calls
  by hand and recommended an audit. Both are built here: the scrubber on every line, and the audit.
- **Degraded speech puts calls through.** Rather than failing a call the assistant cannot speak on,
  an open speech circuit removes the assistant from the call's plan and routing's existing fallback
  rings the user. This changes product behaviour during an outage and is recorded in D-035.
- **Timelines are stored**, in a new table with its call, rather than kept in process memory, so a
  call that went wrong is diagnosable after a restart and from any process.
- **Readiness is not failed by an open circuit**, which would only move calls to processes failing
  them the same way.
- **`CallEvent` gained `correlation_id`**, excluded from equality, so a call's run can log the id of
  the request that announced it.
- **The dependency audit is its own target and CI job** (`make audit-deps`), not part of `make
  verify`: it needs the advisory databases online, and verify runs offline before every push.
