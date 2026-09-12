"""The FCM adapter: the message, the access token, and what every error means."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from letmehandle.adapters.notification.fcm.credentials import (
    DEFAULT_TOKEN_URI,
    AccessTokenSource,
    ServiceAccount,
)
from letmehandle.adapters.notification.fcm.provider import (
    PAYLOAD_LIMIT_BYTES,
    FCMNotificationProvider,
    build_client,
)
from letmehandle.adapters.notification.shared import CredentialError, compact_json
from letmehandle.domain.errors import ProviderError
from letmehandle.domain.models.identifiers import CallId
from letmehandle.domain.ports.notification import (
    DeliveryStatus,
    DevicePlatform,
    DeviceToken,
    EscalationNotification,
)
from tests.contracts.fakes import FixedClock
from tests.contracts.test_fcm_notification_contract import fcm_provider
from tests.support.push_services import (
    EXAMPLE_CLIENT_EMAIL,
    EXAMPLE_PROJECT,
    SimulatedFCM,
    bad_request_on,
    ec_key_pem,
    fcm_error_body,
    fcm_error_code,
    rsa_key_pem,
    service_account_json,
)

REGISTRATION = "example-registration-token:APA91b-example_value"
TOKEN = DeviceToken(DevicePlatform.ANDROID, REGISTRATION)
NOTIFICATION = EscalationNotification(
    call_id=CallId("call-1"),
    title="The caller asked for you",
    body="Needs from you: where to leave a parcel.",
    caller_label="a courier",
    data={"kind": "escalation", "call_id": "call-1"},
)


def error(status: int, name: str, *details: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    return status, fcm_error_body(status, name, details=list(details))


class TestTheMessage:
    async def test_it_sends_a_high_priority_collapsing_notification_to_the_device(self) -> None:
        service = SimulatedFCM()
        outcome = await fcm_provider(service).send(TOKEN, NOTIFICATION)

        assert outcome.status is DeliveryStatus.DELIVERED
        (request,) = service.requests
        assert str(request.url) == (
            f"https://fcm.googleapis.com/v1/projects/{EXAMPLE_PROJECT}/messages:send"
        )
        assert json.loads(request.content) == {
            "message": {
                "token": REGISTRATION,
                "notification": {
                    "title": "The caller asked for you",
                    "body": "a courier\nNeeds from you: where to leave a parcel.",
                },
                "data": {"kind": "escalation", "call_id": "call-1"},
                "android": {
                    "priority": "HIGH",
                    "ttl": "600s",
                    "collapse_key": "call-1",
                    "notification": {"tag": "call-1", "channel_id": "escalation"},
                },
            }
        }

    def test_it_measures_the_message_without_its_address_against_the_limit(self) -> None:
        provider = fcm_provider(SimulatedFCM())
        assert provider.payload_limit_bytes == PAYLOAD_LIMIT_BYTES == 4096
        assert provider.payload_size(NOTIFICATION) == len(
            compact_json(provider.message(NOTIFICATION))
        )
        assert "token" not in provider.message(NOTIFICATION)

    async def test_a_token_for_another_platform_is_a_defect(self) -> None:
        with pytest.raises(ProviderError, match="cannot be sent by FCM"):
            await fcm_provider(SimulatedFCM()).send(
                DeviceToken(DevicePlatform.IOS, "t"), NOTIFICATION
            )

    async def test_it_names_itself_and_closes_its_client(self) -> None:
        provider = fcm_provider(SimulatedFCM())
        assert provider.name == "fcm"
        assert provider.platform is DevicePlatform.ANDROID
        await provider.aclose()
        assert provider._client.is_closed

    async def test_the_default_client_offers_http2(self) -> None:
        client = build_client(timeout=3)
        try:
            assert client._transport._pool._http2 is True  # type: ignore[attr-defined]
        finally:
            await client.aclose()


class TestTheAccessToken:
    async def test_one_token_serves_many_sends_until_near_expiry(self) -> None:
        clock = FixedClock()
        service = SimulatedFCM()
        provider = fcm_provider(service, clock)
        await provider.send(TOKEN, NOTIFICATION)
        clock.advance(54 * 60)
        await provider.send(TOKEN, NOTIFICATION)
        assert service.token_requests == 1
        clock.advance(60)
        await provider.send(TOKEN, NOTIFICATION)
        assert service.token_requests == 2
        assert [each.headers["authorization"] for each in service.requests] == [
            "Bearer example-access-1",
            "Bearer example-access-1",
            "Bearer example-access-2",
        ]

    async def test_a_short_lived_token_is_refreshed_halfway(self) -> None:
        clock = FixedClock()
        service = SimulatedFCM(expires_in=300)
        provider = fcm_provider(service, clock)
        await provider.send(TOKEN, NOTIFICATION)
        clock.advance(149)
        await provider.send(TOKEN, NOTIFICATION)
        assert service.token_requests == 1
        clock.advance(1)
        await provider.send(TOKEN, NOTIFICATION)
        assert service.token_requests == 2

    async def test_concurrent_sends_share_one_refresh(self) -> None:
        service = SimulatedFCM()
        provider = fcm_provider(service)
        outcomes = await asyncio.gather(*(provider.send(TOKEN, NOTIFICATION) for _ in range(5)))
        assert all(outcome.succeeded for outcome in outcomes)
        assert service.token_requests == 1

    async def test_a_refused_access_token_is_replaced_on_the_next_send(self) -> None:
        service = SimulatedFCM()
        provider = fcm_provider(service)
        await provider.send(TOKEN, NOTIFICATION)
        service.issued.clear()  # the service stops honouring what it issued
        outcome = await provider.send(TOKEN, NOTIFICATION)
        assert outcome.status is DeliveryStatus.FAILED
        assert outcome.detail == "401 UNAUTHENTICATED"
        assert (await provider.send(TOKEN, NOTIFICATION)).succeeded
        assert service.token_requests == 2

    async def test_a_refused_service_account_is_a_rejection_naming_only_the_error_code(
        self,
    ) -> None:
        service = SimulatedFCM(
            token_response=(400, {"error": "invalid_grant", "error_description": "Invalid JWT"})
        )
        outcome = await fcm_provider(service).send(TOKEN, NOTIFICATION)
        assert outcome.status is DeliveryStatus.REJECTED
        assert outcome.detail == "authorisation: token endpoint 400 invalid_grant"

    @pytest.mark.parametrize(
        ("response", "detail", "status"),
        [
            ((503, {}), "authorisation: token endpoint 503", DeliveryStatus.FAILED),
            (
                (429, {"error": "rate limited!"}),
                "authorisation: token endpoint 429",
                DeliveryStatus.FAILED,
            ),
            (
                (200, {"token_type": "Bearer"}),
                "authorisation: token endpoint answered without a token",
                DeliveryStatus.FAILED,
            ),
            (
                (200, {"access_token": ""}),
                "authorisation: token endpoint answered without a token",
                DeliveryStatus.FAILED,
            ),
            (
                (200, ["not", "an", "object"]),
                "authorisation: token endpoint answered without a token",
                DeliveryStatus.FAILED,
            ),
            (
                (401, ["not", "an", "object"]),
                "authorisation: token endpoint 401",
                DeliveryStatus.REJECTED,
            ),
        ],
    )
    async def test_an_unusable_token_response_is_an_outcome(
        self, response: tuple[int, Any], detail: str, status: DeliveryStatus
    ) -> None:
        service = SimulatedFCM(token_response=response)
        outcome = await fcm_provider(service).send(TOKEN, NOTIFICATION)
        assert outcome.status is status
        assert outcome.detail == detail
        assert service.requests == []

    async def test_a_token_endpoint_that_is_not_json_is_an_outcome(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="<html>oops</html>")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        tokens = AccessTokenSource(
            ServiceAccount.parse(service_account_json()), client=client, clock=FixedClock()
        )
        provider = FCMNotificationProvider(project_id=EXAMPLE_PROJECT, tokens=tokens, client=client)
        outcome = await provider.send(TOKEN, NOTIFICATION)
        assert outcome.detail == "authorisation: token endpoint 500"

    async def test_an_unreachable_token_endpoint_is_retryable(self) -> None:
        service = SimulatedFCM(failure=httpx.ConnectTimeout("slow"))
        outcome = await fcm_provider(service).send(TOKEN, NOTIFICATION)
        assert outcome.status is DeliveryStatus.FAILED
        assert outcome.detail == "authorisation: transport: ConnectTimeout"


class TestTheServiceAccount:
    def test_it_reads_what_a_token_needs(self) -> None:
        account = ServiceAccount.parse(service_account_json())
        assert account.client_email == EXAMPLE_CLIENT_EMAIL
        assert account.private_key_id == "example-key-id"

    def test_the_standard_token_endpoint_is_assumed_when_none_is_given(self) -> None:
        document = json.loads(service_account_json())
        del document["token_uri"]
        assert ServiceAccount.parse(json.dumps(document)).token_uri == DEFAULT_TOKEN_URI

    @pytest.mark.parametrize(
        ("document", "message"),
        [
            ("{not json", "not valid JSON"),
            ("[1, 2]", "not a JSON object"),
            (service_account_json(client_email=" "), "has no client_email"),
            (service_account_json(private_key="not a key"), "not a readable PEM"),
        ],
    )
    def test_an_unusable_account_is_refused_without_repeating_it(
        self, document: str, message: str
    ) -> None:
        with pytest.raises(CredentialError, match=message) as caught:
            ServiceAccount.parse(document)
        assert "PRIVATE KEY" not in str(caught.value)

    def test_a_key_that_is_not_rsa_is_refused(self) -> None:
        with pytest.raises(CredentialError, match="RSA"):
            ServiceAccount.parse(service_account_json(private_key=ec_key_pem()))

    def test_an_escaped_key_as_found_in_a_real_document_is_read(self) -> None:
        # json.dumps escapes the newlines; this is the same document written by hand, once more.
        assert ServiceAccount.parse(service_account_json(private_key=rsa_key_pem())).private_key


class TestResponses:
    @pytest.mark.parametrize(
        ("response", "expected", "detail"),
        [
            (
                error(404, "NOT_FOUND", fcm_error_code("UNREGISTERED")),
                DeliveryStatus.TOKEN_INVALID,
                "404 UNREGISTERED",
            ),
            (
                error(
                    400,
                    "INVALID_ARGUMENT",
                    fcm_error_code("INVALID_ARGUMENT"),
                    bad_request_on("message.token"),
                ),
                DeliveryStatus.TOKEN_INVALID,
                "400 INVALID_ARGUMENT",
            ),
            (
                error(
                    400,
                    "INVALID_ARGUMENT",
                    fcm_error_code("INVALID_ARGUMENT"),
                    bad_request_on("message.android.ttl"),
                ),
                DeliveryStatus.REJECTED,
                "400 INVALID_ARGUMENT",
            ),
            (error(400, "INVALID_ARGUMENT"), DeliveryStatus.REJECTED, "400 INVALID_ARGUMENT"),
            (
                error(403, "PERMISSION_DENIED", fcm_error_code("SENDER_ID_MISMATCH")),
                DeliveryStatus.REJECTED,
                "403 SENDER_ID_MISMATCH",
            ),
            (
                error(429, "RESOURCE_EXHAUSTED", fcm_error_code("QUOTA_EXCEEDED")),
                DeliveryStatus.FAILED,
                "429 QUOTA_EXCEEDED",
            ),
            (
                error(503, "UNAVAILABLE", fcm_error_code("UNAVAILABLE")),
                DeliveryStatus.FAILED,
                "503 UNAVAILABLE",
            ),
            (
                error(500, "INTERNAL", fcm_error_code("INTERNAL")),
                DeliveryStatus.FAILED,
                "500 INTERNAL",
            ),
            (
                error(401, "UNAUTHENTICATED", fcm_error_code("THIRD_PARTY_AUTH_ERROR")),
                DeliveryStatus.REJECTED,
                "401 THIRD_PARTY_AUTH_ERROR",
            ),
            (
                error(400, "INVALID_ARGUMENT", fcm_error_code("UNSPECIFIED_ERROR")),
                DeliveryStatus.REJECTED,
                "400 UNSPECIFIED_ERROR",
            ),
            (error(404, "NOT_FOUND"), DeliveryStatus.REJECTED, "404 NOT_FOUND"),
            (error(502, "BAD_GATEWAY"), DeliveryStatus.FAILED, "502 BAD_GATEWAY"),
            ((302, {}), DeliveryStatus.FAILED, "302"),
            ((400, {"error": "a string"}), DeliveryStatus.REJECTED, "400"),
            ((400, {"error": {"status": 7, "details": "nope"}}), DeliveryStatus.REJECTED, "400"),
            (
                (
                    400,
                    {
                        "error": {
                            "status": "INVALID_ARGUMENT",
                            "details": [
                                "x",
                                {
                                    "@type": "type.googleapis.com/google.rpc.BadRequest",
                                    "fieldViolations": "x",
                                },
                                {
                                    "@type": "type.googleapis.com/google.rpc.BadRequest",
                                    "fieldViolations": ["x", {"field": 3}],
                                },
                            ],
                        }
                    },
                ),
                DeliveryStatus.REJECTED,
                "400 INVALID_ARGUMENT",
            ),
        ],
    )
    async def test_every_documented_error_has_its_outcome(
        self, response: tuple[int, dict[str, Any]], expected: DeliveryStatus, detail: str
    ) -> None:
        service = SimulatedFCM(responses={REGISTRATION: response})
        outcome = await fcm_provider(service).send(TOKEN, NOTIFICATION)
        assert outcome.status is expected
        assert outcome.detail == detail
        assert REGISTRATION not in (outcome.detail or "")

    async def test_a_body_that_is_not_json_is_read_by_status(self) -> None:
        service = SimulatedFCM()
        provider = fcm_provider(service)
        await provider.send(TOKEN, NOTIFICATION)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="unavailable")

        provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        outcome = await provider.send(TOKEN, NOTIFICATION)
        assert (outcome.status, outcome.detail) == (DeliveryStatus.FAILED, "503")

    async def test_a_transport_failure_while_sending_is_retryable(self) -> None:
        service = SimulatedFCM()
        provider = fcm_provider(service)
        await provider.send(TOKEN, NOTIFICATION)
        service.failure = httpx.ReadTimeout("slow")
        outcome = await provider.send(TOKEN, NOTIFICATION)
        assert (outcome.status, outcome.detail) == (DeliveryStatus.FAILED, "transport: ReadTimeout")
