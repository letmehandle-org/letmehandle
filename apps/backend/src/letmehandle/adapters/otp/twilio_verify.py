"""Sign-in codes the telephony provider's verification service makes, texts and checks.

For a country whose operators deliver application texts only from registered templates, a message
the application composes may never arrive, while the provider's verification service sends its own
registered one. That service chooses the code, so this provider owns the code for every number it
serves: it asks the service to text one, and later asks whether a code is right (D-042). The
application still decides whether a code may be sent, how many, how long it lasts and how many
guesses it takes; only the comparison happens here.

This provider keeps nothing: not the code, not the number. Its logs say that a code was sent,
refused or checked and why, never to whom or what it was.

A number the service will not deliver to is an `UnreachableNumberError`, because that is the one
failure the person signing in can fix. Every other failure is a `ProviderError` saying whether
another attempt could help, and a request the service may have acted on without answering is a
`DeliveryUncertainError`. A check that finds nothing to check — expired, already approved, or never
sent — is a wrong code, not a failure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final
from urllib.parse import quote

import httpx

from letmehandle.adapters.transport.twilio.rest import (
    PROVIDER,
    REQUEST_TIMEOUT_SECONDS,
    error_code,
    raise_for,
    send,
)
from letmehandle.domain.errors import (
    CapabilityNotSupportedError,
    ProviderError,
    UnreachableNumberError,
)
from letmehandle.domain.ports.otp import OTPProvider
from letmehandle.observability.logging import get_logger

if TYPE_CHECKING:
    from letmehandle.domain.models.phone_number import PhoneNumber

logger = get_logger(__name__)

# The verification service's public origin. Not configuration: there is one, and it is documented.
VERIFY_ORIGIN: Final = "https://verify.twilio.com"
VERIFY_VERSION: Final = "v2"

VERIFICATIONS_PATH: Final = "/Verifications"
CHECK_PATH: Final = "/VerificationCheck"

_BAD_REQUEST: Final = 400
_NOT_FOUND: Final = 404
_TOO_MANY_REQUESTS: Final = 429

# The service's codes for a number it will not deliver to: an invalid number, not a number at all,
# not a mobile, and a landline that cannot receive a text.
UNDELIVERABLE_CODES: Final = frozenset({60200, 21211, 21614, 60205})

# The verification has taken as many wrong codes as the service allows; it will never approve one.
MAX_CHECK_ATTEMPTS: Final = 60202

_APPROVED: Final = "approved"


class VerifyOTPProvider(OTPProvider):
    """Has the verification service text a number its own code, and asks whether a code is right."""

    def __init__(
        self,
        *,
        account_id: str,
        auth_token: str,
        service_id: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=f"{VERIFY_ORIGIN}/{VERIFY_VERSION}/Services/{quote(service_id, safe='')}",
            auth=(account_id, auth_token),
            timeout=REQUEST_TIMEOUT_SECONDS,
            transport=transport,
        )

    @property
    def name(self) -> str:
        return f"{PROVIDER}-verify"

    @property
    def is_safe_for_production(self) -> bool:
        return True

    def issues_its_own_codes(self, number: PhoneNumber) -> bool:
        return True

    async def send(self, number: PhoneNumber, code: str) -> None:
        raise CapabilityNotSupportedError(self.name, "sending a code it did not make")

    async def send_own_code(self, number: PhoneNumber) -> None:
        form = {"To": [number.value], "Channel": ["sms"]}
        response = await send(self._client, "POST", VERIFICATIONS_PATH, form)
        refusal = error_code(response)
        if response.status_code == _BAD_REQUEST and refusal in UNDELIVERABLE_CODES:
            logger.info("otp_code_undeliverable", provider=self.name, error_code=refusal)
            raise UnreachableNumberError(
                self.name, f"the number cannot be sent a code (error {refusal})"
            )
        raise_for(response)
        logger.info("otp_code_sent", provider=self.name)

    async def check(self, number: PhoneNumber, code: str) -> bool:
        form = {"To": [number.value], "Code": [code]}
        response = await send(self._client, "POST", CHECK_PATH, form)
        refusal = error_code(response)
        if response.status_code == _NOT_FOUND or (
            response.status_code == _TOO_MANY_REQUESTS and refusal == MAX_CHECK_ATTEMPTS
        ):
            # Nothing the service will approve: expired, used, never sent, or out of guesses.
            logger.info("otp_code_checked", provider=self.name, approved=False, error_code=refusal)
            return False
        raise_for(response)
        approved = _status(response) == _APPROVED
        logger.info("otp_code_checked", provider=self.name, approved=approved)
        return approved

    async def aclose(self) -> None:
        """Release the connection pool. Safe to call more than once."""
        await self._client.aclose()


def _status(response: httpx.Response) -> str:
    """The verification's status, or a `ProviderError`: an answer that says none is no answer."""
    try:
        body = response.json()
    except ValueError:
        raise ProviderError(PROVIDER, "the API answered with no JSON", retryable=True) from None
    status = body.get("status") if isinstance(body, dict) else None
    if not isinstance(status, str):
        raise ProviderError(PROVIDER, "the API described no verification", retryable=True)
    return status
