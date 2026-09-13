"""A user's telephony region is read from the number they signed in with."""

from __future__ import annotations

import pytest

from letmehandle.domain.errors import InvariantError
from letmehandle.domain.models.phone_number import PhoneNumber
from letmehandle.domain.models.region import IN, REGIONS, US, region_named, region_of

# Shorter than any number in India's plan, so it can reach nobody; the disclosure audit refuses
# any longer one outside the ranges reserved for fiction, and there is no such range in India.
INDIAN_NUMBER = PhoneNumber.parse("+91555001")


def test_a_north_american_number_is_in_the_us_region() -> None:
    assert region_of(PhoneNumber.parse("+12025550143")) is US


def test_an_indian_number_is_in_the_india_region() -> None:
    assert region_of(INDIAN_NUMBER) is IN


def test_a_number_from_a_country_no_region_covers_has_no_region() -> None:
    assert region_of(PhoneNumber.parse("+447700900123")) is None


@pytest.mark.parametrize(("name", "region"), [("US", US), ("in", IN), (" In ", IN)])
def test_a_region_is_found_by_its_name_in_any_case(name: str, region: object) -> None:
    assert region_named(name) is region


def test_an_unknown_region_name_is_refused_listing_the_ones_there_are() -> None:
    with pytest.raises(InvariantError, match="US, IN"):
        region_named("GB")


def test_no_two_regions_share_a_name_or_a_calling_code() -> None:
    assert len({region.name for region in REGIONS}) == len(REGIONS)
    assert len({region.calling_code for region in REGIONS}) == len(REGIONS)
