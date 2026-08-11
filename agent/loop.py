"""
The autonomous loop: perceive -> decide -> act -> record -> report.

This is what turns the Oracle from an app you click into an agent that runs.
A single run is idempotent (ledger-guarded), bounded (--max-preps), and
honest (--dry-run prints the exact plan it would execute).
"""
import logging
from datetime import datetime

import config
from services.hubspot import HubSpotClient
from services.octave import OctaveClient

from agent import act as act_module
from agent import decide as decide_module
from agent import perceive as perceive_module
from agent import state

_log = logging.getLogger(__name__)


class RunContext(object):
    """Everything an executor needs, assembled once per run."""

    def __init__(self, list_name, calling_date=None, dry_run=False):
        self.list_name = list_name
        self.calling_date = calling_date or datetime.now().strftime("%Y-%m-%d")
        self.dry_run = dry_run
        self.hubspot = HubSpotClient(config.HUBSPOT_ACCESS_TOKEN)
        self.octave = OctaveClient(config.OCTAVE_API_KEY)
        self.prepped = []


def run_once(list_name=None, max_preps=None, dry_run=False,
             allow_escalated=False, post_sheet=True, limit=None):
    """Execute one full agent cycle. Returns a report dict."""
    ctx = RunContext(list_name, dry_run=dry_run)

    state.record("run_started", list_name=list_name,
                 dry_run=dry_run, max_preps=max_preps)

    # --- PERCEIVE ---
    dial_obs, routing_obs = perceive_module.perceive(ctx.hubspot, list_name, limit=limit)
    for obs in dial_obs + routing_obs:
        state.record("observed", contact_id=obs["contact_id"], email=obs["email"],
                     has_prep_note=obs["has_prep_note"], tz=obs["timezone"])

    # --- DECIDE ---
    plan = decide_module.build_plan(
        dial_obs, routing_obs,
        ledger_has=state.already_done,
        max_preps=max_preps,
        allow_escalated=allow_escalated,
        post_sheet=post_sheet,
    )
    for step in plan:
        event = "skipped" if step["action"] in ("skip", "escalate") else "decided"
        state.record(event, contact_id=step.get("contact_id"),
                     action=step["action"], reason=step.get("reason"))

    report = {
        "list_name": list_name,
        "dry_run": dry_run,
        "observed": len(dial_obs) + len(routing_obs),
        "plan": decide_module.plan_counts(plan),
        "results": [],
        "escalations": [s for s in plan if s["action"] == "escalate"],
    }

    if dry_run:
        report["results"] = [
            {"action": s["action"], "contact_id": s.get("contact_id"), "reason": s.get("reason")}
            for s in plan
        ]
        state.record("run_finished", dry_run=True, **report["plan"])
        return report

    # --- ACT ---
    for step in plan:
        if step["action"] in ("skip", "escalate"):
            continue
        ok, detail = act_module.execute(ctx, step)
        state.record("acted", contact_id=step.get("contact_id"),
                     action=step["action"], ok=ok,
                     detail=detail if ok else None,
                     error=None if ok else detail)
        report["results"].append({
            "action": step["action"], "contact_id": step.get("contact_id"),
            "ok": ok, "detail": detail,
        })

    report["summary"] = state.summarize()
    state.record("run_finished", dry_run=False, **report["plan"])
    return report


def format_report(report):
    """Human-readable run report for the terminal or a Slack post."""
    lines = []
    mode = "DRY RUN" if report["dry_run"] else "LIVE"
    lines.append("SUMMIT agent run [{}] list={}".format(
        mode, report["list_name"]))
    lines.append("Observed {} contacts".format(report["observed"]))
    lines.append("Plan: " + (", ".join(
        "{}={}".format(k, v) for k, v in sorted(report["plan"].items())) or "nothing to do"))

    if report["dry_run"]:
        for r in report["results"]:
            if r["action"] != "skip":
                lines.append("  would {} {} — {}".format(
                    r["action"], r["contact_id"] or "", r["reason"]))
    else:
        ok = sum(1 for r in report["results"] if r["ok"])
        fail = len(report["results"]) - ok
        lines.append("Executed: {} ok, {} failed".format(ok, fail))
        for r in report["results"]:
            if not r["ok"]:
                lines.append("  FAILED {} {} — {}".format(r["action"], r["contact_id"], r["detail"]))

    for esc in report["escalations"]:
        lines.append("  ESCALATE {} — {}".format(esc["contact_id"], esc["reason"]))

    return "\n".join(lines)
