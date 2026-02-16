"""
US-only filtering for The Forge pipeline.
"""
import re


US_COUNTRY_ALIASES = {"US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"}


def is_us_company(company_data):
    """Check if a company is based in the US."""
    country = (company_data.get("country") or company_data.get("countryCode") or "").strip().upper()
    if country in US_COUNTRY_ALIASES:
        return True
    # Check location text fallback
    location = (company_data.get("location") or company_data.get("locationText") or "").upper()
    if any(alias in location for alias in US_COUNTRY_ALIASES):
        return True
    # Check city/state patterns (e.g. "San Francisco, CA")
    if re.search(r',\s*[A-Z]{2}\s*$', company_data.get("location", "")):
        return True
    return False


def is_us_person(person_data):
    """Check if a person is based in the US."""
    country = (person_data.get("countryCode") or person_data.get("country") or "").strip().upper()
    if country in US_COUNTRY_ALIASES:
        return True
    location = (person_data.get("locationText") or person_data.get("location") or "").upper()
    if any(alias in location for alias in US_COUNTRY_ALIASES):
        return True
    if re.search(r',\s*[A-Z]{2}\s*$', person_data.get("locationText", "")):
        return True
    return False
