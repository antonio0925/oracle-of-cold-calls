"""Concurrent and repeated outcome logging.

Codex confirmed record_disposition was an unlocked read-modify-write: two
completions could interleave and one would vanish. These tests fail against
the unlocked version.
"""
import json
import os
import threading
from unittest.mock import MagicMock, patch

import pytest

import app as app_module
from services import sessions

N = 40


@pytest.fixture
def big_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs("sessions", exist_ok=True)
    data = {
        "session_id": "s1",
        "call_sheet": [{
            "label": "B", "contacts": [
                {"name": f"C{i}", "contact_id": str(i)} for i in range(N)
            ],
        }],
        "unknown_tz": [],
        "contacts": [{"contact_id": str(i), "script_content": ""} for i in range(N)],
    }
    sessions.set_session("s1", json.loads(json.dumps(data)))
    sessions.save_session_to_disk("s1", json.loads(json.dumps(data)))
    yield
    sessions.delete_session("s1")


def test_concurrent_completions_do_not_lose_any(big_session):
    errors = []

    def log_one(i):
        try:
            sessions.record_disposition("s1", str(i), "voicemail")
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=log_one, args=(i,)) for i in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    on_disk = sessions.load_session_from_disk("s1")["dispositions"]
    missing = sorted(set(str(i) for i in range(N)) - set(on_disk), key=int)
    assert not missing, f"{len(missing)} completions lost: {missing[:10]}"
    assert len(on_disk) == N


def test_concurrent_undo_and_complete_stay_consistent(big_session):
    for i in range(N):
        sessions.record_disposition("s1", str(i), "voicemail")

    def undo(i):
        sessions.clear_disposition("s1", str(i))

    threads = [threading.Thread(target=undo, args=(i,)) for i in range(0, N, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    remaining = sessions.load_session_from_disk("s1")["dispositions"]
    assert sorted(remaining, key=int) == [str(i) for i in range(1, N, 2)]


def test_record_reports_whether_it_had_already_been_logged(big_session):
    _, already = sessions.record_disposition("s1", "1", "voicemail")
    assert already is False
    _, already = sessions.record_disposition("s1", "1", "voicemail")
    assert already is True


def _client():
    c = app_module.app.test_client()
    with c.session_transaction() as sess:
        sess["summit_auth"] = True
    return c


def test_replaying_a_completion_does_not_stack_hubspot_notes(big_session):
    hs = MagicMock()
    client = _client()
    with patch.object(app_module, "HubSpotClient", return_value=hs), \
            patch.object(app_module.config, "HUBSPOT_ACCESS_TOKEN", "tok"):
        for _ in range(3):
            resp = client.post("/api/climb/complete", json={
                "session_id": "s1", "contact_id": "1", "disposition": "voicemail",
            })
            assert resp.status_code == 200

    assert hs.create_note_for_contact.call_count == 1, \
        "a double-click must not stack notes on the contact record"
