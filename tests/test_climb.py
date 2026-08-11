"""Today's Climb: the call list and its check-off, backed by the session file.

The oracle_ custom properties do not exist in the HubSpot portal, so
completion lives in the session and the outcome reaches HubSpot as a note.
"""
import json
import os
from unittest.mock import MagicMock, patch

import pytest

import app as app_module
from services import sessions


SESSION = {
    "session_id": "s1",
    "segment": "My List",
    "calling_date": "2026-08-10",
    "call_sheet": [
        {
            "label": "Early", "color": "#000", "description": "d", "local_time": "8am",
            "contacts": [
                {"name": "Ann Lee", "contact_id": "1", "company": "Acme", "tz": "ET"},
                {"name": "Bob Ray", "contact_id": "2", "company": "Acme", "tz": "ET"},
            ],
        },
    ],
    "unknown_tz": [{"name": "Cy Doe", "contact_id": "3", "company": "Zeta", "tz": "???"}],
    "contacts": [
        {"contact_id": "1", "script_content": "VOICEMAIL: hi"},
        {"contact_id": "2", "script_content": "LIVE CALL: yo"},
        {"contact_id": "3", "script_content": ""},
    ],
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("sessions", exist_ok=True)
    sessions.set_session("s1", json.loads(json.dumps(SESSION)))
    sessions.save_session_to_disk("s1", json.loads(json.dumps(SESSION)))
    c = app_module.app.test_client()
    with c.session_transaction() as sess:
        sess["summit_auth"] = True
    yield c
    sessions.delete_session("s1")


def _climb(client):
    return client.get("/api/climb?session_id=s1").get_json()


def test_climb_returns_the_sheet_with_scripts_attached(client):
    data = _climb(client)
    assert data["session_id"] == "s1"
    ann = data["blocks"][0]["contacts"][0]
    assert ann["name"] == "Ann Lee"
    assert ann["script"] == "VOICEMAIL: hi"
    assert ann["completed"] is False
    assert data["totals"] == {"total": 3, "completed": 0, "remaining": 3}


def test_completing_a_call_checks_it_off_and_notes_hubspot(client):
    hs = MagicMock()
    with patch.object(app_module, "HubSpotClient", return_value=hs), \
            patch.object(app_module.config, "HUBSPOT_ACCESS_TOKEN", "tok"):
        resp = client.post("/api/climb/complete", json={
            "session_id": "s1", "contact_id": "1",
            "disposition": "voicemail", "notes": "left a message",
        })

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True and body["completed"] == 1
    assert body["hubspot_note_written"] is True

    hs.create_note_for_contact.assert_called_once()
    note = hs.create_note_for_contact.call_args[0][1]
    assert "CALL OUTCOME" in note and "Voicemail left" in note
    assert "left a message" in note

    data = _climb(client)
    assert data["blocks"][0]["contacts"][0]["completed"] is True
    assert data["totals"]["completed"] == 1
    assert data["totals"]["remaining"] == 2


def test_check_off_survives_a_hubspot_failure(client):
    # The BDR must not lose their place because HubSpot is down.
    hs = MagicMock()
    hs.create_note_for_contact.side_effect = RuntimeError("hubspot 500")
    with patch.object(app_module, "HubSpotClient", return_value=hs), \
            patch.object(app_module.config, "HUBSPOT_ACCESS_TOKEN", "tok"):
        resp = client.post("/api/climb/complete", json={
            "session_id": "s1", "contact_id": "2", "disposition": "no_answer",
        })

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["hubspot_note_written"] is False
    assert "hubspot 500" in body["hubspot_error"]
    assert _climb(client)["totals"]["completed"] == 1


def test_completion_persists_to_disk(client):
    with patch.object(app_module.config, "HUBSPOT_ACCESS_TOKEN", ""):
        client.post("/api/climb/complete", json={
            "session_id": "s1", "contact_id": "1", "disposition": "voicemail",
        })
    sessions.delete_session("s1")          # drop the in-memory copy
    on_disk = sessions.load_session_from_disk("s1")
    assert on_disk["dispositions"]["1"]["disposition"] == "voicemail"


def test_undo_clears_one_outcome(client):
    with patch.object(app_module.config, "HUBSPOT_ACCESS_TOKEN", ""):
        client.post("/api/climb/complete", json={
            "session_id": "s1", "contact_id": "1", "disposition": "voicemail",
        })
    resp = client.post("/api/climb/undo", json={"session_id": "s1", "contact_id": "1"})
    assert resp.status_code == 200
    assert _climb(client)["totals"]["completed"] == 0


def test_unknown_disposition_is_rejected(client):
    resp = client.post("/api/climb/complete", json={
        "session_id": "s1", "contact_id": "1", "disposition": "nonsense",
    })
    assert resp.status_code == 400


def test_missing_fields_are_rejected(client):
    resp = client.post("/api/climb/complete", json={"session_id": "s1"})
    assert resp.status_code == 400


def test_empty_state_when_no_session_exists(client):
    data = client.get("/api/climb?session_id=nope").get_json()
    assert data["blocks"] == []
    assert data["totals"]["total"] == 0
    assert "Route Plan" in data["msg"]
