"""Disposition routing: log-only. Every route must carry a usable log entry."""
from unittest.mock import MagicMock, patch

import app as app_module
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


def _complete(disposition, hs):
    client = app_module.app.test_client()
    # The password gate covers every /api route. Sign in first, otherwise
    # these assertions would only prove the gate works.
    with client.session_transaction() as sess:
        sess["summit_auth"] = True
    with patch.object(app_module, "HubSpotClient", return_value=hs), \
            patch.object(app_module.config, "HUBSPOT_ACCESS_TOKEN", "token"):
        return client.post(
            "/api/action/complete",
            json={"contact_id": "123", "disposition": disposition},
        )


def test_do_not_call_sets_standard_hubspot_donotcall_property():
    # Compliance: oracle_ properties are invisible to the rest of HubSpot.
    # The standard donotcall property is the one every other surface reads.
    hs = MagicMock()
    resp = _complete("do_not_call", hs)

    assert resp.status_code == 200
    props = hs.update_contact_properties.call_args[0][1]
    assert props["donotcall"] == "true"
    assert props["oracle_call_disposition"] == "do_not_call"


def test_non_dnc_disposition_does_not_set_donotcall():
    hs = MagicMock()
    resp = _complete("voicemail", hs)

    assert resp.status_code == 200
    props = hs.update_contact_properties.call_args[0][1]
    assert "donotcall" not in props


def test_disposition_completes_without_any_sequence_call():
    # The response must no longer carry a campaign result, and the handler
    # must return 200 with no external sequence system involved.
    hs = MagicMock()
    resp = _complete("connected_interested", hs)

    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    assert "supersend_result" not in resp.get_json()
    hs.append_journey_log.assert_called_once()
