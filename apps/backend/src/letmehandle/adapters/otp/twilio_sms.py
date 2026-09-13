"""Sign-in codes sent as a text message through the Messaging API, keeping nothing (D-037)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from letmehandle.adapters.transport.twilio.rest import (
    PROVIDER,
    account_client,
    error_code,
    raise_for,
    send,
)
from letmehandle.application.preferences.context import DEFAULT_LOCALE, closest_phrasebook
from letmehandle.domain.errors import UnreachableNumberError
from letmehandle.domain.models.auth import CHALLENGE_LIFETIME
from letmehandle.domain.ports.otp import OTPProvider
from letmehandle.observability.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Mapping

    import httpx

    from letmehandle.domain.models.phone_number import PhoneNumber

logger = get_logger(__name__)

MESSAGES_PATH: Final = "/Messages.json"

_BAD_REQUEST: Final = 400

# Provider error codes for a number that cannot receive the text.
UNDELIVERABLE_CODES: Final = frozenset({21211, 21217, 21610, 21612, 21614})

# The message text per locale (D-017); a number signing in gets the default.
SIGN_IN_MESSAGES: Final[Mapping[str, str]] = {
    DEFAULT_LOCALE: "Your LetMeHandle sign-in code is {code}. It expires in {minutes} minutes.",
}


class SmsOTPProvider(OTPProvider):
    """Sends each code as one text message from the account's number."""

    def __init__(
        self,
        *,
        account_id: str,
        auth_token: str,
        sender: PhoneNumber,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._sender = sender
        self._client = account_client(
            account_id=account_id, auth_token=auth_token, transport=transport
        )

    @property
    def name(self) -> str:
        return f"{PROVIDER}-sms"

    @property
    def is_safe_for_production(self) -> bool:
        return True

    async def send(self, number: PhoneNumber, code: str) -> None:
        body = closest_phrasebook(DEFAULT_LOCALE, SIGN_IN_MESSAGES).format(
            code=code, minutes=int(CHALLENGE_LIFETIME.total_seconds() // 60)
        )
        form = {"To": [number.value], "From": [self._sender.value], "Body": [body]}
        response = await send(self._client, "POST", MESSAGES_PATH, form)
        refusal = error_code(response)
        if response.status_code == _BAD_REQUEST and refusal in UNDELIVERABLE_CODES:
            logger.info("otp_code_undeliverable", provider=self.name, error_code=refusal)
            raise UnreachableNumberError(
                self.name, f"the number cannot be sent a text message (error {refusal})"
            )
        raise_for(response)
        logger.info("otp_code_sent", provider=self.name)

    async def aclose(self) -> None:
        """Release the connection pool. Safe to call more than once."""
        await self._client.aclose()
