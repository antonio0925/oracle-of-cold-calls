"""
Who is not worth a dial today.

Two gates run against the HubSpot contact record before a script is written:

  no longer with company   the person left, so the call is wasted and the
                           script would be about the wrong employer
  international            the number cannot be dialled as a US call, so the
                           BDR reaches them by email or LinkedIn instead

The US company and person helpers below take Clay and Octave shaped records
and are kept for callers outside this app.
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


# ---------------------------------------------------------------------------
# HubSpot contact gates
# ---------------------------------------------------------------------------

# HubSpot returns booleancheckbox values as the strings "true" and "false".
_TRUTHY = {"true", "yes", "1"}


def has_left_the_company(props):
    """True when the contact is flagged `no_longer_with_company` in HubSpot.

    Reported by the BDR: a VP who had left ClickUp was still on the call list
    with a script about ClickUp. The property is set on the record; nothing
    read it.
    """
    raw = props.get("no_longer_with_company")
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in _TRUTHY


# Country names that place a contact outside the US. "Georgia" is deliberately
# absent: it is a US state far more often than it is the country in this data,
# and the US markers are checked first anyway.
NON_US_COUNTRIES = {
    "AFGHANISTAN", "ALBANIA", "ALGERIA", "ANDORRA", "ANGOLA", "ARGENTINA",
    "ARMENIA", "AUSTRALIA", "AUSTRIA", "AZERBAIJAN", "BAHRAIN", "BANGLADESH",
    "BELARUS", "BELGIUM", "BOLIVIA", "BOSNIA", "BOTSWANA", "BRAZIL",
    "BULGARIA", "CAMBODIA", "CAMEROON", "CANADA", "CHILE", "CHINA",
    "COLOMBIA", "COSTA RICA", "CROATIA", "CYPRUS", "CZECHIA",
    "CZECH REPUBLIC", "DENMARK", "DOMINICAN REPUBLIC", "ECUADOR", "EGYPT",
    "EL SALVADOR", "ESTONIA", "ETHIOPIA", "FINLAND", "FRANCE", "GERMANY",
    "GHANA", "GREECE", "GUATEMALA", "HONDURAS", "HONG KONG", "HUNGARY",
    "ICELAND", "INDIA", "INDONESIA", "IRAN", "IRAQ", "IRELAND", "ISRAEL",
    "ITALY", "JAPAN", "JORDAN", "KAZAKHSTAN", "KENYA", "KUWAIT", "LATVIA",
    "LEBANON", "LITHUANIA", "LUXEMBOURG", "MALAYSIA", "MALTA", "MEXICO",
    "MOLDOVA", "MOROCCO", "NEPAL", "NETHERLANDS", "NEW ZEALAND", "NIGERIA",
    "NORWAY", "PAKISTAN", "PANAMA", "PARAGUAY", "PERU", "PHILIPPINES",
    "POLAND", "PORTUGAL", "QATAR", "ROMANIA", "RUSSIA", "SAUDI ARABIA",
    "SERBIA", "SINGAPORE", "SLOVAKIA", "SLOVENIA", "SOUTH AFRICA",
    "SOUTH KOREA", "SPAIN", "SRI LANKA", "SWEDEN", "SWITZERLAND", "TAIWAN",
    "TANZANIA", "THAILAND", "TUNISIA", "TURKEY", "TURKIYE", "UGANDA",
    "UKRAINE", "UNITED ARAB EMIRATES", "UNITED KINGDOM", "URUGUAY",
    "UZBEKISTAN", "VENEZUELA", "VIETNAM", "ZIMBABWE",
    "UK", "UAE", "ENGLAND", "SCOTLAND", "WALES", "NORTHERN IRELAND",
}

# The North American Numbering Plan. +1 covers the US and Canada, and a +1
# number is dialable from a US desk, so it is not treated as international on
# the phone signal alone. A Canadian location still trips the location signal.
_NANP_PREFIX = re.compile(r'^\+?1[\s.\-(]*\d{3}')
# A bare US number: 10 digits, or 11 starting with 1.
_BARE_US = re.compile(r'^(?:1)?\d{10}$')


def _digits(value):
    return re.sub(r'\D', '', value or "")


def phone_is_dialable_in_us(raw_phone):
    """True when this number can be dialled as a normal US call."""
    phone = (raw_phone or "").strip()
    if not phone:
        return False
    # 00 is the international prefix used outside North America.
    if phone.startswith("00"):
        return False
    if phone.startswith("+"):
        return bool(_NANP_PREFIX.match(phone))
    return bool(_BARE_US.match(_digits(phone)))


def _location_text(props):
    parts = [props.get("state"), props.get("country"), props.get("city")]
    return " ".join(p for p in parts if p).upper()


def location_is_non_us(props):
    """True when the contact's location text names a country outside the US.

    US markers win. The location field in this portal holds LinkedIn style
    strings such as "New York, New York, United States" and "San Francisco Bay
    Area", so a US mention is checked before any country name is looked for.
    """
    location = _location_text(props)
    if not location:
        return False
    if _location_mentions_us(location) or _location_ends_with_us_state(location):
        return False
    return any(
        re.search(rf"\b{re.escape(country)}\b", location)
        for country in NON_US_COUNTRIES
    )


def phone_is_non_us(props):
    """True when the contact has numbers and none of them is US dialable."""
    numbers = [props.get("phone"), props.get("mobilephone")]
    present = [n for n in numbers if (n or "").strip()]
    if not present:
        return False
    return not any(phone_is_dialable_in_us(n) for n in present)


def international_reason(props):
    """Why this contact cannot be cold called from a US desk, or "".

    Either signal is enough. On the BDR's own list the two never agreed: the
    phone flagged seven people and the location text flagged three, with no
    overlap. Contacts whose location reads "New York" while their number is
    +972 are exactly the ones she could not dial.
    """
    if phone_is_non_us(props):
        number = (props.get("phone") or props.get("mobilephone") or "").strip()
        return f"Phone {number} is not a US number"
    if location_is_non_us(props):
        return f"Based outside the US ({(props.get('state') or props.get('country') or '').strip()})"
    return ""
