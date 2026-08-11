"""Disposition routing: log-only. Every route must carry a usable log entry."""
from services.routing_config import DISPOSITION_ROUTES, get_route, list_dispositions

# Routing no longer executes against any sequence system. "action" survives as
# inert metadata that the UI dropdown and the API response still surface, so it
# must stay inside the known set.
KNOWN_ACTIONS = {"advance", "transfer", "finish", "remove", "retry"}


def test_all_actions_are_known():
    for disposition, route in DISPOSITION_ROUTES.items():
        assert route["action"] in KNOWN_ACTIONS, disposition
        assert route["log_entry"]


def test_get_route():
    assert get_route("do_not_call")["action"] == "remove"
    assert get_route("nonsense") is None
    assert len(list_dispositions()) == len(DISPOSITION_ROUTES)


def test_no_em_dashes_in_log_entries():
    # Journey log entries are user-visible in HubSpot.
    for disposition, route in DISPOSITION_ROUTES.items():
        assert "—" not in route["log_entry"], disposition
