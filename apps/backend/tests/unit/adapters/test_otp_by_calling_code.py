"""Each sign-in code goes through the provider for its number's country, or the default (D-041)."""

from __future__ import annotations

from letmehandle.adapters.otp.by_calling_code import OTPProviderByCallingCode
from letmehandle.adapters.otp.mock import MockOTPProvider
from letmehandle.domain.models.phone_number import PhoneNumber
from tests.contracts.fakes import CheckingOTPProvider, RecordingOTPProvider

US_NUMBER = PhoneNumber.parse("+12025550143")
# Shorter than any number in India's plan, so it can reach nobody.
IN_NUMBER = PhoneNumber.parse("+91555001")
UK_NUMBER = PhoneNumber.parse("+447700900123")


class RealProvider(RecordingOTPProvider):
    """A provider that delivers to real handsets, and holds a connection to close."""

    def __init__(self) -> None:
        super().__init__()
        self.closed = 0

    @property
    def is_safe_for_production(self) -> bool:
        return True

    async def aclose(self) -> None:
        self.closed += 1


async def test_a_number_is_sent_its_code_by_its_countrys_provider_and_others_by_the_default() -> (
    None
):
    default, india = RecordingOTPProvider(), RealProvider()
    provider = OTPProviderByCallingCode(default=default, by_calling_code={"91": india})

    await provider.send(IN_NUMBER, "111111")
    await provider.send(US_NUMBER, "222222")
    await provider.send(UK_NUMBER, "333333")

    assert india.sent == [(IN_NUMBER, "111111")]
    assert default.sent == [(US_NUMBER, "222222"), (UK_NUMBER, "333333")]
    assert provider.name == "recording+91:recording"


async def test_a_country_whose_provider_makes_its_codes_is_sent_and_checked_by_it() -> None:
    default, india = RecordingOTPProvider(), CheckingOTPProvider()
    provider = OTPProviderByCallingCode(default=default, by_calling_code={"91": india})

    assert provider.issues_its_own_codes(IN_NUMBER)
    assert not provider.issues_its_own_codes(US_NUMBER)

    await provider.send_own_code(IN_NUMBER)
    assert await provider.check(IN_NUMBER, "000000") is False
    assert await provider.check(IN_NUMBER, india.code) is True
    assert india.issued == [IN_NUMBER]
    assert india.checked == [(IN_NUMBER, "000000"), (IN_NUMBER, india.code)]
    assert default.sent == []


async def test_a_number_is_never_checked_by_a_provider_other_than_its_own() -> None:
    # A code the Indian provider made is not one the default can be asked about, and the reverse.
    india, default = CheckingOTPProvider(code="111111"), CheckingOTPProvider(code="222222")
    provider = OTPProviderByCallingCode(default=default, by_calling_code={"91": india})
    await provider.send_own_code(IN_NUMBER)
    await provider.send_own_code(US_NUMBER)

    assert await provider.check(US_NUMBER, "111111") is False
    assert await provider.check(IN_NUMBER, "222222") is False
    assert default.checked == [(US_NUMBER, "111111")]
    assert india.checked == [(IN_NUMBER, "222222")]


def test_it_is_safe_for_production_only_when_every_provider_is() -> None:
    real = RealProvider()
    assert OTPProviderByCallingCode(
        default=real, by_calling_code={"91": RealProvider()}
    ).is_safe_for_production
    assert not OTPProviderByCallingCode(
        default=real, by_calling_code={"91": RecordingOTPProvider()}
    ).is_safe_for_production


def test_a_testing_code_is_never_fixed_for_numbers_a_real_provider_texts() -> None:
    # The code is chosen before the provider is, so a fixed one would reach real handsets too.
    mixed = OTPProviderByCallingCode(
        default=MockOTPProvider(is_production=False), by_calling_code={"91": RealProvider()}
    )
    testing = OTPProviderByCallingCode(
        default=MockOTPProvider(is_production=False),
        by_calling_code={"91": MockOTPProvider(is_production=False)},
    )
    assert mixed.fixed_code is None
    assert testing.fixed_code == MockOTPProvider(is_production=False).fixed_code


async def test_closing_it_closes_each_provider_once_however_many_countries_it_serves() -> None:
    shared = RealProvider()
    provider = OTPProviderByCallingCode(
        default=RecordingOTPProvider(), by_calling_code={"91": shared, "44": shared}
    )
    await provider.aclose()
    assert shared.closed == 1
