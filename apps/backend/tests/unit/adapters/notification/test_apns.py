"""The APNs adapter: the request it makes, the token it signs, and what every response means."""

from __future__ import annotations

import json
from datetime import timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from letmehandle.adapters.notification.apns import provider as apns_module
from letmehandle.adapters.notification.apns.provider import (
    PAYLOAD_LIMIT_BYTES,
    APNsEnvironment,
    APNsNotificationProvider,
    build_client,
    encode,
    outcome_for,
)
from letmehandle.adapters.notification.apns.token import APNsProviderToken
from letmehandle.adapters.notification.shared import CredentialError
from letmehandle.domain.errors import ProviderError
from letmehandle.domain.models.identifiers import CallId
from letmehandle.domain.ports.notification import (
    DeliveryStatus,
    DevicePlatform,
    DeviceToken,
    EscalationNotification,
)
from tests.contracts.fakes import FixedClock
from tests.contracts.test_apns_notification_contract import apns_provider
from tests.support.push_services import (
    EXAMPLE_KEY_ID,
    EXAMPLE_TEAM_ID,
    EXAMPLE_TOPIC,
    HEX_DEVICE_TOKEN,
    SimulatedAPNs,
    ec_key,
    ec_key_pem,
    rsa_key_pem,
)

TOKEN = DeviceToken(DevicePlatform.IOS, HEX_DEVICE_TOKEN)
NOTIFICATION = EscalationNotification(
    call_id=CallId("call-1"),
    title="The caller asked for you",
    body="Needs from you: where to leave a parcel.",
    caller_label="a courier",
    data={"kind": "escalation", "call_id": "call-1"},
)


def signer(clock: FixedClock, **overrides: str) -> APNsProviderToken:
    values = {
        "key_id": EXAMPLE_KEY_ID,
        "team_id": EXAMPLE_TEAM_ID,
        "private_key": ec_key_pem(),
    }
    values.update(overrides)
    return APNsProviderToken(clock=clock, **values)


class TestTheRequest:
    async def test_it_posts_a_time_sensitive_alert_for_the_device(self) -> None:
        service = SimulatedAPNs()
        clock = FixedClock()
        outcome = await apns_provider(service, clock).send(TOKEN, NOTIFICATION)

        assert outcome.status is DeliveryStatus.DELIVERED
        (request,) = service.requests
        assert request.url.path == f"/3/device/{HEX_DEVICE_TOKEN}"
        assert request.headers["apns-push-type"] == "alert"
        assert request.headers["apns-priority"] == "10"
        assert request.headers["apns-topic"] == EXAMPLE_TOPIC
        assert request.headers["apns-collapse-id"] == "call-1"
        expected_expiry = int((clock.now() + timedelta(minutes=10)).timestamp())
        assert request.headers["apns-expiration"] == str(expected_expiry)
        body = json.loads(request.content)
        assert body == {
            "kind": "escalation",
            "call_id": "call-1",
            "aps": {
                "alert": {
                    "title": "The caller asked for you",
                    "subtitle": "a courier",
                    "body": "Needs from you: where to leave a parcel.",
                },
                "sound": "default",
                "interruption-level": "time-sensitive",
            },
        }

    async def test_the_device_token_cannot_change_the_path(self) -> None:
        service = SimulatedAPNs()
        await apns_provider(service).send(
            DeviceToken(DevicePlatform.IOS, "../../x?y"), NOTIFICATION
        )
        assert service.requests[0].url.raw_path == b"/3/device/..%2F..%2Fx%3Fy"

    async def test_a_long_call_id_collapses_under_a_digest_that_apns_accepts(self) -> None:
        service = SimulatedAPNs()
        long_call = EscalationNotification(
            call_id=CallId("c" * 65), title="t", body="b", caller_label="someone"
        )
        outcome = await apns_provider(service).send(TOKEN, long_call)
        assert outcome.succeeded
        assert len(service.requests[0].headers["apns-collapse-id"]) == 64

    def test_data_cannot_replace_the_alert(self) -> None:
        hostile = EscalationNotification(
            call_id=CallId("c"), title="t", body="b", caller_label="x", data={"aps": "nothing"}
        )
        assert json.loads(encode(hostile))["aps"]["alert"]["title"] == "t"

    def test_it_measures_what_it_sends_against_apples_limit(self) -> None:
        provider = apns_provider(SimulatedAPNs())
        assert provider.payload_limit_bytes == PAYLOAD_LIMIT_BYTES == 4096
        assert provider.payload_size(NOTIFICATION) == len(encode(NOTIFICATION))
        wide = EscalationNotification(call_id=CallId("c"), title="界", body="b", caller_label="x")
        assert provider.payload_size(wide) == len(encode(wide))
        assert "界".encode() in encode(wide)

    async def test_a_token_for_another_platform_is_a_defect(self) -> None:
        with pytest.raises(ProviderError, match="cannot be sent by APNs"):
            await apns_provider(SimulatedAPNs()).send(
                DeviceToken(DevicePlatform.ANDROID, "t"), NOTIFICATION
            )

    def test_it_names_itself_and_its_platform(self) -> None:
        provider = apns_provider(SimulatedAPNs())
        assert provider.name == "apns"
        assert provider.platform is DevicePlatform.IOS

    @pytest.mark.parametrize(
        ("environment", "host"),
        [
            (APNsEnvironment.SANDBOX, "api.sandbox.push.apple.com"),
            (APNsEnvironment.PRODUCTION, "api.push.apple.com"),
        ],
    )
    async def test_the_default_client_speaks_http2_to_the_right_host(
        self, environment: APNsEnvironment, host: str
    ) -> None:
        client = build_client(environment, timeout=3)
        try:
            assert client.base_url.host == host
            # httpx keeps the flag on its connection pool; there is no public accessor.
            assert client._transport._pool._http2 is True  # type: ignore[attr-defined]
        finally:
            await client.aclose()

    async def test_without_a_client_it_builds_the_http2_one_and_closes_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        built: list[tuple[APNsEnvironment, float]] = []
        service = SimulatedAPNs()

        def recording(environment: APNsEnvironment, *, timeout: float) -> httpx.AsyncClient:
            built.append((environment, timeout))
            return httpx.AsyncClient(base_url="https://apns.test", transport=service.transport())

        monkeypatch.setattr(apns_module, "build_client", recording)
        clock = FixedClock()
        provider = APNsNotificationProvider(
            token=signer(clock),
            topic=EXAMPLE_TOPIC,
            environment=APNsEnvironment.PRODUCTION,
            clock=clock,
        )
        assert built == [(APNsEnvironment.PRODUCTION, 10.0)]
        await provider.aclose()


class TestTheProviderToken:
    def test_it_is_es256_signed_with_the_key_id_and_team(self) -> None:
        clock = FixedClock()
        token = signer(clock).current()
        assert jwt.get_unverified_header(token) == {
            "alg": "ES256",
            "kid": EXAMPLE_KEY_ID,
            "typ": "JWT",
        }
        claims = jwt.decode(token, ec_key().public_key(), algorithms=["ES256"])
        assert claims == {"iss": EXAMPLE_TEAM_ID, "iat": int(clock.now().timestamp())}

    def test_it_is_reused_until_fifty_minutes_old_then_replaced(self) -> None:
        clock = FixedClock()
        tokens = signer(clock)
        first = tokens.current()
        clock.advance(49 * 60 + 59)
        assert tokens.current() == first
        clock.advance(1)
        assert tokens.current() != first

    def test_a_rejected_token_is_replaced_only_once_it_is_twenty_minutes_old(self) -> None:
        # Replacing sooner is itself an error from APNs: TooManyProviderTokenUpdates.
        clock = FixedClock()
        tokens = signer(clock)
        first = tokens.current()
        clock.advance(19 * 60)
        tokens.rejected()
        assert tokens.current() == first
        clock.advance(60)
        tokens.rejected()
        assert tokens.current() != first

    def test_rejecting_before_any_token_exists_does_nothing(self) -> None:
        tokens = signer(FixedClock())
        tokens.rejected()
        assert tokens.current()

    async def test_an_expired_provider_token_response_replaces_it_for_the_next_send(self) -> None:
        clock = FixedClock()
        service = SimulatedAPNs(responses={HEX_DEVICE_TOKEN: (403, "ExpiredProviderToken")})
        provider = apns_provider(service, clock)
        await provider.send(TOKEN, NOTIFICATION)
        clock.advance(21 * 60)
        outcome = await provider.send(TOKEN, NOTIFICATION)
        await provider.send(TOKEN, NOTIFICATION)
        assert outcome.status is DeliveryStatus.FAILED
        # Kept after the first rejection (too young to replace), replaced after the second.
        first, second, third = (each.headers["authorization"] for each in service.requests)
        assert first == second != third

    def test_a_key_that_is_not_pem_is_refused_without_repeating_it(self) -> None:
        with pytest.raises(CredentialError, match="not a readable PEM") as caught:
            signer(FixedClock(), private_key="secret-looking-text")
        assert "secret-looking-text" not in str(caught.value)
        assert caught.value.__cause__ is None

    def test_a_key_on_one_line_with_escaped_newlines_is_accepted(self) -> None:
        one_line = ec_key_pem().replace("\n", "\\n")
        assert signer(FixedClock(), private_key=one_line).current()

    def test_an_rsa_key_is_refused(self) -> None:
        with pytest.raises(CredentialError, match="P-256"):
            signer(FixedClock(), private_key=rsa_key_pem())

    def test_a_key_on_another_curve_is_refused(self) -> None:
        other = ec.generate_private_key(ec.SECP384R1()).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        with pytest.raises(CredentialError, match="P-256"):
            signer(FixedClock(), private_key=other.decode())

    @pytest.mark.parametrize("missing", ["key_id", "team_id"])
    def test_a_key_without_its_identifiers_is_refused(self, missing: str) -> None:
        with pytest.raises(CredentialError, match="key id and its team id"):
            signer(FixedClock(), **{missing: " "})


# Every documented reason, with the outcome it must produce.
DOCUMENTED = [
    (400, "BadCollapseId", DeliveryStatus.REJECTED),
    (400, "BadDeviceToken", DeliveryStatus.TOKEN_INVALID),
    (400, "BadExpirationDate", DeliveryStatus.REJECTED),
    (400, "BadMessageId", DeliveryStatus.REJECTED),
    (400, "BadPriority", DeliveryStatus.REJECTED),
    (400, "BadTopic", DeliveryStatus.REJECTED),
    (400, "DeviceTokenNotForTopic", DeliveryStatus.REJECTED),
    (400, "DuplicateHeaders", DeliveryStatus.REJECTED),
    (400, "IdleTimeout", DeliveryStatus.FAILED),
    (400, "InvalidPushType", DeliveryStatus.REJECTED),
    (400, "MissingDeviceToken", DeliveryStatus.REJECTED),
    (400, "MissingTopic", DeliveryStatus.REJECTED),
    (400, "PayloadEmpty", DeliveryStatus.REJECTED),
    (400, "TopicDisallowed", DeliveryStatus.REJECTED),
    (403, "BadCertificate", DeliveryStatus.REJECTED),
    (403, "BadCertificateEnvironment", DeliveryStatus.REJECTED),
    (403, "ExpiredProviderToken", DeliveryStatus.FAILED),
    (403, "Forbidden", DeliveryStatus.REJECTED),
    (403, "InvalidProviderToken", DeliveryStatus.REJECTED),
    (403, "MissingProviderToken", DeliveryStatus.REJECTED),
    (403, "UnrelatedKeyIdInToken", DeliveryStatus.REJECTED),
    (403, "BadEnvironmentKeyIdInToken", DeliveryStatus.REJECTED),
    (404, "BadPath", DeliveryStatus.REJECTED),
    (405, "MethodNotAllowed", DeliveryStatus.REJECTED),
    (410, "ExpiredToken", DeliveryStatus.TOKEN_INVALID),
    (410, "Unregistered", DeliveryStatus.TOKEN_INVALID),
    (413, "PayloadTooLarge", DeliveryStatus.REJECTED),
    (429, "TooManyProviderTokenUpdates", DeliveryStatus.FAILED),
    (429, "TooManyRequests", DeliveryStatus.FAILED),
    (500, "InternalServerError", DeliveryStatus.FAILED),
    (503, "ServiceUnavailable", DeliveryStatus.FAILED),
    (503, "Shutdown", DeliveryStatus.FAILED),
]


class TestResponses:
    @pytest.mark.parametrize(("status", "reason", "expected"), DOCUMENTED)
    async def test_every_documented_response_has_its_outcome(
        self, status: int, reason: str, expected: DeliveryStatus
    ) -> None:
        service = SimulatedAPNs(responses={HEX_DEVICE_TOKEN: (status, reason)})
        outcome = await apns_provider(service).send(TOKEN, NOTIFICATION)
        assert outcome.status is expected
        assert outcome.detail == f"{status} {reason}"
        assert HEX_DEVICE_TOKEN not in (outcome.detail or "")

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (410, DeliveryStatus.TOKEN_INVALID),
            (429, DeliveryStatus.FAILED),
            (500, DeliveryStatus.FAILED),
            (502, DeliveryStatus.FAILED),
            (400, DeliveryStatus.REJECTED),
            (302, DeliveryStatus.FAILED),
        ],
    )
    def test_an_undocumented_or_missing_reason_falls_back_on_the_status(
        self, status: int, expected: DeliveryStatus
    ) -> None:
        outcome = outcome_for(status, None)
        assert outcome.status is expected
        assert outcome.detail == str(status)

    async def test_a_body_that_is_not_json_is_read_by_status(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(502, text="<html>bad gateway</html>")

        provider = apns_provider(SimulatedAPNs())
        provider._client = httpx.AsyncClient(
            base_url="https://apns.test", transport=httpx.MockTransport(handler)
        )
        outcome = await provider.send(TOKEN, NOTIFICATION)
        assert outcome.status is DeliveryStatus.FAILED
        assert outcome.detail == "502"

    async def test_a_body_that_is_json_but_not_an_error_is_read_by_status(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json=["unexpected"])

        provider = apns_provider(SimulatedAPNs())
        provider._client = httpx.AsyncClient(
            base_url="https://apns.test", transport=httpx.MockTransport(handler)
        )
        assert (await provider.send(TOKEN, NOTIFICATION)).status is DeliveryStatus.REJECTED

    async def test_a_transport_failure_is_a_retryable_outcome_that_names_no_token(self) -> None:
        service = SimulatedAPNs(
            failure=httpx.ConnectError(f"cannot reach /3/device/{HEX_DEVICE_TOKEN}")
        )
        outcome = await apns_provider(service).send(TOKEN, NOTIFICATION)
        assert outcome.status is DeliveryStatus.FAILED
        assert outcome.detail == "transport: ConnectError"
