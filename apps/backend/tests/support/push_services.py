"""Stand-ins for Apple's and Google's push servers, at the HTTP layer.

The adapters under test are real: they build real requests, sign real tokens and parse real
responses. Only the far end is replaced, by handlers mounted on `httpx.MockTransport`, and those
handlers check what the real services check — the signature on the token, the headers, the path —
so a request the real service would refuse is refused here too rather than cheerfully accepted.

Keys are generated for each run. None is ever written to the repository.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cache
from typing import Any, Final
from urllib.parse import parse_qs, unquote

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

# Obviously invented identifiers, in the shapes the services use.
EXAMPLE_KEY_ID: Final = "EXAMPLEKEY"
EXAMPLE_TEAM_ID: Final = "EXAMPLETM1"
EXAMPLE_TOPIC: Final = "com.example.letmehandle"
EXAMPLE_PROJECT: Final = "example-project"
EXAMPLE_CLIENT_EMAIL: Final = "push-sender@example.com"
EXAMPLE_TOKEN_URI: Final = "https://oauth2.example.com/token"
# A 64-character hex device token, as iOS issues them. Invented.
HEX_DEVICE_TOKEN: Final = "0f" * 32


def _pem(key: ec.EllipticCurvePrivateKey | rsa.RSAPrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


@cache
def ec_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


@cache
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def ec_key_pem() -> str:
    return _pem(ec_key())


def rsa_key_pem() -> str:
    return _pem(rsa_key())


def service_account_json(**overrides: str) -> str:
    """A service account document with a generated key and invented identifiers."""
    document = {
        "type": "service_account",
        "project_id": EXAMPLE_PROJECT,
        "private_key_id": "example-key-id",
        "private_key": rsa_key_pem(),
        "client_email": EXAMPLE_CLIENT_EMAIL,
        "token_uri": EXAMPLE_TOKEN_URI,
    }
    document.update(overrides)
    return json.dumps(document)


@dataclass
class SimulatedAPNs:
    """Answers `/3/device/<token>` the way APNs does, after checking what APNs checks.

    `responses` maps a device token to the (status, reason) it gets; anything else is delivered.
    """

    responses: dict[str, tuple[int, str | None]] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)
    failure: Exception | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        refusal = self._refusal(request)
        if refusal is not None:
            return refusal
        device = unquote(request.url.path.removeprefix("/3/device/"))
        status, reason = self.responses.get(device, (200, None))
        if status == 200:
            return httpx.Response(200, headers={"apns-id": "example-apns-id"})
        body: dict[str, Any] = {"reason": reason}
        if status == 410:
            body["timestamp"] = 1_700_000_000_000
        return httpx.Response(status, json=body)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def _refusal(self, request: httpx.Request) -> httpx.Response | None:
        def refuse(status: int, reason: str) -> httpx.Response:
            return httpx.Response(status, json={"reason": reason})

        if request.method != "POST":
            return refuse(405, "MethodNotAllowed")
        if not request.url.path.startswith("/3/device/"):
            return refuse(404, "BadPath")
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("bearer "):
            return refuse(403, "MissingProviderToken")
        try:
            header = jwt.get_unverified_header(authorization.removeprefix("bearer "))
            claims = jwt.decode(
                authorization.removeprefix("bearer "),
                ec_key().public_key(),
                algorithms=["ES256"],
                options={"require": ["iss", "iat"]},
            )
        except jwt.PyJWTError:
            return refuse(403, "InvalidProviderToken")
        if header.get("kid") != EXAMPLE_KEY_ID or claims.get("iss") != EXAMPLE_TEAM_ID:
            return refuse(403, "InvalidProviderToken")
        if request.headers.get("apns-topic") != EXAMPLE_TOPIC:
            return refuse(400, "BadTopic")
        if len(request.headers.get("apns-collapse-id", "").encode()) > 64:
            return refuse(400, "BadCollapseId")
        if len(request.content) > 4096:
            return refuse(413, "PayloadTooLarge")
        return None


@dataclass
class SimulatedFCM:
    """Google's token endpoint and FCM's send endpoint, checking what they check.

    `responses` maps a registration token to the (status, error body) it gets.
    """

    responses: dict[str, tuple[int, dict[str, Any]]] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)
    token_requests: int = 0
    token_response: tuple[int, dict[str, Any]] | None = None
    expires_in: int = 3600
    failure: Exception | None = None
    issued: list[str] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.failure is not None:
            raise self.failure
        if str(request.url) == EXAMPLE_TOKEN_URI:
            return self._token(request)
        self.requests.append(request)
        expected = f"/v1/projects/{EXAMPLE_PROJECT}/messages:send"
        if request.method != "POST" or request.url.path != expected:
            return _fcm_error(404, "NOT_FOUND", "no such method")
        if request.headers.get("authorization") not in {f"Bearer {each}" for each in self.issued}:
            return _fcm_error(401, "UNAUTHENTICATED", "invalid credentials")
        message = json.loads(request.content)["message"]
        status, body = self.responses.get(message["token"], (200, {}))
        if status == 200:
            return httpx.Response(
                200, json={"name": f"projects/{EXAMPLE_PROJECT}/messages/example-1"}
            )
        return httpx.Response(status, json=body)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def _token(self, request: httpx.Request) -> httpx.Response:
        self.token_requests += 1
        if self.token_response is not None:
            status, body = self.token_response
            return httpx.Response(status, json=body)
        form = parse_qs(request.content.decode())
        assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
        claims = jwt.decode(
            form["assertion"][0],
            rsa_key().public_key(),
            algorithms=["RS256"],
            audience=EXAMPLE_TOKEN_URI,
            options={"require": ["iss", "iat", "exp", "scope"]},
        )
        assert claims["iss"] == EXAMPLE_CLIENT_EMAIL
        assert claims["scope"] == "https://www.googleapis.com/auth/firebase.messaging"
        access = f"example-access-{self.token_requests}"
        self.issued.append(access)
        return httpx.Response(
            200,
            json={"access_token": access, "expires_in": self.expires_in, "token_type": "Bearer"},
        )


def _fcm_error(
    status: int, name: str, message: str, details: list[dict[str, Any]] | None = None
) -> httpx.Response:
    return httpx.Response(status, json=fcm_error_body(status, name, message, details))


def fcm_error_body(
    status: int,
    name: str,
    message: str = "an error",
    details: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """An error in the shape Google APIs return them."""
    return {"error": {"code": status, "message": message, "status": name, "details": details or []}}


def fcm_error_code(code: str) -> dict[str, Any]:
    return {"@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError", "errorCode": code}


def bad_request_on(field_name: str) -> dict[str, Any]:
    return {
        "@type": "type.googleapis.com/google.rpc.BadRequest",
        "fieldViolations": [{"field": field_name, "description": "invalid"}],
    }
