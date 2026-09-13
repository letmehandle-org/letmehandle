"""A text-message API in this process, answering the sign-in code provider's requests.

It answers the one request the provider makes — create a message — the way the documented API does:
authenticated as the account, refusing a number it cannot deliver to with a 400 and the numeric code
for why, throttling with a 429, failing with a 5xx, or not answering at all. What it accepted is
kept, so a test reads a code the way a person reads the message on their phone.

Every identifier says it is simulated, and every number is in a range reserved for fiction.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from urllib.parse import parse_qsl

import httpx

from letmehandle.domain.models.auth import CODE_LENGTH
from letmehandle.domain.models.phone_number import PhoneNumber

SMS_ACCOUNT: Final = "sms-account-simulated"
SMS_TOKEN: Final = "sms-auth-token-simulated"
SMS_SENDER: Final = PhoneNumber.parse("+12025550101")

_MESSAGES_PATH: Final = f"/2010-04-01/Accounts/{SMS_ACCOUNT}/Messages.json"

# The provider's own codes for a number it will not deliver to: not a number, not one that can
# receive a text, one whose owner asked for no more, one no carrier routes.
NOT_A_NUMBER: Final = 21211
NOT_A_MOBILE: Final = 21614
UNSUBSCRIBED: Final = 21610
TOO_MANY_REQUESTS: Final = 20429
# A refusal about the account rather than the number.
REGION_NOT_ENABLED: Final = 21408


class Behaviour(StrEnum):
    """How the service answers every request, whatever it is asked."""

    DELIVERS = "delivers"
    THROTTLES = "throttles"
    DOWN = "down"
    SILENT = "silent"
    UNREACHABLE = "unreachable"


@dataclass(frozen=True, slots=True)
class SentMessage:
    to: str
    sender: str
    body: str


class SimulatedSms:
    """The service, with what it accepted and what it was asked."""

    def __init__(self) -> None:
        self.behaviour = Behaviour.DELIVERS
        self.refusals: dict[str, int] = {}
        self.sent: list[SentMessage] = []
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def refuse(self, number: str, code: int) -> None:
        """Refuse every message to `number` with the provider's error `code`."""
        self.refusals[number] = code

    def last_code(self) -> str:
        """The code in the last message delivered, as somebody reads it off their phone."""
        found = re.search(rf"\b\d{{{CODE_LENGTH}}}\b", self.sent[-1].body)
        assert found is not None, "the message carries no code"
        return found.group()

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        expected = "Basic " + base64.b64encode(f"{SMS_ACCOUNT}:{SMS_TOKEN}".encode()).decode()
        if request.headers.get("Authorization") != expected:
            return _error(401, 20003)
        if request.method != "POST" or request.url.path != _MESSAGES_PATH:
            return _error(404, 20404)
        match self.behaviour:
            case Behaviour.THROTTLES:
                return _error(429, TOO_MANY_REQUESTS)
            case Behaviour.DOWN:
                return _error(503, 20500)
            case Behaviour.SILENT:
                raise httpx.ReadTimeout("the service did not answer", request=request)
            case Behaviour.UNREACHABLE:
                raise httpx.ConnectError("the service could not be reached", request=request)
            case Behaviour.DELIVERS:
                pass
        fields = dict(parse_qsl(request.content.decode(), keep_blank_values=True))
        refusal = self.refusals.get(fields.get("To", ""))
        if refusal is not None:
            return _error(400, refusal)
        self.sent.append(SentMessage(to=fields["To"], sender=fields["From"], body=fields["Body"]))
        return httpx.Response(
            201, json={"sid": f"SMsim-{len(self.sent)}", "status": "queued", "to": fields["To"]}
        )


def _error(status: int, code: int) -> httpx.Response:
    return httpx.Response(status, json={"code": code, "status": status})
