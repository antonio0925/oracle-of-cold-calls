"""Campaign is optional. Only the call list is required.

Campaign is a label on the prep note, nothing more. Requiring it blocked the
BDR from building a call list at all.
"""
import app as app_module
from services.formatting import format_note_html

PROPS = {"firstname": "Ann", "lastname": "Lee", "company": "Acme"}
SCRIPT = "VOICEMAIL: hi\n\nLIVE CALL: hello"


def _client():
    c = app_module.app.test_client()
    with c.session_transaction() as sess:
        sess["summit_auth"] = True
    return c


def test_generate_does_not_reject_a_missing_campaign():
    resp = _client().post("/generate", json={"segment": "My List", "campaign": ""})
    assert resp.status_code != 400


def test_quick_generate_does_not_reject_a_missing_campaign():
    resp = _client().post("/prepare-battle", json={"segment": "My List", "campaign": ""})
    assert resp.status_code != 400


def test_missing_call_list_is_still_rejected():
    resp = _client().post("/generate", json={"segment": "", "campaign": "Q3"})
    assert resp.status_code == 400
    assert "call list" in resp.get_json()["error"].lower()


def test_note_header_keeps_the_title_when_campaign_is_blank():
    # The first version of this used a conditional that bound to the whole
    # f-string pair and silently dropped the COLD CALL PREP title line.
    html = format_note_html(PROPS, "", SCRIPT)
    assert "COLD CALL PREP - Ann Lee | Acme" in html
    assert "Generated" in html
    assert "|  | Generated" not in html


def test_note_header_shows_the_campaign_when_present():
    html = format_note_html(PROPS, "Q3 Outbound", SCRIPT)
    assert "COLD CALL PREP - Ann Lee | Acme" in html
    assert "Q3 Outbound | Generated" in html
