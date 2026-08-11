"""
US-only filtering for The Forge pipeline.
"""
import re


US_COUNTRY_ALIASES = {"US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"}

# 50 states + DC. The trailing ", XX" check must only accept these —
# a bare [A-Z]{2} also matches "London, UK" and Canadian provinces.
US_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC",
}


def _normalize_country(raw):
    # "U.S." -> "US", "U.S.A." -> "USA", collapse repeated whitespace.
    country = re.sub(r"\.", "", (raw or "")).strip().upper()
    return re.sub(r"\s+", " ", country)


def _location_mentions_us(location):
    # Word-boundary match: a bare substring test makes "US" match
    # AUSTRALIA, AUSTRIA, RUSSIA, BELARUS, and CYPRUS.
    return any(
        re.search(rf"\b{re.escape(alias)}\b", location)
        for alias in US_COUNTRY_ALIASES
    )


def _location_ends_with_us_state(location):
    m = re.search(r",\s*([A-Z]{2})\s*$", location)
    return bool(m) and m.group(1) in US_STATE_CODES


def is_us_company(company_data):
    """Check if a company is based in the US."""
    country = _normalize_country(company_data.get("country") or company_data.get("countryCode"))
    if country in US_COUNTRY_ALIASES:
        return True
    if country and country not in US_STATE_CODES:
        # Unambiguously non-US country — do not fall through to location guessing.
        return False
    # Empty, or a two-letter value that may be a dirty US state code in the
    # country field ("CA" = California or Canada) — let the location decide.
    # Check location text fallback
    location = (company_data.get("location") or company_data.get("locationText") or "").upper()
    if _location_mentions_us(location):
        return True
    # Check city/state patterns (e.g. "San Francisco, CA")
    if _location_ends_with_us_state(location):
        return True
    return False


def is_us_person(person_data):
    """Check if a person is based in the US."""
    country = _normalize_country(person_data.get("countryCode") or person_data.get("country"))
    if country in US_COUNTRY_ALIASES:
        return True
    if country and country not in US_STATE_CODES:
        # Unambiguously non-US country — do not fall through to location guessing.
        return False
    # Empty, or a two-letter value that may be a dirty US state code in the
    # country field ("CA" = California or Canada) — let the location decide.
    location = (person_data.get("locationText") or person_data.get("location") or "").upper()
    if _location_mentions_us(location):
        return True
    if _location_ends_with_us_state(location):
        return True
    return False
