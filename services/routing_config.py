"""
Disposition-to-action routing config.

Maps call dispositions to Supersend sequence actions.
Hot-reloadable: read from disk each request if you prefer,
or just restart the Flask server after edits.
"""

# Each disposition maps to an action dict:
#   action: "advance" | "transfer" | "finish" | "retry" | "remove"
#   next_step: (optional) step number for advance
#   transfer_to: (optional) sequence ID for transfers
#   delay_hours: (optional) delay before action
#   log_entry: human-readable journey log entry

DISPOSITION_ROUTES = {
    "connected_interested": {
        "action": "advance",
        "next_step": None,  # advance to next step in current sequence
        "log_entry": "Connected — interested, advancing sequence",
    },
    "connected_not_interested": {
        "action": "finish",
        "log_entry": "Connected — not interested, finishing sequence",
    },
    "connected_callback": {
        "action": "advance",
        "next_step": None,
        "delay_hours": 48,
        "log_entry": "Connected — callback requested",
    },
    "voicemail": {
        "action": "advance",
        "next_step": None,
        "log_entry": "Voicemail left, advancing sequence",
    },
    "no_answer": {
        "action": "retry",
        "delay_hours": 4,
        "log_entry": "No answer — retry in 4 hours",
    },
    "busy": {
        "action": "retry",
        "delay_hours": 2,
        "log_entry": "Line busy — retry in 2 hours",
    },
    "wrong_number": {
        "action": "finish",
        "log_entry": "Wrong number — removed from sequence",
    },
    "gatekeeper": {
        "action": "advance",
        "next_step": None,
        "log_entry": "Gatekeeper — advancing to email follow-up",
    },
    "meeting_booked": {
        "action": "transfer",
        "transfer_to": None,  # set per-campaign in the UI
        "log_entry": "MEETING BOOKED — transferred to nurture sequence",
    },
    "do_not_call": {
        "action": "remove",
        "log_entry": "Do Not Call — permanently removed",
    },
}


def get_route(disposition):
    """Get the routing config for a disposition.

    Returns the route dict or None if unknown disposition.
    """
    return DISPOSITION_ROUTES.get(disposition)


def list_dispositions():
    """Return all known dispositions with their configs."""
    return [
        {"disposition": k, **v}
        for k, v in DISPOSITION_ROUTES.items()
    ]
