"""US-only filter: country matching, location fallbacks, dirty-data handling."""
import pytest

from services.filters import is_us_company, is_us_person


US_CASES = [
    {"country": "United States"},
    {"country": "U.S."},
    {"country": "U.S.A."},
    {"country": "United  States"},
    {"countryCode": "US"},
    {"location": "San Francisco, CA"},
    {"location": "austin, tx"},
    {"location": "Austin, TX, US"},
    {"location": "New York, USA"},
    # Dirty state code in the country field, disambiguated by location
    {"countryCode": "CA", "location": "San Francisco, CA"},
]

NON_US_CASES = [
    # "US" must not match as a substring
    {"location": "Sydney, Australia"},
    {"location": "Moscow, Russia"},
    {"location": "Vienna, Austria"},
    # Trailing two-letter codes that are not US states
    {"location": "London, UK"},
    {"location": "Toronto, ON"},
    # Explicit non-US countries
    {"countryCode": "AU", "location": "Sydney"},
    {"country": "Canada"},
    {"country": "United Kingdom"},
    # ISO "CA" (Canada) with non-US or missing location
    {"countryCode": "CA", "location": "Toronto, ON"},
    {"countryCode": "CA"},
    {},
]


@pytest.mark.parametrize("data", US_CASES)
def test_us_cases(data):
    assert is_us_company(data) is True
    assert is_us_person(data) is True


@pytest.mark.parametrize("data", NON_US_CASES)
def test_non_us_cases(data):
    assert is_us_company(data) is False
    assert is_us_person(data) is False


def test_person_uses_location_text_field():
    assert is_us_person({"locationText": "Denver, CO"}) is True
    assert is_us_person({"locationText": "Berlin, Germany"}) is False


# ---------------------------------------------------------------------------
# The two gates the BDR asked for
# ---------------------------------------------------------------------------
"""Both come from her feedback on a real call list.

  "Alex Potts is no longer at clickup, but still made it in the cold call
   plan. He's marked at yes to 'no longer at company'"
  "we need to remove international prospects from the list"

The property existed on the record. Nothing read it. The US helpers existed in
this module. Nothing called them.
"""
from services.filters import (
    has_left_the_company, international_reason, phone_is_dialable_in_us,
    location_is_non_us, phone_is_non_us,
)


def test_the_hubspot_checkbox_is_read():
    # HubSpot returns booleancheckbox values as strings.
    assert has_left_the_company({"no_longer_with_company": "true"}) is True
    assert has_left_the_company({"no_longer_with_company": True}) is True
    assert has_left_the_company({"no_longer_with_company": "false"}) is False
    assert has_left_the_company({"no_longer_with_company": None}) is False
    assert has_left_the_company({}) is False


def test_us_numbers_are_dialable():
    for number in ["+1 415 555 0100", "+14155550100", "(415) 555-0100",
                   "415-555-0100", "14155550100"]:
        assert phone_is_dialable_in_us(number) is True, number


def test_foreign_numbers_are_not_dialable():
    for number in ["+393248265806", "+972524742485", "+919949870982",
                   "+4523922207", "+358505027973", "0044 20 7946 0000"]:
        assert phone_is_dialable_in_us(number) is False, number


def test_a_us_location_with_a_foreign_phone_is_international():
    # The real case: her list held people whose location read New York and
    # whose number was +972. She could not dial them.
    props = {"state": "New York, New York, United States", "phone": "+972524742485"}
    assert international_reason(props)


def test_a_mobile_that_is_dialable_keeps_the_contact():
    props = {"state": "New York, New York, United States",
             "phone": "+972524742485", "mobilephone": "+1 415 555 0100"}
    assert international_reason(props) == ""


def test_a_foreign_location_is_international_even_without_a_phone():
    assert international_reason({"state": "London, England, United Kingdom"})
    assert international_reason({"country": "Germany"})


def test_indianapolis_is_not_india():
    # A substring scan flagged three Indianapolis contacts as Indian.
    for location in ["Indianapolis, Indiana, United States", "Greater Indianapolis"]:
        assert location_is_non_us({"state": location}) is False, location


def test_a_us_marker_beats_a_country_word():
    assert location_is_non_us({"state": "Austin, Texas, United States"}) is False
    assert location_is_non_us({"state": "San Francisco Bay Area"}) is False


def test_a_contact_with_no_phone_and_no_location_is_kept():
    # Absent data is not evidence of being abroad. Dropping on silence would
    # empty the list.
    assert international_reason({}) == ""
    assert phone_is_non_us({}) is False


def test_the_reason_names_the_signal():
    # The skip reason is shown to the BDR, so it has to say which one fired.
    props = {"phone": "+393248265806"}
    assert "+393248265806" in international_reason(props)
    assert "United Kingdom" in international_reason({"state": "London, United Kingdom"})
