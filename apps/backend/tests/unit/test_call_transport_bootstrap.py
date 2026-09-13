"""Choosing a call transport happens once, in bootstrap, and nothing downstream learns which."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

from letmehandle.bootstrap import build_call_transport, build_reported_calls
from letmehandle.config.settings import ConfigurationError, Settings, TelephonyProviderName
from letmehandle.domain.models.identifiers import CallId, EventId, UserId
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.ports.call_transport import (
    CallEvent,
    CallEventKind,
    answering,
    audio_streaming,
    bridging,
    screening,
    three_way,
)
from letmehandle.main import create_app, main
from tests.support.config import REQUIRED_ENVIRONMENT, make_settings
from tests.unit.test_entrypoint import recorded_uvicorn  # noqa: F401 - a fixture

if TYPE_CHECKING:
    from fastapi import FastAPI

TELEPHONY_PATHS = (
    "/telephony/voice/incoming",
    "/telephony/voice/assistant",
    "/telephony/conference/status",
    "/telephony/leg/status",
)


def telephony_settings() -> Settings:
    return make_settings(
        telephony_provider=TelephonyProviderName.TWILIO,
        telephony_account_id="account-for-tests",
        telephony_auth_token="token-for-tests",
        telephony_numbers=(PhoneNumber.parse("+12025550100"),),
        telephony_app_id="app-for-tests",
        telephony_webhook_base_url="https://calls.example.com",
    )


async def statuses(app: FastAPI) -> set[int]:
    """What an unsigned request to each of the provider's paths is answered with."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return {(await client.post(path)).status_code for path in TELEPHONY_PATHS}


async def test_a_deployment_with_no_telephony_account_has_no_transport_and_no_routes() -> None:
    settings = make_settings()
    assert build_call_transport(settings, reported_calls=build_reported_calls()) is None
    assert await statuses(create_app(settings)) == {404}


async def test_the_configured_transport_is_built_and_narrows_to_what_it_declares() -> None:
    binding = build_call_transport(telephony_settings(), reported_calls=build_reported_calls())
    assert binding is not None
    transport = binding.transport
    # Narrowing raises when a declaration and an implementation disagree; here none do.
    answering(transport)
    audio_streaming(transport)
    bridging(transport)
    three_way(transport)
    await binding.close()


def test_a_configured_transport_missing_its_account_names_what_is_missing() -> None:
    settings = make_settings(telephony_provider=TelephonyProviderName.TWILIO)
    with pytest.raises(ConfigurationError, match="TELEPHONY_AUTH_TOKEN"):
        build_call_transport(settings, reported_calls=build_reported_calls())


async def test_the_application_mounts_the_providers_routes_and_closes_the_transport() -> None:
    settings = telephony_settings()
    app = create_app(settings)
    # Present, and refusing what is not signed.
    assert await statuses(app) == {403}
    # Not in the documented schema: the provider is not one of the API's clients.
    assert not any(path.startswith("/telephony") for path in app.openapi()["paths"])
    async with app.router.lifespan_context(app):
        transport = app.state.telephony.transport
    events = [event async for event in transport.events()]
    assert events == []


async def test_the_handset_transport_is_the_one_its_reports_feed() -> None:
    # Chosen from configuration like the streaming one, but never built a second time: a report
    # the reporting route accepts must reach the transport the product reads.
    app = create_app(make_settings(telephony_provider=TelephonyProviderName.ANDROID_NATIVE))
    transport = app.state.telephony.transport
    screening(transport)
    assert await statuses(app) == {404}
    reported = CallEvent(CallEventKind.INCOMING, CallId("handset-call"), EventId("handset-event"))
    async with app.router.lifespan_context(app):
        assert app.state.container.reported_calls is transport
        await app.state.container.reported_calls.publish(UserId("user"), reported)
        assert await anext(transport.events()) == reported


def test_main_starts_a_handset_deployment_without_a_telephony_account(
    monkeypatch: pytest.MonkeyPatch,
    recorded_uvicorn: dict[str, object],  # noqa: F811 - the fixture
) -> None:
    for name, value in REQUIRED_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("TELEPHONY_PROVIDER", "android_native")
    main()
    assert recorded_uvicorn


def test_main_refuses_to_start_with_telephony_half_configured(
    monkeypatch: pytest.MonkeyPatch,
    recorded_uvicorn: dict[str, object],  # noqa: F811 - the fixture
) -> None:
    for name, value in REQUIRED_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("TELEPHONY_PROVIDER", "twilio")
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert "TELEPHONY_ACCOUNT_ID" in str(exit_info.value)
    assert recorded_uvicorn == {}
