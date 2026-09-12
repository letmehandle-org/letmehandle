"""A provider that delivers nowhere.

So that the project can be run, developed against and tested without a paid account. Every
contributor can sign in on their own machine, and the test suite needs no credentials.

It refuses to exist in a production configuration. That guard is the whole reason this class is
safe to ship: the failure it prevents is silent and total — everybody can sign in as anybody,
and nothing about the running service looks wrong.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.ports.otp import OTPProvider
from letmehandle.observability.logging import get_logger

if TYPE_CHECKING:
    from letmehandle.domain.models.phone_number import PhoneNumber

logger = get_logger(__name__)

# The code every challenge accepts in development. Fixed and published on purpose: a developer
# has to be able to sign in without reading a log, and pretending this is a secret would invite
# somebody to treat it as one.
DEVELOPMENT_CODE: Final = "000000"


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

    async def send(self, number: PhoneNumber, code: str) -> None:
        self.sent.append((number, code))
        # The number is masked even here. A development log is still a log, and it is the one
        # people paste into issues.
        logger.info("otp_code_not_sent", provider=self.name, number=number.masked, code=code)
