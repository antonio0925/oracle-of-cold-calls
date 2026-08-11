"""Supersend client: bulk actions must hit /contact/bulk-action with campaign fields."""
from unittest.mock import patch

from services.supersend import SupersendClient


def _capture():
    calls = []

    def fake_post(self, path, payload):
        calls.append((path, payload))
        return {"ok": True}

    return calls, fake_post


def test_finish_payload():
    calls, fake_post = _capture()
    with patch.object(SupersendClient, "_post", fake_post):
        SupersendClient("k").finish_contact("c1", "camp1")
    assert calls == [("/contact/bulk-action", {
        "action": "finish", "campaign_id": "camp1", "contact_ids": ["c1"],
    })]


def test_transfer_payload():
    calls, fake_post = _capture()
    with patch.object(SupersendClient, "_post", fake_post):
        SupersendClient("k").transfer_contact("c1", "camp1", "camp2")
    assert calls == [("/contact/bulk-action", {
        "action": "transfer", "campaign_id": "camp1",
        "to_campaign_id": "camp2", "contact_ids": ["c1"],
    })]


def test_assign_step_payload_with_and_without_node():
    calls, fake_post = _capture()
    with patch.object(SupersendClient, "_post", fake_post):
        ss = SupersendClient("k")
        ss.assign_step("c1", "camp1", 3, node_id="n9")
        ss.assign_step("c1", "camp1", 3)
    assert calls[0][1]["node_id"] == "n9"
    assert "node_id" not in calls[1][1]
    for path, payload in calls:
        assert path == "/contact/bulk-action"
        assert payload["campaign_id"] == "camp1"
        assert payload["step_number"] == 3
