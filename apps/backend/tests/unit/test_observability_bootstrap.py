"""Observability is chosen once: tracing to nowhere unless a collector is configured."""

from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic import ValidationError

from letmehandle.adapters.speech.session_support.telemetry import ROUND_TRIP
from letmehandle.adapters.tracing.opentelemetry import OpenTelemetryTracer
from letmehandle.bootstrap import build_observability
from letmehandle.main import create_app
from letmehandle.observability.tracing import NoTracer
from tests.support.config import make_settings

TOKEN = "a-diagnostics-token-long-enough-to-guard-with"


def test_without_a_collector_spans_go_nowhere_and_closing_does_nothing() -> None:
    observability = build_observability(make_settings())

    assert isinstance(observability.tracer, NoTracer)
    observability.close()


def test_with_a_collector_spans_are_exported_to_it() -> None:
    observability = build_observability(
        make_settings(tracing_otlp_endpoint="http://collector.invalid:4318/v1/traces")
    )

    assert isinstance(observability.tracer, OpenTelemetryTracer)
    observability.close()


def test_what_is_recorded_reaches_the_diagnostics_view_of_it() -> None:
    observability = build_observability(make_settings())

    observability.metrics.observe(ROUND_TRIP, 0.4, {"provider": "echo"})

    [measure] = observability.in_process.snapshot().measures
    assert (measure.metric, measure.count, measure.p50) == (ROUND_TRIP, 1, 0.4)


async def test_stopping_the_application_flushes_what_it_traced() -> None:
    closed: list[bool] = []
    settings = make_settings()
    observability = replace(build_observability(settings), close=lambda: closed.append(True))
    app = create_app(settings, observability=observability)

    async with app.router.lifespan_context(app):
        assert closed == []

    assert closed == [True]


async def test_what_the_application_records_is_what_diagnostics_reads() -> None:
    settings = make_settings()
    observability = build_observability(settings)
    app = create_app(settings, observability=observability)

    async with app.router.lifespan_context(app):
        assert app.state.container.metrics is observability.metrics


def test_a_diagnostics_token_too_short_to_guard_anything_is_refused() -> None:
    with pytest.raises(ValidationError, match="DIAGNOSTICS_TOKEN"):
        make_settings(diagnostics_token="short")


def test_a_diagnostics_token_long_enough_is_kept_secret() -> None:
    settings = make_settings(diagnostics_token=TOKEN)

    assert settings.diagnostics_token is not None
    assert TOKEN not in repr(settings)


def test_blank_observability_variables_count_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    from letmehandle.config.settings import Settings

    monkeypatch.setenv("TRACING_OTLP_ENDPOINT", "")
    monkeypatch.setenv("DIAGNOSTICS_TOKEN", "")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]  # pydantic-settings' own argument

    assert settings.tracing_otlp_endpoint is None
    assert settings.diagnostics_token is None
