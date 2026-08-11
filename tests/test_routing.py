"""Disposition routing: every route must carry an action the executor understands."""
from services.routing_config import DISPOSITION_ROUTES, get_route, list_dispositions

# Actions api_action_complete executes against Supersend. "retry" is a
# deliberate no-op there (the rep simply calls again later).
EXECUTED_ACTIONS = {"advance", "transfer", "finish", "remove"}
KNOWN_ACTIONS = EXECUTED_ACTIONS | {"retry"}


def test_all_actions_are_known():
    for disposition, route in DISPOSITION_ROUTES.items():
        assert route["action"] in KNOWN_ACTIONS, disposition
        assert route["log_entry"]


def test_compliance_dispositions_execute_in_supersend():
    # do_not_call and meeting_booked must end or move the sequence —
    # a route action outside EXECUTED_ACTIONS would silently no-op.
    assert DISPOSITION_ROUTES["do_not_call"]["action"] in EXECUTED_ACTIONS
    assert DISPOSITION_ROUTES["meeting_booked"]["action"] in EXECUTED_ACTIONS


def test_get_route():
    assert get_route("do_not_call")["action"] == "remove"
    assert get_route("nonsense") is None
    assert len(list_dispositions()) == len(DISPOSITION_ROUTES)
