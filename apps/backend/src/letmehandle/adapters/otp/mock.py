"""A provider that delivers nowhere and refuses to start in a production configuration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.ports.otp import OTPProvider
from letmehandle.observability.logging import get_logger

if TYPE_CHECKING:
    from letmehandle.domain.models.phone_number import PhoneNumber

logger = get_logger(__name__)

# The code every challenge accepts while this mock is configured.
DEVELOPMENT_CODE: Final = "123456"


class MockOTPProvider(OTPProvider):
    """Records the code instead of sending it."""

    def __init__(self, *, is_production: bool) -> None:
        if is_production:
            raise InvariantError(
                "the mock one-time-password provider cannot run in production: it delivers "
                "nothing and would let anybody sign in as anybody. Configure a real provider."
            )
        self.sent: list[tuple[PhoneNumber, str]] = []

    @property
    def name(self) -> str:
        return "mock"

    @property
    def is_safe_for_production(self) -> bool:
        return False

    @property
    def fixed_code(self) -> str:
        return DEVELOPMENT_CODE

    async def send(self, number: PhoneNumber, code: str) -> None:
        self.sent.append((number, code))
        # Logged as a masked number, which the scrubber leaves in place.
        logger.info("otp_code_not_sent", provider=self.name, masked_number=number.masked, code=code)
