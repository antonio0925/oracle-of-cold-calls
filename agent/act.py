"""
ACT — the only module allowed to cause side effects.

Every executor takes a decision dict and returns (ok, detail). Nothing here
raises into the loop: a failed contact is logged and the run continues.
"""
import json
import logging

from services import formatting, call_sheet as call_sheet_service, slack

_log = logging.getLogger(__name__)


def _script_text(script_data):
    """Normalize Octave's response into the plain text the formatter expects.

    Mirrors app.py: the agent returns a dict, and the note formatter needs the
    string body out of it.
    """
    if isinstance(script_data, str):
        return script_data
    if isinstance(script_data, dict):
        text = script_data.get("content") or script_data.get("text")
        if text:
            return text
        return json.dumps(script_data)
    return ""


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
    script = _script_text(script_data)
    if not script.strip():
        return False, "Octave returned empty content"

    html = formatting.format_note_html(props, ctx.campaign, script)
    ctx.hubspot.create_note_for_contact(obs["contact_id"], html)

    ctx.prepped.append({
        "contact": {"id": obs["contact_id"], "properties": props},
        "tz": obs.get("timezone", "UNKNOWN"),
        "script": script,
        "email_data": email_data,
    })
    return True, "prep note written"


def act_route_contact(ctx, decision):
    """Push a dispositioned contact through its Supersend route."""
    obs = decision["obs"]
    route = decision["route"]
    props = obs["properties"]
    cid = obs["contact_id"]

    ss_contact_id = props.get("oracle_supersend_contact_id")
    campaign_id = props.get("oracle_campaign_id")
    if not ss_contact_id or not campaign_id:
        return False, "missing supersend contact/campaign id on the record"

    action = route.get("action")
    if action == "advance":
        step = route.get("next_step")
        if step is None:
            raw = props.get("oracle_step_number")
            try:
                step = int(raw) + 1
            except (TypeError, ValueError):
                return False, "cannot infer next step from oracle_step_number={!r}".format(raw)
        ctx.supersend.assign_step(ss_contact_id, campaign_id, step, props.get("oracle_node_id"))
        detail = "advanced to step {}".format(step)
    elif action == "retry":
        # Retry keeps the contact in place; we only re-stamp the journey so the
        # next run knows when the retry window opened.
        detail = "retry scheduled in {}h".format(route.get("delay_hours", 0))
    else:
        return False, "executor does not handle action {!r}".format(action)

    ctx.hubspot.append_journey_log(cid, route.get("log_entry", detail))
    ctx.hubspot.update_contact_properties(cid, {"oracle_pending_action": "done"})
    return True, detail


def act_post_call_sheet(ctx, decision):
    """Build the timezone-ordered sheet and post it to Slack."""
    if not ctx.prepped:
        return False, "nothing prepped, no sheet to post"

    blocks, unknowns = call_sheet_service.build_call_sheet(ctx.prepped)
    session_data = {
        "campaign": ctx.campaign,
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
