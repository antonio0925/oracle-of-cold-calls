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
