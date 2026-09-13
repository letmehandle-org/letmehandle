"""Sign-in codes delivered as a text message, through the telephony provider's Messaging API.

The application generates the code, stores only its hash, and hands it here to be sent (D-036).
This provider makes one request per code and keeps nothing: not the code, not the number, not the
message. Its logs say that a code was sent or refused and why, never to whom or what it was.

Every failure becomes a `ProviderError` saying whether another attempt could help, and a number the
provider will not deliver to — not a number, not one that receives texts, one whose owner has
opted out — is an `UnreachableNumberError`, because that is the one failure the person signing in
can fix. A refusal about the account itself, such as a destination the account is not enabled for,
is not blamed on the number.
"""

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

# The provider's codes for a number it will not deliver to: not a valid number, not a number at
# all, one whose owner replied to opt out, one no carrier routes a text to, and not a mobile.
UNDELIVERABLE_CODES: Final = frozenset({21211, 21217, 21610, 21612, 21614})

# What the message says, per locale (D-017). A number signing in has no account and so no locale
# yet, which is why the default is the one read today; a second language is a translation here.
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
