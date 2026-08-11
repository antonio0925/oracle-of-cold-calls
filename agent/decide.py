"""
DECIDE — pure policy. Observations in, planned actions out. No I/O.

Being pure makes the agent's judgement fully testable and makes --dry-run
truthful: the plan you see is exactly the plan that would execute.

Actions the agent can plan:
  prep_contact   generate Octave call content + write a HubSpot prep note
  route_contact  record a dispositioned contact's outcome in HubSpot
  post_call_sheet publish the timezone-ordered dial sheet to Slack
  skip           explicit no-op, with a reason (recorded, never silent)
"""
from services import routing_config

# Dispositions the agent will act on without a human in the loop.
# Anything destructive or irreversible is escalated instead.
AUTONOMOUS_ACTIONS = {"advance", "retry"}
ESCALATE_ACTIONS = {"remove", "transfer", "finish"}


def decide_prep(obs, ledger_has, max_preps=None, planned_count=0):
    """Decide whether to prep one contact. Returns a decision dict."""
    cid = obs["contact_id"]

    def skip(reason):
        return {"action": "skip", "contact_id": cid, "reason": reason, "obs": obs}

    if not obs.get("email"):
        return skip("no email address on record")
    if obs.get("has_prep_note"):
        return skip("prep note already exists")
    if ledger_has("prep_contact", cid):
        return skip("already prepped in this ledger day")
    if not obs.get("email_data"):
        return skip("no logged outbound email — Oracle needs one as source material")
    if not (obs.get("phone") or "").strip():
        return skip("no phone number — not dialable")
    if max_preps is not None and planned_count >= max_preps:
        return skip("run budget of {} preps reached".format(max_preps))

    return {"action": "prep_contact", "contact_id": cid, "reason": "dialable, emailed, unprepped", "obs": obs}


def decide_route(obs, ledger_has, allow_escalated=False):
    """Decide how to route one dispositioned contact."""
    cid = obs["contact_id"]
    disposition = (obs.get("disposition") or "").strip()

    def skip(reason):
        return {"action": "skip", "contact_id": cid, "reason": reason, "obs": obs}

    if not disposition:
        return skip("pending action but no disposition logged")
    if ledger_has("route_contact", cid):
        return skip("already routed in this ledger day")

    route = routing_config.get_route(disposition)
    if route is None:
        return skip("unknown disposition {!r}".format(disposition))

    act = route.get("action")
    if act in ESCALATE_ACTIONS and not allow_escalated:
        return {
            "action": "escalate",
            "contact_id": cid,
            "reason": "{} requires human sign-off ({})".format(disposition, act),
            "route": route,
            "obs": obs,
        }
    if act not in AUTONOMOUS_ACTIONS:
        return skip("action {!r} not in the autonomous set".format(act))

    return {
        "action": "route_contact",
        "contact_id": cid,
        "reason": "disposition {} -> {}".format(disposition, act),
        "route": route,
        "disposition": disposition,
        "obs": obs,
    }


def build_plan(dial_obs, routing_obs, ledger_has, max_preps=None, allow_escalated=False, post_sheet=True):
    """Turn observations into an ordered, deduplicated action plan."""
    plan = []
    prep_count = 0

    for obs in dial_obs:
        decision = decide_prep(obs, ledger_has, max_preps=max_preps, planned_count=prep_count)
        if decision["action"] == "prep_contact":
            prep_count += 1
        plan.append(decision)

    for obs in routing_obs:
        plan.append(decide_route(obs, ledger_has, allow_escalated=allow_escalated))

    if post_sheet and prep_count > 0:
        plan.append({
            "action": "post_call_sheet",
            "contact_id": None,
            "reason": "{} contacts prepped this run".format(prep_count),
        })

    return plan


def plan_counts(plan):
    counts = {}
    for step in plan:
        counts[step["action"]] = counts.get(step["action"], 0) + 1
    return counts
