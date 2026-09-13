"""Spans carry structure and never content, and are exported without a message or a stack."""

from __future__ import annotations

import asyncio

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from letmehandle.adapters.tracing.opentelemetry import (
    SERVICE_NAME,
    OpenTelemetryTracer,
    exporting_tracer,
    otlp_tracer,
)
from letmehandle.domain.errors import ProviderError
from letmehandle.observability.tracing import (
    UNTRACEABLE_CALL,
    NoTracer,
    SpanAttributeError,
    traceable_call_id,
)

NUMBER = "+12025550123"


@pytest.fixture
def exported() -> tuple[OpenTelemetryTracer, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return OpenTelemetryTracer(provider.get_tracer("tests")), exporter


@pytest.mark.parametrize(
    ("identifier", "carried"),
    [
        ("CAsim-e2e-delivery", "CAsim-e2e-delivery"),
        ("3f2a9c1e-b7d4-4e0a-9c55-7a1b2c3d4e5f", "3f2a9c1e-b7d4-4e0a-9c55-7a1b2c3d4e5f"),
        ("user-7:3f2a9c", "user-7:3f2a9c"),
        ("12025550123", UNTRACEABLE_CALL),
        ("202-555-0123", UNTRACEABLE_CALL),
        (NUMBER, UNTRACEABLE_CALL),
        ("a call from somebody", UNTRACEABLE_CALL),
        ("x" * 65, UNTRACEABLE_CALL),
    ],
)
def test_a_call_id_is_carried_only_when_it_cannot_be_a_number_or_words(
    identifier: str, carried: str
) -> None:
    assert traceable_call_id(identifier) == carried


@pytest.mark.parametrize(
    ("name", "attributes"),
    [
        ("Call Arrived", {}),
        ("call", {"caller": "somebody"}),
        ("call", {"outcome": "the caller asked for a refund"}),
        ("call", {"stage": NUMBER}),
    ],
    ids=["a name that is a sentence", "an unknown attribute", "a sentence", "a number"],
)
def test_the_quiet_tracer_refuses_what_the_exporting_one_would(
    exported: tuple[OpenTelemetryTracer, InMemorySpanExporter],
    name: str,
    attributes: dict[str, str],
) -> None:
    tracer, exporter = exported
    for each in (NoTracer(), tracer):
        with pytest.raises(SpanAttributeError), each.span(name, **attributes):
            pass

    assert exporter.get_finished_spans() == ()


def test_the_quiet_tracer_checks_attributes_set_later_and_records_nothing() -> None:
    with NoTracer().span("call", **{"call.id": "CAsim-1"}) as span:
        span.set_attribute("outcome", "completed")
        span.set_attribute("dependency", 3)
        span.record_failure(RuntimeError(NUMBER))
        with pytest.raises(SpanAttributeError):
            span.set_attribute("transcript", "hello")


def test_spans_nest_and_carry_only_what_was_checked(
    exported: tuple[OpenTelemetryTracer, InMemorySpanExporter],
) -> None:
    tracer, exporter = exported

    with (
        tracer.span("call", **{"call.id": "12025550123"}),
        tracer.span("telephony.dial", dependency="telephony") as dial,
    ):
        dial.set_attribute("outcome", "answered")

    dial_span, call_span = exporter.get_finished_spans()
    assert dial_span.parent is not None
    assert dial_span.parent.span_id == call_span.context.span_id
    assert dict(call_span.attributes or {}) == {"call.id": UNTRACEABLE_CALL}
    assert dict(dial_span.attributes or {}) == {"dependency": "telephony", "outcome": "answered"}


def test_a_failure_leaving_a_span_is_its_kind_and_an_error_status_never_its_message(
    exported: tuple[OpenTelemetryTracer, InMemorySpanExporter],
) -> None:
    tracer, exporter = exported

    with (
        pytest.raises(ProviderError),
        tracer.span("speech.open"),
    ):
        raise ProviderError("speech", f"no line for {NUMBER}", retryable=True)

    [span] = exporter.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description is None
    assert span.events == ()
    assert dict(span.attributes or {}) == {"failure.kind": "unavailable"}


async def test_a_span_left_by_cancellation_is_marked_cancelled_not_failed(
    exported: tuple[OpenTelemetryTracer, InMemorySpanExporter],
) -> None:
    tracer, exporter = exported

    async def waits() -> None:
        with tracer.span("agent.judgement"):
            await asyncio.Event().wait()

    task = asyncio.create_task(waits())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    [span] = exporter.get_finished_spans()
    assert span.status.status_code is StatusCode.UNSET
    assert dict(span.attributes or {}) == {"outcome": "cancelled"}


def test_a_failure_recorded_while_the_span_runs_is_marked_the_same_way(
    exported: tuple[OpenTelemetryTracer, InMemorySpanExporter],
) -> None:
    tracer, exporter = exported

    with tracer.span("notification.delivery") as span:
        span.record_failure(TimeoutError())

    [finished] = exporter.get_finished_spans()
    assert finished.status.status_code is StatusCode.ERROR
    assert dict(finished.attributes or {}) == {"failure.kind": "timeout"}


def test_an_exporting_tracer_names_the_service_and_flushes_on_shutdown() -> None:
    exporter = InMemorySpanExporter()
    exporting = exporting_tracer(exporter, version="1.2.3")

    with exporting.tracer.span("call"):
        pass
    exporting.shutdown()

    [span] = exporter.get_finished_spans()
    assert span.resource.attributes["service.name"] == SERVICE_NAME
    assert span.resource.attributes["service.version"] == "1.2.3"


def test_an_otlp_tracer_is_built_for_a_collector_without_sending_anything_yet() -> None:
    exporting = otlp_tracer("http://collector.invalid:4318/v1/traces", version="1.2.3")

    assert isinstance(exporting.tracer, OpenTelemetryTracer)
    exporting.shutdown()
