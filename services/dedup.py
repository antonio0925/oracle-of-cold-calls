"""
Signal deduplication — prevents the same signal from being processed twice
within a cooldown window.

Uses a simple in-memory store with TTL. Survives restarts via optional
disk persistence. Thread-safe via threading.Lock.
"""
import time
import json
import os
import logging
import threading

log = logging.getLogger(__name__)

# Default cooldown: 24 hours
DEFAULT_COOLDOWN_SECONDS = 86400

# In-memory store: { "email::signal_type": expiry_timestamp }
_seen = {}
_lock = threading.Lock()

DEDUP_FILE = "sessions/signal_dedup.json"


def _load_from_disk():
    """Load persisted dedup state on startup."""
    global _seen
    if os.path.exists(DEDUP_FILE):
        try:
            with open(DEDUP_FILE) as f:
                data = json.load(f)
            now = time.time()
            # Only load entries that haven't expired
            with _lock:
                _seen = {k: v for k, v in data.items() if v > now}
            log.info("Loaded %d active dedup entries from disk", len(_seen))
        except Exception as e:
            log.warning("Failed to load dedup state: %s", e)


def _save_to_disk():
    """Persist current dedup state to disk. Caller must hold _lock."""
    try:
        os.makedirs(os.path.dirname(DEDUP_FILE), exist_ok=True)
        now = time.time()
        active = {k: v for k, v in _seen.items() if v > now}
        with open(DEDUP_FILE, "w") as f:
            json.dump(active, f)
    except Exception as e:
        log.warning("Failed to save dedup state: %s", e)


# Load on import
_load_from_disk()


def is_duplicate(email, signal_type, cooldown_seconds=DEFAULT_COOLDOWN_SECONDS):
    """Check if this email+signal_type combo was seen within the cooldown window.

    Returns True if duplicate (should be skipped), False if new.
    """
    key = f"{email.lower().strip()}::{signal_type}"
    now = time.time()

    with _lock:
        # Clean expired entries lazily (when store exceeds 1000 entries)
        if len(_seen) > 1000:
            expired = [k for k, v in _seen.items() if v <= now]
            for k in expired:
                del _seen[k]

        expiry = _seen.get(key)
        if expiry and expiry > now:
            return True

    return False


def mark_seen(email, signal_type, cooldown_seconds=DEFAULT_COOLDOWN_SECONDS):
    """Mark this email+signal_type as seen. Call after successful processing."""
    key = f"{email.lower().strip()}::{signal_type}"
    with _lock:
        _seen[key] = time.time() + cooldown_seconds
        _save_to_disk()
