"""A route build must always reach a terminal event.

The BDR reported that the screen never showed the call sheet, while the log
showed the work finishing. The Railway log gave the cause:

    File "/app/app.py", line 850, in stream
      if resolved == 0 and contact_ids:
    NameError: name 'resolved' is not defined

`resolved` is a counter in /generate. The same block was copied into
/quick-generate, where the counter does not exist, so every quick build that
found at least one prep note raised after the last contact and before the
`complete` event. A generator that raises just closes the response body, and
the browser reads that as a clean end of stream, so the page sat on the
progress bar and reported nothing.

Two rules are tested here:
  1. Every stream ends with a terminal event: complete, error, or done.
  2. A stream that raises anyway sends `error` instead of closing silently.
"""
import json
import os
from unittest.mock import patch

import pytest

import app as app_module
from services import sessions

TERMINAL = {"complete", "error", "done"}


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


def _events(response):
    """Parse an SSE body into a list of message dicts."""
    out = []
    for chunk in response.get_data(as_text=True).split("\n\n"):
        chunk = chunk.strip()
        if chunk.startswith("data: "):
            out.append(json.loads(chunk[6:]))
    return out


def _contact(cid):
    return {
        "id": str(cid),
        "properties": {
            "firstname": "Ann", "lastname": f"Diaz{cid}",
            "company": f"Company {cid}", "jobtitle": "VP Sales",
            "state": "CA", "country": "United States",
        },
    }


class _FakeHubSpot:
    """Enough HubSpot for a quick build to run end to end."""

    def __init__(self, contact_count):
        self.contact_count = contact_count

    def search_lists(self, name):
        return "list-1"

    def get_list_memberships(self, list_id):
        return [str(i) for i in range(self.contact_count)]

    def batch_get_contacts(self, ids, properties):
        return [_contact(i) for i in ids]

    def get_all_prep_notes_for_contact(self, cid):
        return [{"id": f"n{cid}", "body": "COLD CALL PREP", "created_at": ""}]


def _quick_build(contact_count, target):
    fake = _FakeHubSpot(contact_count)
    with patch.object(app_module, "HubSpotClient", lambda token: fake), \
            patch.object(app_module.config, "HUBSPOT_ACCESS_TOKEN", "tok"):
        resp = _client().post("/quick-generate", json={
            "segment": "My List", "target": target, "calling_date": "2026-08-12",
        })
        return _events(resp)


@pytest.mark.parametrize("contact_count", [1, 5, 11, 40])
def test_a_quick_build_always_reaches_a_terminal_event(contact_count):
    # The crash was after the loop, so it did not depend on the contact count.
    # The count is varied because the report was "more than 10 never renders".
    events = _quick_build(contact_count, target=50)
    kinds = [e["type"] for e in events]
    assert TERMINAL & set(kinds), f"stream ended with no terminal event: {kinds}"
    assert kinds[-1] == "complete"


def test_a_quick_build_hands_back_a_session_the_page_can_render():
    events = _quick_build(12, target=50)
    complete = events[-1]
    assert complete["stats"]["prepped"] == 12
    data = sessions.load_session_from_disk(complete["session_id"])
    # renderReview iterates data.call_sheet. Without it the page throws and
    # shows nothing, which is the same symptom by a different route.
    assert data["call_sheet"], "the session must carry a call sheet"


def test_a_list_holding_no_contacts_says_so_instead_of_crashing():
    # A company list resolves to zero contacts. That message must win over
    # "nobody has a prep note", because the two have different fixes.
    fake = _FakeHubSpot(3)
    fake.batch_get_contacts = lambda ids, properties: []
    with patch.object(app_module, "HubSpotClient", lambda token: fake), \
            patch.object(app_module.config, "HUBSPOT_ACCESS_TOKEN", "tok"):
        events = _events(_client().post("/quick-generate", json={
            "segment": "Company List", "target": 50, "calling_date": "2026-08-12",
        }))
    errors = [e["msg"] for e in events if e["type"] == "error"]
    assert errors, "an empty resolve must be reported"
    assert "none of them are contacts" in errors[0]


def test_a_crash_mid_stream_arrives_as_an_error_event():
    # The guard, tested directly: any escaping exception must still reach the
    # browser as an event it knows how to show.
    def exploding():
        yield "data: " + json.dumps({"type": "status", "msg": "working"}) + "\n\n"
        raise RuntimeError("HubSpot fell over")

    with app_module.app.test_request_context():
        body = app_module.sse_response(exploding, "/test").get_data(as_text=True)

    events = [json.loads(c.strip()[6:]) for c in body.split("\n\n") if c.strip().startswith("data: ")]
    assert events[-1]["type"] == "error"
    assert "HubSpot fell over" in events[-1]["msg"]


# ---------------------------------------------------------------------------
# The two gates, at the route level
# ---------------------------------------------------------------------------

class _GateHubSpot(_FakeHubSpot):
    """A list holding one dialable contact, one leaver, one foreign number."""

    ROWS = {
        "1": {"firstname": "Ann", "lastname": "Diaz", "company": "Acme",
              "state": "Austin, Texas, United States", "phone": "+1 512 555 0100"},
        "2": {"firstname": "Alex", "lastname": "Potts", "company": "clickup.com",
              "state": "Austin, Texas, United States", "phone": "+1 512 555 0101",
              "no_longer_with_company": "true"},
        "3": {"firstname": "Gal", "lastname": "Herman", "company": "monday.com",
              "state": "New York, New York, United States", "phone": "+972524742485"},
    }

    def __init__(self):
        super().__init__(len(self.ROWS))

    def get_list_memberships(self, list_id):
        return list(self.ROWS)

    def batch_get_contacts(self, ids, properties):
        return [{"id": i, "properties": dict(self.ROWS[i])} for i in ids]


def _gate_build():
    fake = _GateHubSpot()
    with patch.object(app_module, "HubSpotClient", lambda token: fake), \
            patch.object(app_module.config, "HUBSPOT_ACCESS_TOKEN", "tok"):
        resp = _client().post("/quick-generate", json={
            "segment": "My List", "target": 50, "calling_date": "2026-08-12",
        })
    return _events(resp)


def test_a_contact_who_left_the_company_never_reaches_the_call_list():
    events = _gate_build()
    skips = {e["name"]: e["reason"] for e in events if e["type"] == "skip"}
    assert "Alex Potts" in skips
    assert "No longer with" in skips["Alex Potts"]
    assert events[-1]["stats"]["skipped_left_company"] == 1


def test_a_contact_with_a_foreign_number_never_reaches_the_call_list():
    events = _gate_build()
    skips = {e["name"]: e["reason"] for e in events if e["type"] == "skip"}
    assert "Gal Herman" in skips
    assert "not a US number" in skips["Gal Herman"]
    assert events[-1]["stats"]["skipped_international"] == 1


def test_the_dialable_contact_survives_both_gates():
    events = _gate_build()
    complete = events[-1]
    assert complete["type"] == "complete"
    assert complete["stats"]["prepped"] == 1
    done = [e["name"] for e in events if e["type"] == "done_contact"]
    assert done == ["Ann Diaz"]
