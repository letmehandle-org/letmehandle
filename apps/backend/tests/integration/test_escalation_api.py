"""Devices, dispatch and escalation context, through the running application and a real database.

The push services are the simulated ones; everything between the HTTP request and them is real:
the routes, the repositories, the dispatcher, both adapters and the trimming.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from letmehandle.adapters.database.repositories import SqlDeviceRepository
from letmehandle.adapters.notification.apns.provider import (
    APNsEnvironment,
    APNsNotificationProvider,
)
from letmehandle.adapters.notification.apns.token import APNsProviderToken
from letmehandle.adapters.notification.fcm.credentials import AccessTokenSource, ServiceAccount
from letmehandle.adapters.notification.fcm.provider import FCMNotificationProvider
from letmehandle.application.escalation.dispatch import (
    AttemptResult,
    DispatchResult,
    EscalationDispatcher,
)
from letmehandle.bootstrap import build_escalation_dispatcher
from letmehandle.domain.models.escalation import EscalationReason
from letmehandle.domain.models.escalation_context import EscalationContext
from letmehandle.domain.models.identifiers import CallId, UserId
from letmehandle.domain.ports.notification import DevicePlatform
from tests.contracts.fakes import FixedClock
from tests.integration.conftest import ANOTHER_NUMBER, bearer, sign_in
from tests.support.push_services import (
    EXAMPLE_KEY_ID,
    EXAMPLE_PROJECT,
    EXAMPLE_TEAM_ID,
    EXAMPLE_TOPIC,
    HEX_DEVICE_TOKEN,
    SimulatedAPNs,
    SimulatedFCM,
    ec_key_pem,
    fcm_error_body,
    fcm_error_code,
    service_account_json,
)
from tests.support.recording_metrics import RecordingMetrics

if TYPE_CHECKING:
    from tests.integration.conftest import Api

pytestmark = pytest.mark.integration

ANDROID_TOKEN = "example-android-token:APA91b-example"
RAISED = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


async def user_id_of(api: Api, tokens: dict[str, Any]) -> UserId:
    response = await api.client.get("/v1/me", headers=bearer(tokens))
    return UserId(response.json()["id"])


async def register(api: Api, tokens: dict[str, Any], body: dict[str, Any]) -> httpx.Response:
    return await api.client.put("/v1/devices", headers=bearer(tokens), json=body)


def a_context(call: str = "call-1", **overrides: object) -> EscalationContext:
    values: dict[str, object] = {
        "call_id": CallId(call),
        "reason": EscalationReason.DECISION_NEEDS_THE_USER,
        "raised_at": RAISED,
        "caller_label": "a courier",
        "established": "They are at the gate with a parcel.",
        "needed": "Where to leave it.",
    }
    values.update(overrides)
    return EscalationContext(**values)  # type: ignore[arg-type]


def dispatcher_for(
    api: Api, apns: SimulatedAPNs, fcm: SimulatedFCM, metrics: RecordingMetrics
) -> EscalationDispatcher:
    clock = FixedClock()
    fcm_client = httpx.AsyncClient(transport=fcm.transport())
    providers = (
        APNsNotificationProvider(
            token=APNsProviderToken(
                key_id=EXAMPLE_KEY_ID,
                team_id=EXAMPLE_TEAM_ID,
                private_key=ec_key_pem(),
                clock=clock,
            ),
            topic=EXAMPLE_TOPIC,
            environment=APNsEnvironment.SANDBOX,
            clock=clock,
            client=httpx.AsyncClient(base_url="https://apns.test", transport=apns.transport()),
        ),
        FCMNotificationProvider(
            project_id=EXAMPLE_PROJECT,
            tokens=AccessTokenSource(
                ServiceAccount.parse(service_account_json()), client=fcm_client, clock=clock
            ),
            client=fcm_client,
        ),
    )
    container = api.app.state.container
    return build_escalation_dispatcher(
        replace(container, notifications=providers),
        api.app.state.session_factory,
        metrics=metrics,
    )


class TestDeviceRoutes:
    async def test_registering_needs_a_token(self, api: Api) -> None:
        response = await api.client.put(
            "/v1/devices", json={"platform": "ios", "token": HEX_DEVICE_TOKEN}
        )
        assert response.status_code == 401

    async def test_unregistering_needs_a_token(self, api: Api) -> None:
        response = await api.client.post(
            "/v1/devices/unregister", json={"platform": "ios", "token": HEX_DEVICE_TOKEN}
        )
        assert response.status_code == 401

    @pytest.mark.parametrize(
        "body",
        [
            {"platform": "windows", "token": "abc"},
            {"platform": "ios"},
            {"platform": "ios", "token": ""},
            {"platform": "ios", "token": "has space"},
            {"platform": "ios", "token": "../../path"},
            {"platform": "ios", "token": "x" * 513},
            {"platform": "ios", "token": "abc", "previous_token": "bad token"},
            {"platform": "ios", "token": "abc", "unexpected": True},
        ],
    )
    async def test_a_malformed_registration_is_a_422(self, api: Api, body: dict[str, Any]) -> None:
        tokens = await sign_in(api)
        response = await register(api, tokens, body)
        assert response.status_code == 422, response.text
        assert response.json()["error"] == "invalid_request"

    async def test_a_malformed_unregistration_is_a_422(self, api: Api) -> None:
        tokens = await sign_in(api)
        response = await api.client.post(
            "/v1/devices/unregister", headers=bearer(tokens), json={"platform": "ios"}
        )
        assert response.status_code == 422

    async def test_registration_is_idempotent_and_removal_is_silent(self, api: Api) -> None:
        tokens = await sign_in(api)
        body = {"platform": "ios", "token": HEX_DEVICE_TOKEN}
        assert (await register(api, tokens, body)).status_code == 204
        assert (await register(api, tokens, body)).status_code == 204

        user = await user_id_of(api, tokens)
        async with api.app.state.session_factory() as session:
            devices = await SqlDeviceRepository(session, FixedClock()).tokens_for(user)
        assert [each.value for each in devices] == [HEX_DEVICE_TOKEN]

        for _ in range(2):
            removed = await api.client.post(
                "/v1/devices/unregister", headers=bearer(tokens), json=body
            )
            assert removed.status_code == 204
        async with api.app.state.session_factory() as session:
            assert await SqlDeviceRepository(session, FixedClock()).tokens_for(user) == []

    async def test_rotation_replaces_the_previous_token(self, api: Api) -> None:
        tokens = await sign_in(api)
        await register(api, tokens, {"platform": "android", "token": "old-token"})
        response = await register(
            api,
            tokens,
            {"platform": "android", "token": "new-token", "previous_token": "old-token"},
        )
        assert response.status_code == 204
        user = await user_id_of(api, tokens)
        async with api.app.state.session_factory() as session:
            devices = await SqlDeviceRepository(session, FixedClock()).tokens_for(user)
        assert [each.value for each in devices] == ["new-token"]

    async def test_one_user_cannot_unregister_another_s_device(self, api: Api) -> None:
        alice = await sign_in(api)
        bob = await sign_in(api, ANOTHER_NUMBER)
        body = {"platform": "ios", "token": HEX_DEVICE_TOKEN}
        await register(api, alice, body)
        await api.client.post("/v1/devices/unregister", headers=bearer(bob), json=body)

        alice_id = await user_id_of(api, alice)
        async with api.app.state.session_factory() as session:
            devices = await SqlDeviceRepository(session, FixedClock()).tokens_for(alice_id)
        assert len(devices) == 1

    async def test_signing_out_with_the_device_removes_it(self, api: Api) -> None:
        tokens = await sign_in(api)
        device = {"platform": "ios", "token": HEX_DEVICE_TOKEN}
        await register(api, tokens, device)
        user = await user_id_of(api, tokens)

        response = await api.client.post(
            "/v1/auth/signout", json={"refresh_token": tokens["refresh_token"], "device": device}
        )
        assert response.status_code == 204
        async with api.app.state.session_factory() as session:
            assert await SqlDeviceRepository(session, FixedClock()).tokens_for(user) == []

    async def test_an_unknown_session_signing_out_removes_nothing(self, api: Api) -> None:
        tokens = await sign_in(api)
        device = {"platform": "ios", "token": HEX_DEVICE_TOKEN}
        await register(api, tokens, device)
        response = await api.client.post(
            "/v1/auth/signout", json={"refresh_token": "invented", "device": device}
        )
        assert response.status_code == 204
        user = await user_id_of(api, tokens)
        async with api.app.state.session_factory() as session:
            assert len(await SqlDeviceRepository(session, FixedClock()).tokens_for(user)) == 1

    async def test_signing_out_without_a_device_still_works(self, api: Api) -> None:
        tokens = await sign_in(api)
        response = await api.client.post(
            "/v1/auth/signout", json={"refresh_token": tokens["refresh_token"]}
        )
        assert response.status_code == 204


class TestDispatchThroughBothPlatforms:
    async def test_each_device_is_reached_through_its_own_platform(self, api: Api) -> None:
        tokens = await sign_in(api)
        await register(api, tokens, {"platform": "ios", "token": HEX_DEVICE_TOKEN})
        await register(api, tokens, {"platform": "android", "token": ANDROID_TOKEN})
        apns, fcm = SimulatedAPNs(), SimulatedFCM()

        report = await dispatcher_for(api, apns, fcm, RecordingMetrics()).dispatch(
            await user_id_of(api, tokens), a_context()
        )

        assert report.result is DispatchResult.SENT
        assert report.delivered == 2
        assert [each.url.path for each in apns.requests] == [f"/3/device/{HEX_DEVICE_TOKEN}"]
        assert [json.loads(each.content)["message"]["token"] for each in fcm.requests] == [
            ANDROID_TOKEN
        ]

    async def test_a_failing_platform_does_not_affect_the_other(self, api: Api) -> None:
        tokens = await sign_in(api)
        await register(api, tokens, {"platform": "ios", "token": HEX_DEVICE_TOKEN})
        await register(api, tokens, {"platform": "android", "token": ANDROID_TOKEN})
        apns = SimulatedAPNs(responses={HEX_DEVICE_TOKEN: (503, "ServiceUnavailable")})
        fcm = SimulatedFCM()

        report = await dispatcher_for(api, apns, fcm, RecordingMetrics()).dispatch(
            await user_id_of(api, tokens), a_context()
        )

        results = {each.token.platform: each.result for each in report.attempts}
        assert results == {
            DevicePlatform.IOS: AttemptResult.FAILED,
            DevicePlatform.ANDROID: AttemptResult.DELIVERED,
        }
        context = await api.client.get("/v1/escalations/call-1", headers=bearer(tokens))
        assert context.json()["delivery"] == "delivered"

    async def test_a_token_a_platform_calls_dead_is_removed(self, api: Api) -> None:
        tokens = await sign_in(api)
        await register(api, tokens, {"platform": "ios", "token": HEX_DEVICE_TOKEN})
        await register(api, tokens, {"platform": "android", "token": ANDROID_TOKEN})
        apns = SimulatedAPNs(responses={HEX_DEVICE_TOKEN: (410, "Unregistered")})
        dead = fcm_error_body(404, "NOT_FOUND", details=[fcm_error_code("UNREGISTERED")])
        fcm = SimulatedFCM(responses={ANDROID_TOKEN: (404, dead)})
        user = await user_id_of(api, tokens)

        report = await dispatcher_for(api, apns, fcm, RecordingMetrics()).dispatch(
            user, a_context()
        )

        assert all(each.token_removed for each in report.attempts)
        async with api.app.state.session_factory() as session:
            assert await SqlDeviceRepository(session, FixedClock()).tokens_for(user) == []
        context = await api.client.get("/v1/escalations/call-1", headers=bearer(tokens))
        assert context.json()["delivery"] == "failed"

    async def test_a_repeated_dispatch_sends_nothing_more(self, api: Api) -> None:
        tokens = await sign_in(api)
        await register(api, tokens, {"platform": "ios", "token": HEX_DEVICE_TOKEN})
        apns, fcm = SimulatedAPNs(), SimulatedFCM()
        dispatcher = dispatcher_for(api, apns, fcm, RecordingMetrics())
        user = await user_id_of(api, tokens)

        await dispatcher.dispatch(user, a_context())
        again = await dispatcher.dispatch(user, a_context())

        assert again.result is DispatchResult.DEDUPLICATED
        assert len(apns.requests) == 1


class TestEscalationContext:
    async def test_it_returns_what_the_notification_carried(self, api: Api) -> None:
        tokens = await sign_in(api)
        await register(api, tokens, {"platform": "ios", "token": HEX_DEVICE_TOKEN})
        apns = SimulatedAPNs()
        await dispatcher_for(api, apns, SimulatedFCM(), RecordingMetrics()).dispatch(
            await user_id_of(api, tokens), a_context()
        )
        alert = json.loads(apns.requests[0].content)["aps"]["alert"]

        response = await api.client.get("/v1/escalations/call-1", headers=bearer(tokens))

        assert response.status_code == 200
        body = response.json()
        assert (body["title"], body["caller_label"], body["body"]) == (
            alert["title"],
            alert["subtitle"],
            alert["body"],
        )
        assert body == {
            "call_id": "call-1",
            "status": "live",
            "reason": "decision_needs_the_user",
            "title": "There is a decision only you can make",
            "caller_label": "a courier",
            "body": (
                "Needs from you: Where to leave it.\nSo far: They are at the gate with a parcel."
            ),
            "caller": "a courier",
            "established": "They are at the gate with a parcel.",
            "needed": "Where to leave it.",
            "raised_at": "2026-06-01T12:00:00Z",
            "ended_at": None,
            "delivery": "delivered",
        }

    async def test_it_is_there_when_no_push_could_be_sent(self, api: Api) -> None:
        # The context is stored before sending, so a user with no registered device — or one
        # whose every push failed — still has the escalation to open.
        tokens = await sign_in(api)
        await dispatcher_for(api, SimulatedAPNs(), SimulatedFCM(), RecordingMetrics()).dispatch(
            await user_id_of(api, tokens), a_context(caller_label=None, established=None)
        )
        response = await api.client.get("/v1/escalations/call-1", headers=bearer(tokens))
        assert response.status_code == 200
        body = response.json()
        assert body["delivery"] == "no_devices"
        assert body["caller"] is None
        assert body["caller_label"] == "Unknown caller"

    async def test_a_call_that_has_ended_reads_as_ended(self, api: Api) -> None:
        tokens = await sign_in(api)
        user = await user_id_of(api, tokens)
        dispatcher = dispatcher_for(api, SimulatedAPNs(), SimulatedFCM(), RecordingMetrics())
        await dispatcher.dispatch(user, a_context())
        ended_at = RAISED + timedelta(minutes=4)
        assert await dispatcher.call_ended(user, CallId("call-1"), ended_at)

        body = (await api.client.get("/v1/escalations/call-1", headers=bearer(tokens))).json()
        assert body["status"] == "ended"
        assert body["ended_at"] == "2026-06-01T12:04:00Z"

    async def test_another_user_s_escalation_is_not_found(self, api: Api) -> None:
        alice = await sign_in(api)
        bob = await sign_in(api, ANOTHER_NUMBER)
        await dispatcher_for(api, SimulatedAPNs(), SimulatedFCM(), RecordingMetrics()).dispatch(
            await user_id_of(api, alice), a_context()
        )
        response = await api.client.get("/v1/escalations/call-1", headers=bearer(bob))
        assert response.status_code == 404
        assert response.json()["error"] == "escalation_not_found"

    async def test_an_unknown_call_is_not_found(self, api: Api) -> None:
        tokens = await sign_in(api)
        response = await api.client.get("/v1/escalations/no-such-call", headers=bearer(tokens))
        assert response.status_code == 404

    async def test_it_needs_a_token(self, api: Api) -> None:
        assert (await api.client.get("/v1/escalations/call-1")).status_code == 401

    async def test_without_transcript_keys_it_is_unavailable_as_call_history_is(
        self, api: Api
    ) -> None:
        tokens = await sign_in(api)
        container = api.app.state.container
        api.app.state.container = replace(container, transcript_cipher=None)

        response = await api.client.get("/v1/escalations/call-1", headers=bearer(tokens))

        assert response.status_code == 503
        assert response.json()["error"] == "escalations_unavailable"

    @pytest.mark.parametrize("call_id", ["c" * 65, "%20padded%20"])
    async def test_a_malformed_call_id_is_a_422(self, api: Api, call_id: str) -> None:
        tokens = await sign_in(api)
        response = await api.client.get(f"/v1/escalations/{call_id}", headers=bearer(tokens))
        assert response.status_code == 422
