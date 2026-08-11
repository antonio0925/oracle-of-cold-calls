"""The start time of a call plan.

The BDR picks the day and the time they make the calls. The time stamps the
plan: it appears on the Route Plan, on every HubSpot prep note, on the Slack
call sheet, and on Today's Climb. It does not change who is on the list or the
order of the time blocks.

Before this, only the day existed. A plan built on Monday evening for Tuesday
at 9am carried no trace of the 9am anywhere the BDR could read it.
"""
import json
import os
from unittest.mock import patch

import pytest

import app as app_module
from services import sessions
from services.formatting import (
    clean_clock, format_clock, format_plan_stamp, format_note_html,
)
from services.slack import build_slack_messages


# --- the value itself -------------------------------------------------------

def test_a_browser_time_is_kept():
    assert clean_clock("09:00") == "09:00"
    assert clean_clock("9:00") == "09:00"
    assert clean_clock("17:30") == "17:30"
    assert clean_clock("08:15:00") == "08:15"


def test_junk_is_dropped_rather_than_stored():
    # This value is written onto HubSpot contact records, so it is never
    # passed through unchecked.
    for raw in [None, "", "  ", "noon", "25:00", "12:60", "9", "<b>9:00</b>"]:
        assert clean_clock(raw) == "", raw


def test_a_time_reads_as_a_clock_not_as_24_hour():
    assert format_clock("09:00") == "9:00 AM"
    assert format_clock("00:30") == "12:30 AM"
    assert format_clock("12:00") == "12:00 PM"
    assert format_clock("17:30") == "5:30 PM"


def test_the_stamp_names_the_day_and_the_time():
    assert format_plan_stamp("2026-08-12", "09:00", "PT") == "Wednesday Aug 12, from 9:00 AM PT"


def test_a_plan_with_no_time_still_reads_correctly():
    # Sessions built before the time existed must not render "None" at anyone.
    assert format_plan_stamp("2026-08-12", "", "PT") == "Wednesday Aug 12"
    assert format_plan_stamp("", "", "PT") == ""


# --- where the BDR reads it -------------------------------------------------

_SCRIPT = "### VOICEMAIL SCRIPT\nHello Ann.\n"


def test_the_prep_note_carries_the_call_day_and_time():
    html = format_note_html(
        {"firstname": "Ann", "lastname": "Diaz", "company": "Acme"},
        _SCRIPT, "2026-08-12", "09:00", "PT",
    )
    assert "Call day: Wednesday Aug 12, from 9:00 AM PT" in html


def test_a_note_without_a_plan_has_no_empty_call_day_line():
    html = format_note_html(
        {"firstname": "Ann", "lastname": "Diaz", "company": "Acme"}, _SCRIPT)
    assert "Call day:" not in html


def test_the_slack_sheet_header_carries_the_time():
    header, _ = build_slack_messages({
        "calling_date": "2026-08-12", "calling_time": "09:00",
        "stats": {"prepped": 3}, "call_sheet": [], "unknown_tz": [], "contacts": [],
    })
    assert "Wednesday Aug 12, from 9:00 AM" in header


def test_the_slack_sheet_still_works_for_a_plan_with_no_time():
    header, _ = build_slack_messages({
        "calling_date": "2026-08-12",
        "stats": {"prepped": 3}, "call_sheet": [], "unknown_tz": [], "contacts": [],
    })
    assert "Wednesday Aug 12" in header
    assert "from" not in header.split("\n")[0]


# --- the round trip through a real request ----------------------------------

@pytest.fixture(autouse=True)
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("sessions", exist_ok=True)
    sessions._sessions.clear()


def _client():
    c = app_module.app.test_client()
    with c.session_transaction() as sess:
        sess["summit_auth"] = True
    return c


class _FakeHubSpot:
    def search_lists(self, name):
        return "list-1"

    def get_list_memberships(self, list_id):
        return ["1", "2"]

    def batch_get_contacts(self, ids, properties):
        return [{
            "id": str(i),
            "properties": {"firstname": "Ann", "lastname": f"D{i}",
                           "company": f"Co{i}", "state": "CA",
                           "country": "United States"},
        } for i in ids]

    def get_all_prep_notes_for_contact(self, cid):
        return [{"id": "n", "body": "COLD CALL PREP", "created_at": ""}]


def _build(payload):
    with patch.object(app_module, "HubSpotClient", lambda token: _FakeHubSpot()), \
            patch.object(app_module.config, "HUBSPOT_ACCESS_TOKEN", "tok"):
        body = _client().post("/quick-generate", json=payload).get_data(as_text=True)
    events = [json.loads(c.strip()[6:]) for c in body.split("\n\n")
              if c.strip().startswith("data: ")]
    return events[-1]


def test_the_time_survives_the_build_and_reaches_todays_climb():
    complete = _build({"segment": "My List", "target": 50,
                       "calling_date": "2026-08-12", "calling_time": "09:00"})
    session_id = complete["session_id"]

    stored = sessions.load_session_from_disk(session_id)
    assert stored["calling_time"] == "09:00"

    climb = _client().get(f"/api/climb?session_id={session_id}").get_json()
    assert climb["calling_time"] == "09:00"
    assert climb["plan_stamp"].startswith("Wednesday Aug 12, from 9:00 AM")


def test_a_junk_time_does_not_reach_the_stored_plan():
    complete = _build({"segment": "My List", "target": 50,
                       "calling_date": "2026-08-12", "calling_time": "half nine"})
    stored = sessions.load_session_from_disk(complete["session_id"])
    assert stored["calling_time"] == ""
