"""
Dual-layer session store — in-memory dict backed by atomic JSON files.

Thread-safe via threading.Lock (Phase 4 addition).
Covers both Oracle (prep_*) and Forge (forge_*) sessions.
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


def find_resumable_session(segment, campaign, calling_date):
    """Find an existing partial session that matches segment+campaign+date.
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
                    and data.get("campaign", "").lower().strip() == campaign.lower().strip()
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


# ---------------------------------------------------------------------------
# Forge session disk I/O
# ---------------------------------------------------------------------------
def save_forge_session(session_id, data):
    """Save a Forge session to disk with atomic write."""
    os.makedirs("sessions", exist_ok=True)
    path = f"sessions/forge_{session_id}.json"
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, default=str, indent=2)
    os.replace(tmp_path, path)


def load_forge_session(session_id):
    """Load a Forge session from disk."""
    path = f"sessions/forge_{session_id}.json"
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


def list_forge_sessions():
    """List all Forge sessions for recovery."""
    sessions_dir = "sessions"
    if not os.path.isdir(sessions_dir):
        return []
    results = []
    for fname in sorted(os.listdir(sessions_dir), reverse=True):
        if not fname.startswith("forge_") or not fname.endswith(".json"):
            continue
        path = os.path.join(sessions_dir, fname)
        try:
            with open(path) as f:
                data = json.load(f)
            results.append({
                "session_id": data.get("session_id", ""),
                "campaign_id": data.get("campaign_id", ""),
                "campaign_name": data.get("campaign_name", ""),
                "stage": data.get("stage", 1),
                "status": data.get("status", ""),
                "discovered_domains_count": len(data.get("discovered_domains", [])),
                "companies_count": len(data.get("companies", [])),
                "qualified_companies_count": len(data.get("qualified_companies", [])),
                "people_count": len(data.get("people", [])),
                "modified": datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M"),
            })
        except Exception:
            continue
    return results[:20]


def utc_now_iso():
    """Return current UTC time as ISO string (replaces naive datetime.now())."""
    return datetime.now(timezone.utc).isoformat()
