"""
Disposition-to-action routing config.

Routing is log-only. The app records the disposition on the HubSpot
contact and appends log_entry to the journey log. No campaign or
sequence system is called.

Hot-reloadable: read from disk each request if you prefer,
or just restart the Flask server after edits.
"""

# Each disposition maps to an action dict:
#   log_entry: human-readable journey log entry. This is the only key
#              the app reads.
#
# The action, next_step, transfer_to, and delay_hours keys are inert
# metadata. They described the old campaign behaviour. They are kept
# because the UI dropdown and the API response still surface "action",
# and a later refactor may revive them.

DISPOSITION_ROUTES = {
    "connected_interested": {
        "action": "advance",
        "next_step": None,  # advance to next step in current sequence
        "log_entry": "Connected: interested",
    },
    "connected_not_interested": {
        "action": "finish",
        "log_entry": "Connected: not interested",
    },
    "connected_callback": {
        "action": "advance",
        "next_step": None,
        "delay_hours": 48,
        "log_entry": "Connected: callback requested",
    },
    "voicemail": {
        "action": "advance",
        "next_step": None,
        "log_entry": "Voicemail left",
    },
    "no_answer": {
        "action": "retry",
        "delay_hours": 4,
        "log_entry": "No answer: retry in 4 hours",
    },
    "busy": {
        "action": "retry",
        "delay_hours": 2,
        "log_entry": "Line busy: retry in 2 hours",
    },
    "wrong_number": {
        "action": "finish",
        "log_entry": "Wrong number",
    },
    "gatekeeper": {
        "action": "advance",
        "next_step": None,
        "log_entry": "Gatekeeper reached",
    },
    "meeting_booked": {
        "action": "transfer",
        "transfer_to": None,  # set per-campaign in the UI
        "log_entry": "MEETING BOOKED",
    },
    "do_not_call": {
        "action": "remove",
        "log_entry": "Do Not Call: marked donotcall in HubSpot",
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
