"""An in-process verification API answering start and check requests, with fictional ids."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from urllib.parse import parse_qsl

import httpx

from tests.support.simulated_sms import SMS_ACCOUNT, SMS_TOKEN

VERIFY_SERVICE: Final = "verify-service-simulated"

_HOST: Final = "verify.twilio.com"

_SERVICE_PATH: Final = f"/v2/Services/{VERIFY_SERVICE}"
_VERIFICATIONS_PATH: Final = f"{_SERVICE_PATH}/Verifications"
_CHECK_PATH: Final = f"{_SERVICE_PATH}/VerificationCheck"

# The service's own codes for a number it will not deliver to, and for its limits.
INVALID_PARAMETER: Final = 60200
NOT_A_NUMBER: Final = 21211
NOT_A_MOBILE: Final = 21614
MAX_SEND_ATTEMPTS: Final = 60203
MAX_CHECK_ATTEMPTS: Final = 60202
NOT_FOUND: Final = 20404
# A refusal about the account rather than the number.
DELIVERY_BLOCKED: Final = 60410

# Wrong codes one verification takes before the service refuses to check it again.
CHECKS_PER_VERIFICATION: Final = 5


class Behaviour(StrEnum):
    """How the service answers every request, whatever it is asked."""

    ANSWERS = "answers"
    THROTTLES = "throttles"
    DOWN = "down"
    SILENT = "silent"
    UNREACHABLE = "unreachable"


@dataclass(slots=True)
class _Pending:
    code: str
    wrong_checks: int = 0


@dataclass(frozen=True, slots=True)
class TextedCode:
    to: str
    code: str


class SimulatedVerify:
    """The service, with what it texted and what it was asked."""

    def __init__(self) -> None:
        self.behaviour = Behaviour.ANSWERS
        self.refusals: dict[str, int] = {}
        self.texted: list[TextedCode] = []
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)
        self._pending: dict[str, _Pending] = {}
        self._issued = 0

    def refuse(self, number: str, code: int) -> None:
        """Refuse to start a verification for `number` with the service's error `code`."""
        self.refusals[number] = code

    def expire(self, number: str) -> None:
        """Let the pending verification for `number` run out, as the service's own expiry does."""
        del self._pending[number]

    def last_code(self) -> str:
        """The code in the last text sent, as somebody reads it off their phone."""
        return self.texted[-1].code

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        expected = "Basic " + base64.b64encode(f"{SMS_ACCOUNT}:{SMS_TOKEN}".encode()).decode()
        if request.headers.get("Authorization") != expected:
            return _error(401, 20003)
        match self.behaviour:
            case Behaviour.THROTTLES:
                return _error(429, MAX_SEND_ATTEMPTS)
            case Behaviour.DOWN:
                return _error(503, 20500)
            case Behaviour.SILENT:
                raise httpx.ReadTimeout("the service did not answer", request=request)
            case Behaviour.UNREACHABLE:
                raise httpx.ConnectError("the service could not be reached", request=request)
            case Behaviour.ANSWERS:
                pass
        fields = dict(parse_qsl(request.content.decode(), keep_blank_values=True))
        if request.url.host != _HOST:
            return _error(404, NOT_FOUND)
        if request.method == "POST" and request.url.path == _VERIFICATIONS_PATH:
            return self._start(fields)
        if request.method == "POST" and request.url.path == _CHECK_PATH:
            return self._check(fields)
        return _error(404, NOT_FOUND)

    def _start(self, fields: dict[str, str]) -> httpx.Response:
        to = fields.get("To", "")
        refusal = self.refusals.get(to)
        if refusal is not None:
            return _error(400, refusal)
        if fields.get("Channel") != "sms":
            return _error(400, INVALID_PARAMETER)
        pending = self._pending.get(to)
        if pending is None:
            self._issued += 1
            pending = self._pending[to] = _Pending(code=f"{700000 + self._issued}")
        self.texted.append(TextedCode(to=to, code=pending.code))
        return httpx.Response(201, json={"sid": "VEsimulated", "status": "pending", "to": to})

    def _check(self, fields: dict[str, str]) -> httpx.Response:
        to = fields.get("To", "")
        pending = self._pending.get(to)
        if pending is None:
            return _error(404, NOT_FOUND)
        if pending.wrong_checks >= CHECKS_PER_VERIFICATION:
            return _error(429, MAX_CHECK_ATTEMPTS)
        if fields.get("Code") != pending.code:
            pending.wrong_checks += 1
            return httpx.Response(200, json={"status": "pending", "valid": False, "to": to})
        del self._pending[to]
        return httpx.Response(200, json={"status": "approved", "valid": True, "to": to})


def _error(status: int, code: int) -> httpx.Response:
    return httpx.Response(status, json={"code": code, "status": status})
