"""Session store: disk roundtrip and full cleanup after successful approve."""
from services.sessions import (
    delete_session_file,
    load_session_from_disk,
    save_session_to_disk,
)


def test_disk_roundtrip_and_file_delete(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data = {"session_id": "t1", "contacts": [{"contact_id": "1"}],
            "failed_contact_ids": ["1"]}
    save_session_to_disk("t1", data)
    assert load_session_from_disk("t1") == data

    # After a fully successful approve, the disk file must be gone —
    # a stale failed_contact_ids list drives duplicate HubSpot notes.
    delete_session_file("t1")
    assert load_session_from_disk("t1") is None
    delete_session_file("t1")  # idempotent, no raise
