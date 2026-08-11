"""Call pacing: never burn an account, never call the same person two days running.

Two rules:
  1. At most MAX_CONTACTS_PER_ACCOUNT_PER_DAY contacts per company per day.
  2. A contact rests for CALL_COOLDOWN_DAYS after any logged call, voicemail
     included. One day of cooldown means a contact called yesterday is not
     dialable today, but one called two days ago is.
"""
from datetime import datetime, timedelta, timezone

import config
from services.hubspot import HubSpotClient


def _day(offset):
    return (datetime.now(timezone.utc) + timedelta(days=offset)).strftime("%Y-%m-%d")


def _cutoff():
    """The same cutoff /generate computes."""
    return (
        datetime.now(timezone.utc) - timedelta(days=config.CALL_COOLDOWN_DAYS)
    ).strftime("%Y-%m-%d")


def _blocked(last_call_day):
    """Mirror of the Filter D comparison in /generate."""
    return bool(last_call_day) and last_call_day >= _cutoff()


# --- defaults ---------------------------------------------------------------

def test_defaults_match_the_stated_policy():
    assert config.MAX_CONTACTS_PER_ACCOUNT_PER_DAY == 2
    assert config.CALL_COOLDOWN_DAYS == 1


# --- rule 2: cooldown -------------------------------------------------------

def test_called_yesterday_is_not_dialable_today():
    assert _blocked(_day(-1)) is True


def test_called_today_is_not_dialable_again():
    assert _blocked(_day(0)) is True


def test_two_days_between_calls_is_allowed():
    assert _blocked(_day(-2)) is False


def test_older_calls_are_allowed():
    assert _blocked(_day(-30)) is False


def test_never_called_is_dialable():
    assert _blocked("") is False


# --- timestamp normalisation -----------------------------------------------
# HubSpot returns ISO strings on some endpoints and epoch millis on others.
# A raw compare across the two silently disables the cooldown.

def test_iso_timestamp_normalises_to_a_date():
    assert HubSpotClient._call_date("2026-08-09T14:23:00.000Z") == "2026-08-09"


def test_epoch_millis_normalises_to_a_date():
    ms = int(datetime(2026, 8, 9, tzinfo=timezone.utc).timestamp() * 1000)
    assert HubSpotClient._call_date(str(ms)) == "2026-08-09"


def test_unparseable_timestamps_do_not_block_a_contact():
    # A malformed string sorts above a real date, which would wrongly skip a
    # dialable contact. Unknown must mean dialable.
    for junk in ["", None, "garbage", "not-a-date-xyz", "2026-8-9"]:
        assert HubSpotClient._call_date(junk) == ""
        assert _blocked(HubSpotClient._call_date(junk)) is False


# --- rule 1: account cap ----------------------------------------------------

def _simulate_account_cap(companies):
    """Mirror of the Filter E counter in /generate. Returns accepted names."""
    from collections import defaultdict
    per_account = defaultdict(int)
    accepted = []
    for name, company in companies:
        key = (company or "").strip().lower() or f"contact:{name}"
        if per_account[key] >= config.MAX_CONTACTS_PER_ACCOUNT_PER_DAY:
            continue
        per_account[key] += 1
        accepted.append(name)
    return accepted


def test_third_contact_at_the_same_account_is_skipped():
    accepted = _simulate_account_cap([
        ("Ann", "Acme"), ("Bob", "Acme"), ("Cy", "Acme"), ("Dee", "Acme"),
    ])
    assert accepted == ["Ann", "Bob"]


def test_cap_is_per_account_not_global():
    accepted = _simulate_account_cap([
        ("Ann", "Acme"), ("Bob", "Acme"), ("Cy", "Acme"),
        ("Dee", "Zeta"), ("Eve", "Zeta"), ("Fay", "Zeta"),
    ])
    assert accepted == ["Ann", "Bob", "Dee", "Eve"]


def test_company_matching_ignores_case_and_padding():
    accepted = _simulate_account_cap([
        ("Ann", "Acme"), ("Bob", " acme "), ("Cy", "ACME"),
    ])
    assert accepted == ["Ann", "Bob"]


def test_contacts_with_no_company_are_not_pooled_together():
    # A blank company must not make every unattached contact one "account".
    accepted = _simulate_account_cap([
        ("Ann", ""), ("Bob", ""), ("Cy", ""), ("Dee", None),
    ])
    assert accepted == ["Ann", "Bob", "Cy", "Dee"]
