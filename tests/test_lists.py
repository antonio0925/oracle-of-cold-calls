"""The list picker must only offer contact lists.

A company or deal list looks identical in the dropdown but resolves to zero
contacts, so the BDR gets an empty route and nothing explaining why.
"""
from unittest.mock import patch

import app as app_module


def _client():
    c = app_module.app.test_client()
    with c.session_transaction() as sess:
        sess["summit_auth"] = True
    return c


def _search_payload(lists):
    return {"lists": lists, "hasMore": False, "offset": 0}


def _lst(list_id, name, object_type, creator="87514817", size="10"):
    return {
        "listId": list_id, "name": name, "objectTypeId": object_type,
        "createdById": creator, "processingType": "DYNAMIC",
        "additionalProperties": {"hs_list_size": size},
    }


def test_company_and_deal_lists_are_excluded():
    payload = _search_payload([
        _lst("1", "My Contacts", "0-1"),
        _lst("2", "Target Accounts", "0-2"),        # companies
        _lst("3", "Open Deals", "0-3"),             # deals
    ])
    with patch.object(app_module.HubSpotClient, "_post", return_value=payload), \
            patch.object(app_module.config, "HUBSPOT_ACCESS_TOKEN", "tok"), \
            patch.object(app_module.config, "HUBSPOT_CREATOR_ID", "87514817"):
        names = [l["name"] for l in _client().get("/api/lists").get_json()["lists"]]

    assert names == ["My Contacts"]


def test_lists_from_other_creators_are_excluded():
    payload = _search_payload([
        _lst("1", "Mine", "0-1", creator="87514817"),
        _lst("2", "Someone else's", "0-1", creator="99999999"),
    ])
    with patch.object(app_module.HubSpotClient, "_post", return_value=payload), \
            patch.object(app_module.config, "HUBSPOT_ACCESS_TOKEN", "tok"), \
            patch.object(app_module.config, "HUBSPOT_CREATOR_ID", "87514817"):
        names = [l["name"] for l in _client().get("/api/lists").get_json()["lists"]]

    assert names == ["Mine"]


def test_contact_object_type_constant_is_correct():
    # Companies are 0-2 and deals are 0-3. Getting this wrong empties the picker.
    assert app_module.CONTACT_OBJECT_TYPE == "0-1"
