"""
Run ledger for the autonomous agent.

Every decision the agent makes is appended to a JSONL ledger so a run is
auditable after the fact and so the agent never repeats an action it already
took today (idempotency).

Ledger location: .agent_state/ledger-YYYY-MM-DD.jsonl
"""
import json
import os
import threading
from datetime import datetime, timezone

STATE_DIR = os.environ.get(
    "AGENT_STATE_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".agent_state"),
)

_lock = threading.Lock()


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def ledger_path(day=None):
    return os.path.join(STATE_DIR, "ledger-{}.jsonl".format(day or today_str()))


def record(event_type, contact_id=None, **fields):
    """Append one event to today's ledger. Returns the event dict."""
    event = {
        "ts": utc_now_iso(),
        "event": event_type,
        "contact_id": str(contact_id) if contact_id is not None else None,
    }
    event.update(fields)
    with _lock:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(ledger_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")
    return event


def read_ledger(day=None):
    """Return all events for a day (default today). Missing file -> []."""
    path = ledger_path(day)
    if not os.path.exists(path):
        return []
    events = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    return events


def already_done(action, contact_id, day=None):
    """True if this exact action already succeeded for this contact today.

    This is the agent's idempotency guard: reruns are safe, no double-writes
    of prep notes and no double-routing of a sequence.
    """
    cid = str(contact_id)
    for ev in read_ledger(day):
        if (
            ev.get("event") == "acted"
            and ev.get("action") == action
            and ev.get("contact_id") == cid
            and ev.get("ok") is True
        ):
            return True
    return False


def summarize(day=None):
    """Aggregate a day's ledger into counts for the run report."""
    events = read_ledger(day)
    summary = {
        "day": day or today_str(),
        "total_events": len(events),
        "observed": 0,
        "decided": 0,
        "acted_ok": 0,
        "acted_failed": 0,
        "skipped": 0,
        "by_action": {},
        "errors": [],
    }
    for ev in events:
        kind = ev.get("event")
        if kind == "observed":
            summary["observed"] += 1
        elif kind == "decided":
            summary["decided"] += 1
            act = ev.get("action", "unknown")
            summary["by_action"][act] = summary["by_action"].get(act, 0) + 1
        elif kind == "skipped":
            summary["skipped"] += 1
        elif kind == "acted":
            if ev.get("ok"):
                summary["acted_ok"] += 1
            else:
                summary["acted_failed"] += 1
                summary["errors"].append(
                    {"contact_id": ev.get("contact_id"), "action": ev.get("action"), "error": ev.get("error")}
                )
    return summary
