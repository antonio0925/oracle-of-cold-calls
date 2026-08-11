"""Tests for the autonomous agent: policy, idempotency, and the full loop."""
import os
import tempfile

import pytest

import agent.state as state


@pytest.fixture(autouse=True)
def isolated_ledger(monkeypatch):
    tmp = tempfile.mkdtemp()
    monkeypatch.setattr(state, "STATE_DIR", tmp)
    yield tmp


def make_obs(cid="1", **over):
    obs = {
        "contact_id": cid,
        "email": "a@b.com",
        "name": "A B",
        "company": "Acme",
        "jobtitle": "VP Sales",
        "phone": "+15551234567",
        "properties": {"firstname": "A", "email": "a@b.com"},
        "timezone": "US/Pacific",
        "has_prep_note": False,
        "email_data": {"subject": "hi", "body_text": "body"},
        "disposition": "",
        "pending_action": "",
        "errors": [],
    }
    obs.update(over)
    return obs


NEVER = lambda action, cid: False


# --- policy: prep ---------------------------------------------------------

def test_prepable_contact_is_prepped():
    from agent.decide import decide_prep
    d = decide_prep(make_obs(), NEVER)
    assert d["action"] == "prep_contact"


@pytest.mark.parametrize("override,fragment", [
    ({"email": ""}, "no email"),
    ({"has_prep_note": True}, "already exists"),
    ({"email_data": None}, "no logged outbound email"),
    ({"phone": ""}, "no phone"),
])
def test_prep_skips_are_explained(override, fragment):
    from agent.decide import decide_prep
    d = decide_prep(make_obs(**override), NEVER)
    assert d["action"] == "skip"
    assert fragment in d["reason"]


def test_prep_respects_run_budget():
    from agent.decide import decide_prep
    d = decide_prep(make_obs(), NEVER, max_preps=5, planned_count=5)
    assert d["action"] == "skip"
    assert "budget" in d["reason"]


# --- policy: routing ------------------------------------------------------

def test_autonomous_disposition_is_routed():
    from agent.decide import decide_route
    d = decide_route(make_obs(disposition="no_answer"), NEVER)
    assert d["action"] == "route_contact"
    assert d["route"]["action"] == "retry"


def test_destructive_disposition_escalates():
    from agent.decide import decide_route
    d = decide_route(make_obs(disposition="do_not_call"), NEVER)
    assert d["action"] == "escalate"


def test_escalation_can_be_overridden():
    from agent.decide import decide_route
    d = decide_route(make_obs(disposition="meeting_booked"), NEVER, allow_escalated=True)
    assert d["action"] == "skip"  # transfer is not in the autonomous executor set


def test_unknown_disposition_is_skipped_not_guessed():
    from agent.decide import decide_route
    d = decide_route(make_obs(disposition="banana"), NEVER)
    assert d["action"] == "skip" and "unknown disposition" in d["reason"]


# --- ledger / idempotency -------------------------------------------------

def test_ledger_blocks_repeat_action():
    state.record("acted", contact_id="7", action="prep_contact", ok=True)
    assert state.already_done("prep_contact", "7") is True
    assert state.already_done("prep_contact", "8") is False


def test_failed_action_is_not_treated_as_done():
    state.record("acted", contact_id="9", action="prep_contact", ok=False, error="boom")
    assert state.already_done("prep_contact", "9") is False


def test_second_run_skips_already_prepped():
    from agent.decide import build_plan
    state.record("acted", contact_id="1", action="prep_contact", ok=True)
    plan = build_plan([make_obs("1")], [], state.already_done)
    assert plan[0]["action"] == "skip"
    assert "already prepped" in plan[0]["reason"]


def test_summarize_counts_outcomes():
    state.record("observed", contact_id="1")
    state.record("acted", contact_id="1", action="prep_contact", ok=True)
    state.record("acted", contact_id="2", action="prep_contact", ok=False, error="x")
    s = state.summarize()
    assert s["observed"] == 1 and s["acted_ok"] == 1 and s["acted_failed"] == 1
    assert s["errors"][0]["contact_id"] == "2"


# --- plan assembly --------------------------------------------------------

def test_plan_adds_call_sheet_only_when_work_happened():
    from agent.decide import build_plan, plan_counts
    plan = build_plan([make_obs("1"), make_obs("2")], [], NEVER)
    assert plan_counts(plan)["post_call_sheet"] == 1

    empty = build_plan([make_obs("3", phone="")], [], NEVER)
    assert "post_call_sheet" not in plan_counts(empty)


# --- shared octave script extraction ------------------------------------

@pytest.mark.parametrize("payload,expected", [
    ({"content": "body"}, "body"),
    ({"text": "body"}, "body"),
    ("raw string", "raw string"),
    (None, ""),
])
def test_script_text_normalizes_octave_payloads(payload, expected):
    from services.octave import script_text
    assert script_text(payload) == expected


def test_script_text_falls_back_to_json_for_unknown_shape():
    from services.octave import script_text
    assert script_text({"weird": 1}) == '{"weird": 1}'


# --- list resolution (real HubSpot contract) -----------------------------

def test_resolve_list_id_accepts_numeric_id_without_search():
    from agent.perceive import resolve_list_id

    class NoSearch(object):
        def search_lists(self, name):
            raise AssertionError("should not search when given a numeric ID")

    assert resolve_list_id(NoSearch(), "302") == "302"


def test_resolve_list_id_resolves_exact_name():
    from agent.perceive import resolve_list_id

    class HS(object):
        def search_lists(self, name):
            return "302" if name == "Real List" else None

    assert resolve_list_id(HS(), "Real List") == "302"


def test_resolve_list_id_raises_actionable_error_on_miss():
    from agent.perceive import resolve_list_id
    import pytest as _pytest

    class HS(object):
        def search_lists(self, name):
            return None

    with _pytest.raises(LookupError) as err:
        resolve_list_id(HS(), "Nope")
    assert "exact" in str(err.value).lower()


# --- full loop against fakes ---------------------------------------------

class FakeHubSpot(object):
    def __init__(self):
        self.notes = []
        self.props_updated = []
        self.journey = []

    def search_lists(self, name):
        # Real contract (services/hubspot.py): returns the list ID string on an
        # exact name match, or None. NOT a list of dicts.
        return "42" if name == "Dial" else None

    def get_list_memberships(self, list_id):
        return ["1", "2"]

    def batch_get_contacts(self, ids, properties):
        return [
            {"id": "1", "properties": {"firstname": "Ann", "email": "ann@acme.com",
                                       "phone": "+15550001111", "jobtitle": "VP Sales",
                                       "state": "CA", "company": "Acme"}},
            {"id": "2", "properties": {"firstname": "Bob", "email": "bob@beta.com",
                                       "jobtitle": "Director", "company": "Beta"}},  # no phone
        ]

    def search_emails_for_contact(self, cid):
        return {"subject": "s", "body_text": "b", "body_html": "<p>b</p>"}

    def get_all_prep_notes_for_contact(self, cid):
        return []

    def get_pending_actions(self):
        return []

    def create_note_for_contact(self, cid, html):
        self.notes.append((cid, html))
        return {"id": "note-" + cid}

    def append_journey_log(self, cid, entry):
        self.journey.append((cid, entry))

    def update_contact_properties(self, cid, props):
        self.props_updated.append((cid, props))


class FakeOctave(object):
    def generate_call_script(self, person, subject, body):
        return {"content": "VOICEMAIL: hi\n\nLIVE CALL: hello"}


def _patch_clients(monkeypatch, hs, octave, posted):
    import agent.loop as loop_mod
    import agent.act as act_mod
    monkeypatch.setattr(loop_mod, "HubSpotClient", lambda *a, **k: hs)
    monkeypatch.setattr(loop_mod, "OctaveClient", lambda *a, **k: octave)
    monkeypatch.setattr(act_mod.slack, "post_to_slack", lambda data: posted.append(data))


def test_dry_run_writes_nothing(monkeypatch):
    from agent.loop import run_once
    hs, posted = FakeHubSpot(), []
    _patch_clients(monkeypatch, hs, FakeOctave(), posted)

    report = run_once(campaign="Q3", list_name="Dial", dry_run=True)
    assert report["dry_run"] is True
    assert report["plan"]["prep_contact"] == 1   # Ann only; Bob has no phone
    assert hs.notes == [] and posted == []


def test_live_run_writes_note_and_posts_sheet(monkeypatch):
    from agent.loop import run_once
    hs, posted = FakeHubSpot(), []
    _patch_clients(monkeypatch, hs, FakeOctave(), posted)

    report = run_once(campaign="Q3", list_name="Dial")
    assert [r["ok"] for r in report["results"]] == [True, True]
    assert len(hs.notes) == 1 and hs.notes[0][0] == "1"
    assert len(posted) == 1


def test_rerun_is_idempotent(monkeypatch):
    from agent.loop import run_once
    hs, posted = FakeHubSpot(), []
    _patch_clients(monkeypatch, hs, FakeOctave(), posted)

    run_once(campaign="Q3", list_name="Dial")
    run_once(campaign="Q3", list_name="Dial")
    assert len(hs.notes) == 1, "second run must not re-write the prep note"


def test_octave_failure_does_not_kill_the_run(monkeypatch):
    from agent.loop import run_once

    class Boom(object):
        def generate_call_script(self, *a, **k):
            raise RuntimeError("octave 500")

    hs, posted = FakeHubSpot(), []
    _patch_clients(monkeypatch, hs, Boom(), posted)

    report = run_once(campaign="Q3", list_name="Dial")
    prep = [r for r in report["results"] if r["action"] == "prep_contact"][0]
    assert prep["ok"] is False and "octave 500" in prep["detail"]
    assert state.summarize()["acted_failed"] >= 1
