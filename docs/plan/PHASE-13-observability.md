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
