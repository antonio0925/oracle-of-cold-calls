"""
Dual-layer session store: in-memory dict backed by atomic JSON files.

Thread-safe via threading.Lock.

The session file is also the record of what the BDR has done. Each
completed call is written into the session's "dispositions" map, so the
call list checks itself off and survives a refresh, a reconnect, and a
server restart. On the deployed service these files live on a persistent
volume.
"""
import os
import json
import copy
import threading
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Thread-safe in-memory session store
# ---------------------------------------------------------------------------
_sessions = {}
_sessions_lock = threading.Lock()


def get_session(key):
    """Get a session by key (thread-safe).

    Returns a deep copy so callers can mutate without affecting the store.
    Use set_session() to write changes back.
    """
    with _sessions_lock:
        data = _sessions.get(key)
        return copy.deepcopy(data) if data is not None else None


def set_session(key, data):
    """Set a session by key (thread-safe)."""
    with _sessions_lock:
        _sessions[key] = data


def delete_session(key):
    """Delete a session by key (thread-safe). No-op if missing."""
    with _sessions_lock:
        _sessions.pop(key, None)


def delete_session_file(session_id):
    """Remove the on-disk prep session file. No-op if missing.

    Must accompany delete_session after a successful approve — a stale
    disk file with failed_contact_ids drives duplicate retry writes.
    """
    path = f"sessions/prep_{session_id}.json"
    if os.path.exists(path):
        os.remove(path)


# ---------------------------------------------------------------------------
# Oracle (prep) session disk I/O
# ---------------------------------------------------------------------------
def save_session_to_disk(session_id, data):
    os.makedirs("sessions", exist_ok=True)
    path = f"sessions/prep_{session_id}.json"
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, default=str, indent=2)
    os.replace(tmp_path, path)  # Atomic write


def load_session_from_disk(session_id):
    path = f"sessions/prep_{session_id}.json"
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


def find_resumable_session(segment, calling_date):
    """Find an existing partial session that matches segment + date.
    Returns (session_id, session_data) or (None, None).
    """
    sessions_dir = "sessions"
    if not os.path.isdir(sessions_dir):
        return None, None
    best_session = None
    best_time = None
    for fname in os.listdir(sessions_dir):
        if not fname.startswith("prep_") or not fname.endswith(".json"):
            continue
        path = os.path.join(sessions_dir, fname)
        try:
            with open(path) as f:
                data = json.load(f)
            if (data.get("segment", "").lower().strip() == segment.lower().strip()
                    and data.get("calling_date", "").strip() == calling_date.strip()
                    and data.get("contacts")):
                mtime = os.path.getmtime(path)
                if best_time is None or mtime > best_time:
                    best_session = data
                    best_time = mtime
        except Exception:
            continue
    if best_session:
        return best_session.get("session_id"), best_session
    return None, None


def find_latest_session():
    """Return (session_id, data) for the newest completed call sheet.

    Today's Climb opens on whatever the BDR generated last, so there is
    nothing to pick from a list on a normal day.
    """
    sessions_dir = "sessions"
    if not os.path.isdir(sessions_dir):
        return None, None
    best, best_time = None, None
    for fname in os.listdir(sessions_dir):
        if not fname.startswith("prep_") or not fname.endswith(".json"):
            continue
        path = os.path.join(sessions_dir, fname)
        try:
            with open(path) as f:
                data = json.load(f)
            if not data.get("call_sheet"):
                continue
            mtime = os.path.getmtime(path)
            if best_time is None or mtime > best_time:
                best, best_time = data, mtime
        except Exception:
            continue
    if best:
        return best.get("session_id"), best
    return None, None


# Serialises the read-modify-write of a session's dispositions map.
#
# get_session returns a deep copy and set_session overwrites, so the store's
# own lock protects each access but not the pair. Two outcomes logged at the
# same moment would both read the map, each add one key, and the second write
# would erase the first: a completed call silently vanishes and the card
# un-ticks on the next refresh. The server runs one worker with eight threads,
# so this is a live window, not a theoretical one.
#
# This is a process-local lock. It is sufficient for one worker and is NOT
# sufficient if the deployment ever grows to two. At that point the file or a
# real datastore has to become the serialisation point.
_disposition_lock = threading.RLock()


def _mutate_dispositions(session_id, mutate):
    """Apply `mutate` to a session's dispositions map, atomically.

    `mutate` takes the map and changes it in place. Returns the updated map,
    or None if the session no longer exists.
    """
    with _disposition_lock:
        data = get_session(session_id) or load_session_from_disk(session_id)
        if not data:
            return None

        dispositions = data.get("dispositions") or {}
        mutate(dispositions)
        data["dispositions"] = dispositions

        set_session(session_id, data)
        save_session_to_disk(session_id, data)
        return dispositions


def record_disposition(session_id, contact_id, disposition, notes=""):
    """Mark one contact done in the session and persist it.

    Returns (dispositions, already_logged), or (None, False) if the session
    is gone. already_logged is True when this contact was already recorded,
    which is how a double-click avoids writing a second HubSpot note.

    Recording is deliberately separate from the HubSpot write, so a HubSpot
    outage cannot make the BDR lose their place in the list.
    """
    seen = {"already": False}

    def mutate(dispositions):
        key = str(contact_id)
        seen["already"] = key in dispositions
        dispositions[key] = {
            "disposition": disposition,
            "notes": notes,
            "at": utc_now_iso(),
        }

    dispositions = _mutate_dispositions(session_id, mutate)
    if dispositions is None:
        return None, False
    return dispositions, seen["already"]


def clear_disposition(session_id, contact_id):
    """Undo one outcome. Same atomicity as recording it."""
    return _mutate_dispositions(
        session_id, lambda d: d.pop(str(contact_id), None)
    )


def utc_now_iso():
    """Return current UTC time as ISO string (replaces naive datetime.now())."""
    return datetime.now(timezone.utc).isoformat()
