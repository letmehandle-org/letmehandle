"""The sign-in code contract, run against every provider the application can be configured with."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from letmehandle.adapters.otp.by_calling_code import OTPProviderByCallingCode
from letmehandle.adapters.otp.mock import MockOTPProvider
from letmehandle.adapters.otp.twilio_sms import SmsOTPProvider
from letmehandle.adapters.otp.twilio_verify import VerifyOTPProvider
from tests.contracts.fakes import CheckingOTPProvider
from tests.contracts.other_ports import OTPProviderContract
from tests.support.simulated_sms import SMS_ACCOUNT, SMS_SENDER, SMS_TOKEN, SimulatedSms
from tests.support.simulated_verify import VERIFY_SERVICE, SimulatedVerify

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class TestMockOTPProvider(OTPProviderContract):
    @pytest.fixture
    def otp(self) -> MockOTPProvider:
        return MockOTPProvider(is_production=False)


class TestSmsOTPProvider(OTPProviderContract):
    """The text-message provider, talking to a simulated message API."""

    @pytest.fixture
    async def otp(self) -> AsyncIterator[SmsOTPProvider]:
        provider = SmsOTPProvider(
            account_id=SMS_ACCOUNT,
            auth_token=SMS_TOKEN,
            sender=SMS_SENDER,
            transport=SimulatedSms().transport,
        )
        yield provider
        await provider.aclose()


class TestVerifyOTPProvider(OTPProviderContract):
    """The provider whose verification service makes its codes, talking to a simulated service."""

    @pytest.fixture
    async def otp(self) -> AsyncIterator[VerifyOTPProvider]:
        provider = VerifyOTPProvider(
            account_id=SMS_ACCOUNT,
            auth_token=SMS_TOKEN,
            service_id=VERIFY_SERVICE,
            transport=SimulatedVerify().transport,
        )
        yield provider
        await provider.aclose()


class TestOTPProviderByCallingCode(OTPProviderContract):
    """The provider that chooses, over the text-message provider for one country and the mock."""

    @pytest.fixture
    async def otp(self) -> AsyncIterator[OTPProviderByCallingCode]:
        texting = SmsOTPProvider(
            account_id=SMS_ACCOUNT,
            auth_token=SMS_TOKEN,
            sender=SMS_SENDER,
            transport=SimulatedSms().transport,
        )
        provider = OTPProviderByCallingCode(
            default=MockOTPProvider(is_production=False), by_calling_code={"1": texting}
        )
        yield provider
        await provider.aclose()


class TestOTPProviderByCallingCodeWithCodesItsProviderMakes(OTPProviderContract):
    """The provider that chooses, where the number's provider makes and checks its own codes."""

    @pytest.fixture
    def otp(self) -> OTPProviderByCallingCode:
        return OTPProviderByCallingCode(
            default=MockOTPProvider(is_production=False),
            by_calling_code={"1": CheckingOTPProvider()},
        )
