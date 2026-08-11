"""
ACT — the only module allowed to cause side effects.

Every executor takes a decision dict and returns (ok, detail). Nothing here
raises into the loop: a failed contact is logged and the run continues.
"""
import logging

from services import formatting, call_sheet as call_sheet_service, slack
from services.octave import script_text

_log = logging.getLogger(__name__)


def act_prep_contact(ctx, decision):
    """Generate Octave call content and write the prep note to HubSpot."""
    obs = decision["obs"]
    props = obs["properties"]
    email_data = obs.get("email_data") or {}

    script_data = ctx.octave.generate_call_script(
        props,
        email_data.get("subject", ""),
        email_data.get("body_text") or email_data.get("body_html") or "",
    )
    script = script_text(script_data)
    if not script.strip():
        return False, "Octave returned empty content"

    html = formatting.format_note_html(props, script)
    ctx.hubspot.create_note_for_contact(obs["contact_id"], html)

    ctx.prepped.append({
        "contact": {"id": obs["contact_id"], "properties": props},
        "tz": obs.get("timezone", "UNKNOWN"),
        "script": script,
        "email_data": email_data,
    })
    return True, "prep note written"


def act_route_contact(ctx, decision):
    """Record a dispositioned contact's outcome in HubSpot.

    Campaign enrollment was removed, so routing is log-only: append the
    journey log and clear the pending action. No external sequence system
    is called.
    """
    obs = decision["obs"]
    route = decision["route"]
    cid = obs["contact_id"]
    disposition = decision.get("disposition", "")

    action = route.get("action")
    if action == "advance":
        detail = "disposition {} logged".format(disposition or action)
    elif action == "retry":
        # Retry keeps the contact in place; we only re-stamp the journey so the
        # next run knows when the retry window opened.
        detail = "retry scheduled in {}h".format(route.get("delay_hours", 0))
    else:
        return False, "executor does not handle action {!r}".format(action)

    update = {"oracle_pending_action": "done"}
    # Compliance guard: do_not_call must always reach the standard HubSpot
    # property, on whichever path executes it.
    if disposition == "do_not_call":
        update["donotcall"] = "true"

    ctx.hubspot.append_journey_log(cid, route.get("log_entry", detail))
    ctx.hubspot.update_contact_properties(cid, update)
    return True, detail


def act_post_call_sheet(ctx, decision):
    """Build the timezone-ordered sheet and post it to Slack."""
    if not ctx.prepped:
        return False, "nothing prepped, no sheet to post"

    blocks, unknowns = call_sheet_service.build_call_sheet(ctx.prepped)
    session_data = {
        "segment": ctx.list_name,
        "calling_date": ctx.calling_date,
        "blocks": blocks,
        "unknowns": unknowns,
        "contacts": ctx.prepped,
    }
    slack.post_to_slack(session_data)
    return True, "call sheet posted for {} contacts".format(len(ctx.prepped))


EXECUTORS = {
    "prep_contact": act_prep_contact,
    "route_contact": act_route_contact,
    "post_call_sheet": act_post_call_sheet,
}


def execute(ctx, decision):
    """Run one decision. Returns (ok, detail). Never raises."""
    executor = EXECUTORS.get(decision["action"])
    if executor is None:
        return False, "no executor for {!r}".format(decision["action"])
    try:
        return executor(ctx, decision)
    except Exception as exc:
        _log.exception("action %s failed", decision["action"])
        return False, "{}: {}".format(type(exc).__name__, exc)
