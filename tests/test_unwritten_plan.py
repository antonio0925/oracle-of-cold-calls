"""Recovering a route plan that was generated but never written to HubSpot.

Generating and writing are two steps. The write button only ever appeared at
the end of a generate stream, and the session id lived in browser memory, so a
reload lost the only path to it: finished scripts sat on disk and could never
reach HubSpot without regenerating from scratch.

A successful write deletes the session file, so a complete file that still
exists is exactly the unwritten case.
"""
import json
import os

import pytest

import app as app_module
from services import sessions


def _client():
    c = app_module.app.test_client()
    with c.session_transaction() as sess:
        sess["summit_auth"] = True
    return c


def _write(session_id, complete, contacts):
    sessions.save_session_to_disk(session_id, {
        "session_id": session_id,
        "segment": "My List",
        "calling_date": "2026-08-10",
        "generation_complete": complete,
        "contacts": [{"contact_id": str(i)} for i in range(contacts)],
    })


@pytest.fixture(autouse=True)
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("sessions", exist_ok=True)


def test_a_complete_unwritten_plan_is_offered_back():
    _write("done", complete=True, contacts=3)
    rows = _client().get("/api/recoverable-sessions").get_json()["sessions"]
    row = next(r for r in rows if r["session_id"] == "done")
    assert row["is_complete"] is True
    assert row["prepped_count"] == 3
    assert row["segment"] == "My List"


def test_a_written_plan_is_not_offered_because_its_file_is_gone():
    _write("written", complete=True, contacts=3)
    # This is what a successful /approve does.
    sessions.delete_session_file("written")
    rows = _client().get("/api/recoverable-sessions").get_json()["sessions"]
    assert all(r["session_id"] != "written" for r in rows)


def test_a_partial_plan_is_listed_but_marked_incomplete():
    # A run that died mid-generation is resumable, not writable.
    _write("partial", complete=False, contacts=2)
    rows = _client().get("/api/recoverable-sessions").get_json()["sessions"]
    row = next(r for r in rows if r["session_id"] == "partial")
    assert row["is_complete"] is False


def test_a_plan_with_no_contacts_is_not_listed():
    _write("empty", complete=True, contacts=0)
    rows = _client().get("/api/recoverable-sessions").get_json()["sessions"]
    assert all(r["session_id"] != "empty" for r in rows)


def test_the_endpoint_is_behind_the_password_gate():
    anon = app_module.app.test_client().get("/api/recoverable-sessions")
    assert anon.status_code == 401
