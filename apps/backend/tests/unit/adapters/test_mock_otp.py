"""The provider that delivers nowhere, and refuses to exist where it would matter."""

from __future__ import annotations

import pytest

from letmehandle.adapters.otp.mock import MockOTPProvider
from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.phone_number import PhoneNumber

NUMBER = PhoneNumber.parse("+12025550143")


async def test_it_records_the_code_instead_of_sending_it() -> None:
    provider = MockOTPProvider(is_production=False)
    await provider.send(NUMBER, "424242")
    assert provider.sent == [(NUMBER, "424242")]


def test_it_says_it_is_not_safe_for_production() -> None:
    assert not MockOTPProvider(is_production=False).is_safe_for_production
    assert MockOTPProvider(is_production=False).name == "mock"


def test_it_refuses_to_exist_in_production() -> None:
    # The failure this prevents is silent and total: everybody can sign in as anybody, and
    # nothing about the running service looks wrong.
    with pytest.raises(InvariantError, match="cannot run in production"):
        MockOTPProvider(is_production=True)
