"""Choosing a call transport happens once, in bootstrap, and nothing downstream learns which."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from httpx import ASGITransport, AsyncClient

from letmehandle.bootstrap import (
    build_call_transport,
    build_container,
    build_reported_calls,
    build_voice_provider,
)
from letmehandle.config.settings import ConfigurationError, Settings, TelephonyProviderName
from letmehandle.domain.models.forwarding import CallForwarding
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
from tests.support.config import (
    REQUIRED_ENVIRONMENT,
    TEST_TRANSCRIPT_KEYS,
    UNREACHABLE_DATABASE,
    make_settings,
)
from tests.support.observability import recorded_observability
from tests.unit.test_entrypoint import recorded_uvicorn  # noqa: F401 - a fixture

if TYPE_CHECKING:
    from fastapi import FastAPI

TELEPHONY_PATHS = (
    "/telephony/voice/incoming",
    "/telephony/voice/assistant",
    "/telephony/conference/status",
    "/telephony/leg/status",
)

# A streaming account, complete, so that what is refused is only where calls would be recorded.
STREAMING_ENVIRONMENT = {
    "TELEPHONY_ACCOUNT_ID": "account-for-tests",
    "TELEPHONY_AUTH_TOKEN": "token-for-tests",
    "TELEPHONY_NUMBERS": "+12025550100",
    "TELEPHONY_APP_ID": "app-for-tests",
    "TELEPHONY_WEBHOOK_BASE_URL": "https://calls.example.com",
}


def telephony_settings() -> Settings:
    return make_settings(
        telephony_provider=TelephonyProviderName.TWILIO,
        telephony_account_id="account-for-tests",
        telephony_auth_token="token-for-tests",
        telephony_numbers=(PhoneNumber.parse("+12025550100"), PhoneNumber.parse("+12025550101")),
        telephony_app_id="app-for-tests",
        telephony_webhook_base_url="https://calls.example.com",
    )


async def statuses(app: FastAPI) -> set[int]:
    """What an unsigned request to each of the provider's paths is answered with."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return {(await client.post(path)).status_code for path in TELEPHONY_PATHS}


async def test_a_deployment_with_no_telephony_account_has_no_transport_and_no_routes() -> None:
    settings = make_settings()
    assert (
        build_call_transport(
            settings, reported_calls=build_reported_calls(), observability=recorded_observability()
        )
        is None
    )
    assert await statuses(create_app(settings)) == {404}


async def test_the_configured_transport_is_built_and_narrows_to_what_it_declares() -> None:
    binding = build_call_transport(
        telephony_settings(),
        reported_calls=build_reported_calls(),
        observability=recorded_observability(),
    )
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
        build_call_transport(
            settings, reported_calls=build_reported_calls(), observability=recorded_observability()
        )


async def test_the_application_mounts_the_providers_routes_and_closes_the_transport() -> None:
    binding = build_call_transport(
        telephony_settings(),
        reported_calls=build_reported_calls(),
        observability=recorded_observability(),
    )
    # Handed over rather than configured, so the lifespan runs without the storage a configured
    # transport refuses to start without.
    app = create_app(make_settings(), telephony=binding)
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
    app = create_app(
        make_settings(
            telephony_provider=TelephonyProviderName.ANDROID_NATIVE,
            database_url=UNREACHABLE_DATABASE,
            transcript_encryption_keys=TEST_TRANSCRIPT_KEYS,
        )
    )
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
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE_DATABASE)
    monkeypatch.setenv("TRANSCRIPT_ENCRYPTION_KEYS", TEST_TRANSCRIPT_KEYS)
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


@pytest.mark.parametrize("provider", ["android_native", "twilio"])
def test_main_refuses_to_carry_calls_with_nowhere_to_record_them(
    monkeypatch: pytest.MonkeyPatch,
    recorded_uvicorn: dict[str, object],  # noqa: F811 - the fixture
    provider: str,
) -> None:
    for name, value in {**REQUIRED_ENVIRONMENT, **STREAMING_ENVIRONMENT}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("TELEPHONY_PROVIDER", provider)
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert "DATABASE_URL, TRANSCRIPT_ENCRYPTION_KEYS" in str(exit_info.value)
    assert recorded_uvicorn == {}


def forwarding_for(settings: Settings) -> CallForwarding | None:
    container = build_container(
        settings, voices=build_voice_provider(settings), reported_calls=build_reported_calls()
    )
    return container.forwarding


def test_a_streaming_deployment_asks_users_to_forward_to_its_first_number() -> None:
    # A streaming call reaches the product only by the user's carrier forwarding it, and the
    # first configured number is the one every user is told, so two screens never disagree.
    assert forwarding_for(telephony_settings()) == CallForwarding(PhoneNumber.parse("+12025550100"))


def test_a_handset_deployment_needs_nothing_forwarded() -> None:
    # The handset screens its own calls; there is nowhere to forward them to.
    settings = make_settings(telephony_provider=TelephonyProviderName.ANDROID_NATIVE)
    assert forwarding_for(settings) is None


def test_a_deployment_carrying_no_calls_needs_nothing_forwarded() -> None:
    assert forwarding_for(make_settings()) is None
