"""Scripts are written a batch at a time, not one at a time.

One Octave call takes about a minute, and that minute is spent waiting on the
network. Measured against the live agent: 8 contacts took 392s serially and
68s together, 8 of 8 succeeding. A 50-call route drops from about 53 minutes
to about 11.

Batching moves work off the critical path, and it also moves three decisions
that used to be trivially correct in a serial loop:

  the target      a batch is chosen before any of it is generated, so the
                  scan must count queued work or it overshoots
  the account cap charged when queued, not when the script lands, or one
                  batch could hold five people from the same company
  the last batch  the scan almost always ends mid-batch, so the remainder
                  must be flushed or those contacts vanish
"""
import json
import os
import threading
import time
from unittest.mock import patch

import pytest

import app as app_module
from services import sessions


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
    out = []
    for chunk in response.get_data(as_text=True).split("\n\n"):
        chunk = chunk.strip()
        if chunk.startswith("data: "):
            out.append(json.loads(chunk[6:]))
    return out


class _HubSpot:
    """A list of dialable US contacts, each at its own company."""

    def __init__(self, count, company_of=None):
        self.count = count
        self.company_of = company_of or (lambda i: f"Company {i}")

    def search_lists(self, name):
        return "list-1"

    def get_list_memberships(self, list_id):
        return [str(i) for i in range(self.count)]

    def batch_get_contacts(self, ids, properties):
        return [{
            "id": str(i),
            "properties": {
                "firstname": "Ann", "lastname": f"Diaz{i}",
                "company": self.company_of(int(i)), "jobtitle": "VP Sales",
                "state": "Austin, Texas, United States",
                "phone": "+1 512 555 0100",
            },
        } for i in ids]

    def get_associated_companies(self, cid):
        return []

    def search_emails_for_contact(self, cid):
        return {"subject": "hello", "body_html": "hi", "body_text": "hi"}

    def search_notes_for_contact(self, cid):
        return False

    def last_call_dates(self, ids):
        return {str(i): "" for i in ids}


class _Octave:
    """Records how many calls are in flight at once."""

    def __init__(self, delay=0.05, fail_on=()):
        self.delay = delay
        self.fail_on = set(fail_on)
        self.calls = 0
        self.in_flight = 0
        self.peak = 0
        self._lock = threading.Lock()

    def generate_call_script(self, props, subject, body):
        name = f"{props.get('firstname')} {props.get('lastname')}"
        with self._lock:
            self.calls += 1
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        try:
            time.sleep(self.delay)
            if props.get("lastname") in self.fail_on:
                raise RuntimeError("Octave said no")
            return {"content": f"### OUTPUT 1: VOICEMAIL SCRIPT\n- hello {name}\n"}
        finally:
            with self._lock:
                self.in_flight -= 1


def _build(target, contacts, octave=None, company_of=None, concurrency=5):
    octave = octave or _Octave()
    hs = _HubSpot(contacts, company_of)
    with patch.object(app_module, "HubSpotClient", lambda token: hs), \
            patch.object(app_module, "OctaveClient", lambda key: octave), \
            patch.object(app_module.config, "HUBSPOT_ACCESS_TOKEN", "tok"), \
            patch.object(app_module.config, "OCTAVE_API_KEY", "key"), \
            patch.object(app_module.config, "OCTAVE_CONCURRENCY", concurrency):
        resp = _client().post("/generate", json={
            "segment": "My List", "target": target,
            "calling_date": "2026-08-12", "calling_time": "09:00",
            "skip_existing": False,
        })
    return _events(resp), octave


def test_calls_actually_overlap():
    # The whole point. A serial loop would peak at 1.
    events, octave = _build(target=10, contacts=10, concurrency=5)
    assert octave.peak > 1, "scripts were still written one at a time"
    assert octave.peak <= 5, f"ran {octave.peak} at once, over the configured 5"


def test_the_target_is_hit_exactly_and_not_overshot():
    # A batch is chosen before any of it is generated. Counting only finished
    # work lets the scan keep selecting people, and the run overshoots.
    events, octave = _build(target=7, contacts=50, concurrency=5)
    complete = events[-1]
    assert complete["type"] == "complete"
    assert complete["stats"]["prepped"] == 7
    assert octave.calls == 7, f"{octave.calls} scripts written for a target of 7"


def test_the_last_partial_batch_is_not_dropped():
    # 12 contacts at 5 per batch ends with a batch of 2. Those two are already
    # charged against the account cap, so losing them would be silent.
    events, octave = _build(target=50, contacts=12, concurrency=5)
    complete = events[-1]
    assert complete["stats"]["prepped"] == 12
    assert octave.calls == 12
    done = [e for e in events if e["type"] == "done_contact"]
    assert len(done) == 12


def test_the_account_cap_holds_inside_a_single_batch():
    # Everyone at one company. The cap is 2 per account per day, and a batch
    # is chosen before any of it is generated, so charging the cap on success
    # would let a whole batch through.
    events, octave = _build(target=50, contacts=10, company_of=lambda i: "Acme")
    complete = events[-1]
    assert complete["stats"]["prepped"] == app_module.config.MAX_CONTACTS_PER_ACCOUNT_PER_DAY
    assert octave.calls == app_module.config.MAX_CONTACTS_PER_ACCOUNT_PER_DAY
    assert complete["stats"]["skipped_account_cap"] == 10 - octave.calls


def test_one_failure_does_not_take_down_its_batch():
    events, octave = _build(target=50, contacts=5,
                            octave=_Octave(fail_on=("Diaz2",)))
    complete = events[-1]
    assert complete["type"] == "complete"
    assert complete["stats"]["prepped"] == 4
    assert complete["stats"]["errors"] == 1
    failed = [e for e in events if e["type"] == "error_contact"]
    assert len(failed) == 1


def test_every_generated_contact_reaches_the_saved_session():
    events, _ = _build(target=50, contacts=12)
    data = sessions.load_session_from_disk(events[-1]["session_id"])
    assert len(data["contacts"]) == 12
    assert all(c["script_content"] for c in data["contacts"])
    placed = sum(len(b["contacts"]) for b in data["call_sheet"]) + len(data["unknown_tz"])
    assert placed == 12


def test_progress_never_runs_past_the_target():
    # The bar is driven by queued work now, so it must still be clamped.
    events, _ = _build(target=6, contacts=40)
    for e in events:
        if e["type"] == "progress":
            assert e["current"] <= e["total"], e
