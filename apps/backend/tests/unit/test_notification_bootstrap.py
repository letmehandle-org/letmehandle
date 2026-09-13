"""Push configuration: optional per platform, complete when present, and never echoed back."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError

from letmehandle.adapters.notification.apns.provider import APNsNotificationProvider
from letmehandle.adapters.notification.fcm.provider import FCMNotificationProvider
from letmehandle.application.escalation.dispatch import EscalationDispatcher
from letmehandle.bootstrap import (
    build_container,
    build_escalation_dispatcher,
    build_notification_providers,
    build_reported_calls,
    build_voice_provider,
    close_notification_providers,
)
from letmehandle.config.settings import (
    APNsEnvironmentName,
    ConfigurationError,
    Settings,
    TelephonyProviderName,
    get_settings,
)
from letmehandle.main import create_app
from tests.contracts.fakes import FixedClock, RecordingNotificationProvider
from tests.support.config import TEST_TRANSCRIPT_KEYS, UNREACHABLE_DATABASE, make_settings
from tests.support.push_services import (
    EXAMPLE_KEY_ID,
    EXAMPLE_PROJECT,
    EXAMPLE_TEAM_ID,
    EXAMPLE_TOPIC,
    ec_key_pem,
    rsa_key_pem,
    service_account_json,
)
from tests.support.recording_metrics import RecordingMetrics

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

APNS: dict[str, Any] = {
    "apns_key_id": EXAMPLE_KEY_ID,
    "apns_team_id": EXAMPLE_TEAM_ID,
    "apns_private_key": ec_key_pem(),
    "apns_topic": EXAMPLE_TOPIC,
    "apns_environment": APNsEnvironmentName.SANDBOX,
}
FCM: dict[str, Any] = {
    "fcm_project_id": EXAMPLE_PROJECT,
    "fcm_service_account_json": service_account_json(),
}


class TestSettings:
    def test_push_is_optional_at_startup(self) -> None:
        settings = make_settings()
        assert not settings.apns_configured
        assert not settings.fcm_configured

    def test_they_are_read_from_the_environment_and_blank_means_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tests.support.config import REQUIRED_ENVIRONMENT

        for name, value in REQUIRED_ENVIRONMENT.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv("APNS_KEY_ID", "")
        monkeypatch.setenv("FCM_PROJECT_ID", EXAMPLE_PROJECT)
        monkeypatch.setenv("FCM_SERVICE_ACCOUNT_JSON", "{}")
        monkeypatch.setenv("APNS_ENVIRONMENT", "production")
        settings = get_settings()
        assert settings.apns_key_id is None
        assert settings.apns_environment is APNsEnvironmentName.PRODUCTION
        assert settings.fcm_project_id == EXAMPLE_PROJECT
        assert settings.fcm_service_account_json is not None
        assert "{}" not in repr(settings)

    def test_an_unknown_apns_environment_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="apns_environment"):
            Settings(apns_environment="staging")  # type: ignore[arg-type]

    def test_complete_apns_credentials_are_returned(self) -> None:
        credentials = make_settings(**APNS).require_apns()
        assert credentials.key_id == EXAMPLE_KEY_ID
        assert credentials.private_key == ec_key_pem()
        assert credentials.environment is APNsEnvironmentName.SANDBOX

    def test_incomplete_apns_names_every_missing_variable(self) -> None:
        settings = make_settings(apns_key_id=EXAMPLE_KEY_ID)
        assert settings.apns_configured
        with pytest.raises(ConfigurationError) as caught:
            settings.require_apns()
        message = str(caught.value)
        for name in ("APNS_TEAM_ID", "APNS_PRIVATE_KEY", "APNS_TOPIC", "APNS_ENVIRONMENT"):
            assert name in message
        assert "APNS_KEY_ID," not in message

    def test_complete_fcm_credentials_are_returned(self) -> None:
        credentials = make_settings(**FCM).require_fcm()
        assert credentials.project_id == EXAMPLE_PROJECT
        assert credentials.service_account_json == service_account_json()

    @pytest.mark.parametrize(
        ("given", "missing"),
        [
            ({"fcm_project_id": EXAMPLE_PROJECT}, "FCM_SERVICE_ACCOUNT_JSON must"),
            ({"fcm_service_account_json": "{}"}, "FCM_PROJECT_ID must"),
        ],
    )
    def test_incomplete_fcm_names_what_is_missing(
        self, given: dict[str, Any], missing: str
    ) -> None:
        settings = make_settings(**given)
        assert settings.fcm_configured
        with pytest.raises(ConfigurationError, match=missing):
            settings.require_fcm()


class TestProviders:
    def test_none_are_built_without_credentials(self) -> None:
        assert build_notification_providers(make_settings(), clock=FixedClock()) == ()
        assert (
            build_container(
                make_settings(),
                voices=build_voice_provider(make_settings()),
                reported_calls=build_reported_calls(),
            ).notifications
            == ()
        )

    def test_each_configured_platform_gets_its_provider(self) -> None:
        both = build_notification_providers(make_settings(**APNS, **FCM), clock=FixedClock())
        assert [type(each) for each in both] == [APNsNotificationProvider, FCMNotificationProvider]
        only_fcm = build_notification_providers(make_settings(**FCM), clock=FixedClock())
        assert [each.name for each in only_fcm] == ["fcm"]

    def test_a_half_configured_platform_stops_startup(self) -> None:
        with pytest.raises(ConfigurationError, match="APNS_TOPIC"):
            build_notification_providers(
                make_settings(**{**APNS, "apns_topic": None}),
                clock=FixedClock(),
            )

    def test_an_unreadable_apns_key_stops_startup_without_repeating_it(self) -> None:
        with pytest.raises(ConfigurationError, match="APNS_PRIVATE_KEY") as caught:
            build_notification_providers(
                make_settings(**{**APNS, "apns_private_key": rsa_key_pem()}),
                clock=FixedClock(),
            )
        assert "PRIVATE KEY-----" not in str(caught.value)

    def test_an_unreadable_service_account_stops_startup_without_repeating_it(self) -> None:
        with pytest.raises(ConfigurationError, match="FCM_SERVICE_ACCOUNT_JSON") as caught:
            build_notification_providers(
                make_settings(fcm_project_id=EXAMPLE_PROJECT, fcm_service_account_json="{bad"),
                clock=FixedClock(),
            )
        assert "{bad" not in str(caught.value)

    async def test_closing_closes_what_can_be_closed(self) -> None:
        settings = make_settings(**APNS, **FCM)
        container = build_container(
            settings, voices=build_voice_provider(settings), reported_calls=build_reported_calls()
        )
        recording = RecordingNotificationProvider()
        container = replace(container, notifications=(*container.notifications, recording))
        await close_notification_providers(container)
        assert all(
            each._client.is_closed  # type: ignore[attr-defined]
            for each in container.notifications
            if each is not recording
        )

    def test_the_dispatcher_is_given_every_provider(self) -> None:
        settings = make_settings(transcript_encryption_keys=TEST_TRANSCRIPT_KEYS, **FCM)
        container = build_container(
            settings, voices=build_voice_provider(settings), reported_calls=build_reported_calls()
        )
        factory: async_sessionmaker[AsyncSession] = None  # type: ignore[assignment]
        dispatcher = build_escalation_dispatcher(container, factory, metrics=RecordingMetrics())
        assert isinstance(dispatcher, EscalationDispatcher)
        assert [each.name for each in dispatcher._providers.values()] == ["fcm"]

    def test_a_dispatcher_that_could_not_seal_what_it_stores_is_refused(self) -> None:
        settings = make_settings(**FCM)
        container = build_container(
            settings, voices=build_voice_provider(settings), reported_calls=build_reported_calls()
        )
        factory: async_sessionmaker[AsyncSession] = None  # type: ignore[assignment]
        with pytest.raises(ConfigurationError, match="TRANSCRIPT_ENCRYPTION_KEYS"):
            build_escalation_dispatcher(container, factory, metrics=RecordingMetrics())


class TestLifespan:
    async def test_with_calls_and_a_database_there_is_a_dispatcher_and_it_is_released(
        self,
    ) -> None:
        settings = make_settings(
            database_url=UNREACHABLE_DATABASE,
            transcript_encryption_keys=TEST_TRANSCRIPT_KEYS,
            telephony_provider=TelephonyProviderName.ANDROID_NATIVE,
            **FCM,
        )
        app = create_app(settings)
        async with app.router.lifespan_context(app):
            assert isinstance(app.state.escalations, EscalationDispatcher)
            provider = app.state.container.notifications[0]
        assert app.state.escalations is None
        assert provider._client.is_closed

    async def test_without_a_database_there_is_no_dispatcher(self) -> None:
        app = create_app(make_settings())
        async with app.router.lifespan_context(app):
            assert app.state.escalations is None

    async def test_without_calls_to_escalate_there_is_no_dispatcher(self) -> None:
        # Nothing but orchestration escalates, so a deployment without keys still starts.
        app = create_app(make_settings(database_url=UNREACHABLE_DATABASE, **FCM))
        async with app.router.lifespan_context(app):
            assert app.state.escalations is None
