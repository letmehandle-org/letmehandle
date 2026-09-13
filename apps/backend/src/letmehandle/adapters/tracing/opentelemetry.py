"""Spans exported through OpenTelemetry, to whatever collector a deployment points it at.

The SDK's own exception handling is switched off on every span. It records an exception's message
and stack as span events, and a message is exactly what must not leave: a failure is recorded here
as its kind, in an attribute, with an error status that carries no description.

The tracer provider is this adapter's own rather than the process-wide one, so nothing else in the
process — a library instrumenting itself — exports through it by accident.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode

from letmehandle.domain.failures import classify
from letmehandle.domain.ports.tracing import Span, Tracer
from letmehandle.observability.tracing import checked_attribute, checked_attributes, checked_name

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from opentelemetry import trace
    from opentelemetry.sdk.trace.export import SpanExporter

    from letmehandle.domain.ports.tracing import AttributeValue

SERVICE_NAME: Final = "letmehandle-backend"
_INSTRUMENTATION: Final = "letmehandle"


class OpenTelemetryTracer(Tracer):
    """Spans, through an OpenTelemetry tracer."""

    def __init__(self, tracer: trace.Tracer) -> None:
        self._tracer = tracer

    @contextmanager
    def span(self, name: str, **attributes: AttributeValue) -> Iterator[Span]:
        with self._tracer.start_as_current_span(
            checked_name(name),
            attributes=checked_attributes(attributes),
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            wrapped = _OpenTelemetrySpan(span)
            try:
                yield wrapped
            except asyncio.CancelledError:
                # Stopped, not failed: a call torn down while this ran.
                span.set_attribute("outcome", "cancelled")
                raise
            except Exception as error:
                wrapped.record_failure(error)
                raise


class _OpenTelemetrySpan(Span):
    def __init__(self, span: trace.Span) -> None:
        self._span = span

    def set_attribute(self, key: str, value: AttributeValue) -> None:
        self._span.set_attribute(key, checked_attribute(key, value))

    def record_failure(self, error: BaseException) -> None:
        self._span.set_attribute("failure.kind", classify(error).kind.value)
        self._span.set_status(Status(StatusCode.ERROR))


@dataclass(frozen=True, slots=True)
class ExportingTracer:
    """A tracer, and how to flush what it has not yet sent and stop, once, at shutdown."""

    tracer: Tracer
    shutdown: Callable[[], None]


def exporting_tracer(exporter: SpanExporter, *, version: str) -> ExportingTracer:
    """A tracer whose spans are batched and handed to `exporter` from a background thread."""
    provider = TracerProvider(
        resource=Resource.create({"service.name": SERVICE_NAME, "service.version": version})
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    return ExportingTracer(
        tracer=OpenTelemetryTracer(provider.get_tracer(_INSTRUMENTATION, version)),
        shutdown=provider.shutdown,
    )


def otlp_tracer(endpoint: str, *, version: str) -> ExportingTracer:
    """A tracer exporting over OTLP's HTTP protocol to `endpoint`, a collector's traces URL."""
    return exporting_tracer(OTLPSpanExporter(endpoint=endpoint), version=version)
