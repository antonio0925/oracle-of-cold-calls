"""
PERCEIVE — build the agent's world model for one run.

The agent does not trust the UI to tell it what to do. It goes and looks:
  * contacts on the target HubSpot list (the dial universe)
  * whether each already has a prep note (don't redo work)
  * whether each has a logged outbound email (Oracle requires one)
  * pending oracle_ actions with a call disposition (routing work)
  * resolved timezone (so the call sheet can be built)

Output is a list of Observation dicts — plain data, no side effects.
"""
import logging

from services import timezone as tz_service

_log = logging.getLogger(__name__)

CONTACT_PROPERTIES = [
    "firstname", "lastname", "email", "company", "jobtitle",
    "phone", "mobilephone", "city", "state", "country", "hs_timezone",
    "oracle_pending_action", "oracle_action_type", "oracle_campaign_id",
    "oracle_node_id", "oracle_step_number", "oracle_call_disposition",
    "oracle_last_action_date",
]


def _observe_contact(hs, contact, want_notes=True):
    props = contact.get("properties", {}) or {}
    cid = str(contact.get("id"))
    obs = {
        "contact_id": cid,
        "email": props.get("email") or "",
        "name": (" ".join([props.get("firstname") or "", props.get("lastname") or ""])).strip(),
        "company": props.get("company") or "",
        "jobtitle": props.get("jobtitle") or "",
        "phone": props.get("phone") or props.get("mobilephone") or "",
        "properties": props,
        "timezone": "UNKNOWN",
        "has_prep_note": False,
        "email_data": None,
        "disposition": props.get("oracle_call_disposition") or "",
        "pending_action": props.get("oracle_pending_action") or "",
        "errors": [],
    }

    try:
        obs["timezone"] = tz_service.resolve_timezone(props)
    except Exception as exc:  # never let enrichment kill the run
        obs["errors"].append("timezone: {}".format(exc))

    try:
        obs["email_data"] = hs.search_emails_for_contact(cid)
    except Exception as exc:
        obs["errors"].append("email_lookup: {}".format(exc))

    if want_notes:
        try:
            notes = hs.get_all_prep_notes_for_contact(cid)
            obs["has_prep_note"] = bool(notes)
        except Exception as exc:
            obs["errors"].append("note_lookup: {}".format(exc))

    return obs


def perceive_list(hs, list_name, limit=None):
    """Observe every contact on a named HubSpot list."""
    lists = hs.search_lists(list_name)
    if not lists:
        raise LookupError("No HubSpot list matching {!r}".format(list_name))
    list_id = lists[0].get("listId") or lists[0].get("id")

    contact_ids = hs.get_list_memberships(list_id)
    if limit:
        contact_ids = contact_ids[:limit]
    if not contact_ids:
        return []

    contacts = hs.batch_get_contacts(contact_ids, CONTACT_PROPERTIES)
    return [_observe_contact(hs, c) for c in contacts]


def perceive_pending_actions(hs):
    """Observe contacts flagged oracle_pending_action = 'pending'.

    These are dispositioned calls whose outcome is not yet logged.
    """
    try:
        pending = hs.get_pending_actions()
    except Exception as exc:
        _log.warning("pending action lookup failed: %s", exc)
        return []
    return [_observe_contact(hs, c, want_notes=False) for c in pending]


def perceive(hs, list_name=None, limit=None):
    """Full perception pass. Returns (dial_observations, routing_observations)."""
    dial = perceive_list(hs, list_name, limit) if list_name else []
    routing = perceive_pending_actions(hs)
    return dial, routing
