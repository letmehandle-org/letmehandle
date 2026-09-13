"""A user is told the forwarding number of their own region (D-041)."""

from __future__ import annotations

from letmehandle.domain.models.forwarding import CallForwarding, ForwardingNumbers
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.region import IN, US

US_LINE = PhoneNumber.parse("+12025550100")
IN_LINE = PhoneNumber.parse("+91555010")
ELSEWHERE_LINE = PhoneNumber.parse("+12025550199")
US_USER = PhoneNumber.parse("+12025550143")
# Shorter than any number in India's plan, so it can reach nobody.
IN_USER = PhoneNumber.parse("+91555001")
UK_USER = PhoneNumber.parse("+447700900123")


def test_each_user_is_told_the_number_of_their_own_region() -> None:
    numbers = ForwardingNumbers(by_region={US: US_LINE, IN: IN_LINE})
    assert numbers.for_user(US_USER) == CallForwarding(US_LINE)
    assert numbers.for_user(IN_USER) == CallForwarding(IN_LINE)


def test_a_user_whose_region_has_no_number_is_told_none() -> None:
    numbers = ForwardingNumbers(by_region={US: US_LINE})
    assert numbers.for_user(IN_USER) is None
    assert numbers.for_user(UK_USER) is None


def test_a_number_for_everyone_else_covers_regions_without_one_and_countries_without_a_region() -> (
    None
):
    numbers = ForwardingNumbers(by_region={IN: IN_LINE}, elsewhere=ELSEWHERE_LINE)
    assert numbers.for_user(IN_USER) == CallForwarding(IN_LINE)
    assert numbers.for_user(US_USER) == CallForwarding(ELSEWHERE_LINE)
    assert numbers.for_user(UK_USER) == CallForwarding(ELSEWHERE_LINE)


def test_a_deployment_that_forwards_nothing_tells_nobody_a_number() -> None:
    assert ForwardingNumbers().for_user(US_USER) is None
