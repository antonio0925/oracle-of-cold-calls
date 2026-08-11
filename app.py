"""
SUMMIT: Flask routes only.

All business logic lives in services/. This file is routes + SSE generators.
"""
import json
import time
import re
import uuid
import hmac
import secrets
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import (
    Flask, render_template, request, Response, jsonify, session, redirect,
)
import requests as http_requests

import logging
import config
from services.sessions import (
    get_session, set_session, delete_session, delete_session_file,
    save_session_to_disk, load_session_from_disk, find_resumable_session,
    find_latest_session, record_disposition, utc_now_iso,
)
from services.timezone import resolve_timezone, tz_label
from services.filters import is_us_company, is_us_person
from services.formatting import format_note_html, normalize_html_for_compare
from services.call_sheet import title_seniority, TIME_BLOCKS, TZ_TO_BLOCKS, build_call_sheet
from services.hubspot import HubSpotClient
from services.octave import OctaveClient, script_text as octave_script_text
from services.slack import post_to_slack
from services.routing_config import get_route, list_dispositions

app = Flask(__name__)
log = logging.getLogger(__name__)

# Signs the session cookie. If FLASK_SECRET_KEY is unset we generate a random
# key at boot: sessions do not survive a restart, which is inconvenient but
# never insecure. A hardcoded fallback would be forgeable.
app.secret_key = config.FLASK_SECRET_KEY or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # Secure cookies need HTTPS. The deploy platform terminates TLS; local
    # development over plain http would never receive the cookie back.
    SESSION_COOKIE_SECURE=not config.FLASK_DEBUG,
)

# Shared thread pool for all SSE generators — bounds total concurrency and
# prevents zombie pools when clients disconnect mid-stream.
_pool = ThreadPoolExecutor(max_workers=8)


def _cancel_futures(futures):
    """Cancel pending futures when an SSE generator is interrupted."""
    cancelled = sum(1 for f in futures if f.cancel())
    if cancelled:
        log.info("Cancelled %d pending futures", cancelled)


# ---------------------------------------------------------------------------
# Auth — shared password gate
# ---------------------------------------------------------------------------
# Every UI route writes to production HubSpot, so the whole app is closed by
# default and opened path by path.
_PUBLIC_PREFIXES = ("/static/",)
_PUBLIC_PATHS = ("/login", "/healthz")


def _is_public(path):
    return path in _PUBLIC_PATHS or path.startswith(_PUBLIC_PREFIXES)


@app.before_request
def _require_login():
    if _is_public(request.path) or session.get("summit_auth"):
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not authenticated"}), 401
    return redirect("/login")


@app.route("/login", methods=["GET", "POST"])
def login():
    """Shared-password sign-in. The BDR gets a URL and this one password."""
    if request.method == "GET":
        if session.get("summit_auth"):
            return redirect("/")
        return render_template("login.html")

    # An unset password must fail closed. Otherwise an empty form field would
    # authenticate and the app would be open to anyone who finds the URL.
    if not config.SUMMIT_PASSWORD:
        log.error("Login attempted but SUMMIT_PASSWORD is not set")
        return render_template(
            "login.html",
            error="No password is configured on the server. Tell Antonio.",
        ), 503

    supplied = request.form.get("password", "")
    if not hmac.compare_digest(supplied, config.SUMMIT_PASSWORD):
        log.warning("Failed login attempt from %s", request.remote_addr)
        return render_template("login.html", error="Wrong password."), 401

    session["summit_auth"] = True
    session.permanent = True
    return redirect("/")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/healthz")
def healthz():
    """Unauthenticated liveness probe for the deploy platform."""
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Flask Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html", hubspot_portal_id=config.HUBSPOT_PORTAL_ID)


@app.route("/api/lists")
def api_lists():
    """Return all HubSpot lists created by the configured creator for the dropdown."""
    if not config.HUBSPOT_ACCESS_TOKEN:
        return jsonify({"error": "Missing HubSpot token"}), 500
    hs = HubSpotClient(config.HUBSPOT_ACCESS_TOKEN)
    all_lists = []
    offset = 0
    while True:
        try:
            data = hs._post("/crm/v3/lists/search", {"query": "", "offset": offset})
            for lst in data.get("lists", []):
                if lst.get("createdById") == config.HUBSPOT_CREATOR_ID:
                    size = lst.get("additionalProperties", {}).get("hs_list_size", "0")
                    all_lists.append({
                        "listId": lst["listId"],
                        "name": lst["name"],
                        "size": int(size) if size else 0,
                        "type": lst.get("processingType", ""),
                    })
            if not data.get("hasMore"):
                break
            offset = data.get("offset", offset + 20)
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    all_lists.sort(key=lambda x: x["name"])
    return jsonify({"lists": all_lists})


@app.route("/api/session/<session_id>")
def api_session(session_id):
    """Fetch full session data for review."""
    session_data = get_session(session_id) or load_session_from_disk(session_id)
    if not session_data:
        return jsonify({"error": "Session not found"}), 404
    return jsonify(session_data)


@app.route("/api/recoverable-sessions")
def api_recoverable_sessions():
    """List session files that can be resumed."""
    import os
    sessions_dir = "sessions"
    if not os.path.isdir(sessions_dir):
        return jsonify({"sessions": []})
    results = []
    for fname in sorted(os.listdir(sessions_dir), reverse=True):
        if not fname.startswith("prep_") or not fname.endswith(".json"):
            continue
        path = os.path.join(sessions_dir, fname)
        try:
            with open(path) as f:
                data = json.load(f)
            if data.get("contacts"):
                results.append({
                    "session_id": data.get("session_id", ""),
                    "segment": data.get("segment", ""),
                    "calling_date": data.get("calling_date", ""),
                    "prepped_count": len(data.get("contacts", [])),
                    "is_complete": data.get("generation_complete", False),
                    "modified": datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M"),
                })
        except Exception:
            continue
    return jsonify({"sessions": results[:20]})


def _clamp_target(raw):
    """Resolve the requested daily call target.

    Absent means the default. Present but out of range clamps. These are
    separate on purpose: `int(raw or DEFAULT)` treats 0 as absent, so a
    target of 0 silently became 50 while -5 clamped to 1.
    """
    if raw is None or raw == "":
        return config.DEFAULT_CALL_TARGET
    try:
        target = int(raw)
    except (TypeError, ValueError):
        return config.DEFAULT_CALL_TARGET
    return max(1, min(target, config.MAX_CALL_TARGET))


@app.route("/generate", methods=["POST"])
def generate():
    """SSE endpoint: build today's call list, streaming progress as it goes."""
    data = request.json
    segment_name = data.get("segment", "").strip()
    calling_date = data.get("calling_date", "").strip()
    skip_existing = data.get("skip_existing", False)

    # How many calls the BDR wants today. The loop stops once it has this
    # many, so a 400-contact list does not cost 400 Octave calls to work 50.
    target = _clamp_target(data.get("target"))

    if not segment_name:
        return jsonify({"error": "A call list is required"}), 400

    if not config.HUBSPOT_ACCESS_TOKEN or not config.OCTAVE_API_KEY:
        return jsonify({"error": "Missing API credentials in .env"}), 500

    hs = HubSpotClient(config.HUBSPOT_ACCESS_TOKEN)
    octave = OctaveClient(config.OCTAVE_API_KEY)

    # Check for a resumable session
    prev_session_id, prev_session = find_resumable_session(segment_name, calling_date)
    if prev_session:
        session_id = prev_session_id or str(uuid.uuid4())[:8]
    else:
        session_id = str(uuid.uuid4())[:8]

    def stream():
        stats = {
            "total": 0, "prepped": 0,
            "skipped_subscriber": 0, "no_source_email": 0,
            "skipped_existing": 0, "skipped_cached": 0,
            "skipped_recent_call": 0, "skipped_account_cap": 0,
            "errors": 0, "not_reached": 0,
            "tz_breakdown": {},
        }
        prepped_contacts = []

        # Call pacing state, rebuilt each run.
        #   per_account  how many contacts this company already has today
        #   last_calls   contact_id -> latest logged call timestamp
        per_account = defaultdict(int)
        last_calls = {}
        cooldown_cutoff = (
            datetime.now(timezone.utc) - timedelta(days=config.CALL_COOLDOWN_DAYS)
        ).strftime("%Y-%m-%d")

        # Build cache from previous session
        cached_scripts = {}
        if prev_session and prev_session.get("contacts"):
            for c in prev_session["contacts"]:
                if c.get("script_content"):
                    cached_scripts[str(c["contact_id"])] = c

        def emit(msg_type, payload):
            return f"data: {json.dumps({'type': msg_type, **payload})}\n\n"

        def _save_progress():
            contacts_payload = [{
                "contact_id": c["contact"]["id"],
                "name": f"{c['contact'].get('properties', {}).get('firstname', '')} {c['contact'].get('properties', {}).get('lastname', '')}".strip(),
                "company": c["contact"].get("properties", {}).get("company", ""),
                "note_html": c["note_html"],
                "script_content": c["script_content"],
                "tz": c["tz_label"],
            } for c in prepped_contacts]
            # Preserve cached scripts the loop has not reached yet — writing
            # only processed contacts destroys them if this run dies early.
            done_ids = {str(c["contact"]["id"]) for c in prepped_contacts}
            for cached_id, cached_contact in cached_scripts.items():
                if cached_id not in done_ids:
                    contacts_payload.append(cached_contact)
            partial_data = {
                "session_id": session_id,
                "segment": segment_name,
                "calling_date": calling_date,
                "stats": stats,
                "generation_complete": False,
                "contacts": contacts_payload,
            }
            save_session_to_disk(session_id, partial_data)

        # Phase 1: Pull contacts
        if cached_scripts:
            yield emit("status", {
                "msg": f"Found {len(cached_scripts)} scripts cached from an earlier run on this list. "
                       f"Only new warriors will be consulted..."
            })
        else:
            yield emit("status", {"msg": "Finding your list in HubSpot..."})

        list_id = hs.search_lists(segment_name)
        if not list_id:
            yield emit("error", {"msg": f"List '{segment_name}' not found in HubSpot."})
            yield emit("done", {"session_id": None})
            return

        yield emit("status", {"msg": f"List found (ID {list_id}). Loading the contacts..."})

        try:
            contact_ids = hs.get_list_memberships(list_id)
        except Exception as e:
            yield emit("error", {"msg": f"Could not load the list members: {e}"})
            yield emit("done", {"session_id": None, "stats": stats})
            return
        stats["total"] = len(contact_ids)
        yield emit("status", {"msg": f"{len(contact_ids)} contacts on the list. Checking who is dialable today..."})

        if not contact_ids:
            yield emit("done", {"session_id": None, "stats": stats})
            return

        # Contact details are fetched a chunk at a time, as the loop consumes
        # them. Reading all of them up front cost one request per 100 contacts
        # before any work started: on a 3,400-contact list that is 34 requests
        # the BDR waits through to make two calls. The loop stops at the
        # target, so this now costs roughly one request per 100 contacts
        # actually looked at.
        CONTACT_PROPERTIES = [
            "firstname", "lastname", "email", "company", "jobtitle",
            "phone", "mobilephone", "city", "state", "country", "hs_timezone",
        ]
        CHUNK = 100

        def contacts_in_chunks():
            for start in range(0, len(contact_ids), CHUNK):
                batch = contact_ids[start:start + CHUNK]
                try:
                    for c in hs.batch_get_contacts(batch, CONTACT_PROPERTIES):
                        yield c
                except Exception as e:
                    log.warning("Contact chunk at %d failed: %s", start, e)
                    stats["errors"] += 1

        # Call history is read per contact, on demand, and cached. Sweeping the
        # whole list up front cost one or two requests for every contact on it,
        # which is most of the wait on a large list when the BDR only wants a
        # few dozen calls. The loop stops at the target, so this now scales
        # with the target instead of the list.
        def last_call_for(cid):
            key = str(cid)
            if key not in last_calls:
                try:
                    last_calls.update(hs.last_call_dates([cid]))
                except Exception as e:
                    log.warning("Call history lookup failed for %s: %s", cid, e)
                    last_calls[key] = ""
            return last_calls.get(key, "")

        # Phase 2: Filter + generate, stopping at the target
        for i, contact in enumerate(contacts_in_chunks()):
            if stats["prepped"] >= target:
                stats["not_reached"] = len(contact_ids) - i
                yield emit("status", {
                    "msg": f"Target of {target} reached. "
                           f"{stats['not_reached']} contacts left untouched for another day.",
                })
                break

            cid = contact["id"]
            props = contact.get("properties", {})
            name = f"{props.get('firstname', '')} {props.get('lastname', '')}".strip() or f"Contact {cid}"
            company_name = props.get("company", "Unknown")

            # Progress tracks the target, because that is the finish line the
            # BDR asked for. Showing progress against the full list would crawl
            # to 12% and stop, which reads as a hang.
            yield emit("progress", {
                "current": stats["prepped"] + 1,
                "total": target,
                "scanned": i + 1,
                "scanned_total": len(contact_ids),
                "name": name,
            })

            # Filter A: Active subscriber check
            try:
                company_ids = hs.get_associated_companies(cid)
                is_subscriber = False
                for comp_id in company_ids:
                    comp_props = hs.get_company_properties(comp_id, [
                        "subscription_status", "mrr_from_subscription"
                    ])
                    sub_status = (comp_props.get("subscription_status") or "").upper()
                    mrr_str = comp_props.get("mrr_from_subscription") or "0"
                    try:
                        mrr = float(mrr_str)
                    except (ValueError, TypeError):
                        mrr = 0
                    if sub_status == "ACTIVE" and mrr > 0:
                        is_subscriber = True
                        stats["skipped_subscriber"] += 1
                        yield emit("skip", {
                            "name": name,
                            "reason": f"Already a customer (${mrr:.0f}/mo)"
                        })
                        break
                if is_subscriber:
                    continue
            except Exception as e:
                yield emit("warn", {"name": name, "msg": f"Could not check subscription: {e}"})

            # Filter B: Prior outbound email is source material, not a gate.
            # A segment of cold contacts must still produce a call list, so a
            # contact with no logged email gets a script from their profile.
            try:
                email_data = hs.search_emails_for_contact(cid)
            except Exception as e:
                yield emit("warn", {"name": name, "msg": f"Email lookup failed, using profile only: {e}"})
                email_data = None
            if not email_data:
                stats["no_source_email"] += 1
                email_data = {"subject": "", "body_html": "", "body_text": ""}

            # Filter C: Existing prep check
            if skip_existing:
                has_prep = hs.search_notes_for_contact(cid)
                if has_prep:
                    stats["skipped_existing"] += 1
                    yield emit("skip", {"name": name, "reason": "Prep note already written"})
                    continue

            # Filter D: call cooldown. A contact called inside the cooldown
            # window rests. A voicemail counts as a call.
            last_call = last_call_for(cid)
            if last_call and last_call >= cooldown_cutoff:
                stats["skipped_recent_call"] += 1
                yield emit("skip", {
                    "name": name,
                    "reason": f"Called {last_call[:10]}, inside the {config.CALL_COOLDOWN_DAYS}-day cooldown",
                })
                continue

            # Filter E: account cap. Never burn a whole account in one day.
            account_key = (props.get("company") or "").strip().lower() or f"contact:{cid}"
            if per_account[account_key] >= config.MAX_CONTACTS_PER_ACCOUNT_PER_DAY:
                stats["skipped_account_cap"] += 1
                yield emit("skip", {
                    "name": name,
                    "reason": f"{company_name} already has {config.MAX_CONTACTS_PER_ACCOUNT_PER_DAY} on today's list",
                })
                continue

            # Resume check — after the filters, so a cached script never
            # bypasses the subscriber, cooldown, account-cap, or prep gates
            if str(cid) in cached_scripts:
                cached = cached_scripts[str(cid)]
                tz = resolve_timezone(props)
                tz_lbl = tz_label(tz)
                stats["tz_breakdown"][tz_lbl] = stats["tz_breakdown"].get(tz_lbl, 0) + 1
                stats["skipped_cached"] += 1
                stats["prepped"] += 1
                per_account[account_key] += 1
                fresh_html = format_note_html(props, cached["script_content"])
                prepped_contacts.append({
                    "contact": contact,
                    "tz": tz,
                    "tz_label": tz_lbl,
                    "script_content": cached["script_content"],
                    "email_data": email_data,
                    "note_html": fresh_html,
                })
                yield emit("done_contact", {
                    "name": name, "company": company_name, "tz": tz_lbl,
                    "cached": True,
                })
                continue

            # Generate script via Octave
            yield emit("generating", {"name": name, "company": company_name})

            try:
                script_data = octave.generate_call_script(
                    props,
                    email_data["subject"],
                    email_data.get("body_html") or email_data.get("body_text", ""),
                )
                script_content = octave_script_text(script_data)

                tz = resolve_timezone(props)
                tz_lbl = tz_label(tz)
                stats["tz_breakdown"][tz_lbl] = stats["tz_breakdown"].get(tz_lbl, 0) + 1

                prepped_contacts.append({
                    "contact": contact,
                    "tz": tz,
                    "tz_label": tz_lbl,
                    "script_content": script_content,
                    "email_data": email_data,
                    "note_html": format_note_html(props, script_content),
                })
                stats["prepped"] += 1
                per_account[account_key] += 1
                yield emit("done_contact", {"name": name, "company": company_name, "tz": tz_lbl})

                try:
                    _save_progress()
                except Exception:
                    pass

            except http_requests.exceptions.Timeout:
                stats["errors"] += 1
                yield emit("error_contact", {
                    "name": name,
                    "msg": "Script generation timed out after 120s. Skipping this contact.",
                })
            except http_requests.exceptions.ConnectionError:
                stats["errors"] += 1
                yield emit("error_contact", {
                    "name": name,
                    "msg": "Lost the connection to Octave. Skipping this contact.",
                })
            except Exception as e:
                stats["errors"] += 1
                yield emit("error_contact", {"name": name, "msg": f"{str(e)}"})

            time.sleep(1)

        # Build call sheet
        blocks, unknowns = build_call_sheet(prepped_contacts)

        call_sheet = []
        for idx, block_info in enumerate(TIME_BLOCKS):
            block_contacts = []
            for item in blocks.get(idx, []):
                p = item["contact"].get("properties", {})
                block_contacts.append({
                    "name": f"{p.get('firstname', '')} {p.get('lastname', '')}".strip(),
                    "title": p.get("jobtitle", ""),
                    "company": p.get("company", ""),
                    "tz": item["tz_label"],
                    "phone": p.get("phone", "") or p.get("mobilephone", ""),
                    "email": p.get("email", ""),
                    "contact_id": item["contact"]["id"],
                })
            call_sheet.append({
                "label": block_info[2],
                "color": block_info[3],
                "description": block_info[4],
                "local_time": block_info[5],
                "contacts": block_contacts,
            })

        unknown_contacts = []
        for item in unknowns:
            p = item["contact"].get("properties", {})
            unknown_contacts.append({
                "name": f"{p.get('firstname', '')} {p.get('lastname', '')}".strip(),
                "title": p.get("jobtitle", ""),
                "company": p.get("company", ""),
                "tz": "???",
                "phone": p.get("phone", "") or p.get("mobilephone", ""),
                "email": p.get("email", ""),
                "contact_id": item["contact"]["id"],
            })

        # Store final session
        session_data = {
            "session_id": session_id,
            "segment": segment_name,
            "calling_date": calling_date,
            "generation_complete": True,
            "stats": stats,
            "call_sheet": call_sheet,
            "unknown_tz": unknown_contacts,
            "contacts": [{
                "contact_id": c["contact"]["id"],
                "name": f"{c['contact'].get('properties', {}).get('firstname', '')} {c['contact'].get('properties', {}).get('lastname', '')}".strip(),
                "company": c["contact"].get("properties", {}).get("company", ""),
                "note_html": c["note_html"],
                "script_content": c["script_content"],
                "tz": c["tz_label"],
            } for c in prepped_contacts],
        }
        set_session(session_id, session_data)
        save_session_to_disk(session_id, session_data)

        cached_count = stats.get("skipped_cached", 0)
        new_count = stats["prepped"] - cached_count
        if cached_count > 0:
            completion_msg = (
                f"Route planned. {stats['prepped']} contacts are ready to call "
                f"({cached_count} reused from an earlier run, {new_count} newly written)."
            )
        else:
            completion_msg = f"Route planned. {stats['prepped']} contacts are ready to call."

        yield emit("complete", {
            "session_id": session_id,
            "stats": stats,
            "msg": completion_msg,
        })

    return Response(stream(), mimetype="text/event-stream")


@app.route("/quick-generate", methods=["POST"])
def quick_generate():
    """SSE endpoint: 'Prepare for Battle' — build call sheet from existing prep notes only.

    Skips all Octave enrichment. Only includes contacts that already have
    COLD CALL PREP notes logged in HubSpot. No approve step, no Slack posting.
    """
    data = request.json
    segment_name = data.get("segment", "").strip()
    calling_date = data.get("calling_date", "").strip()

    target = _clamp_target(data.get("target"))

    if not segment_name:
        return jsonify({"error": "A call list is required"}), 400

    if not config.HUBSPOT_ACCESS_TOKEN:
        return jsonify({"error": "Missing HUBSPOT_ACCESS_TOKEN in .env"}), 500

    hs = HubSpotClient(config.HUBSPOT_ACCESS_TOKEN)
    session_id = str(uuid.uuid4())[:8]

    def stream():
        stats = {
            "total": 0, "prepped": 0,
            "skipped_no_notes": 0, "errors": 0, "not_reached": 0,
            "tz_breakdown": {},
        }
        prepped_contacts = []

        def emit(msg_type, payload):
            return f"data: {json.dumps({'type': msg_type, **payload})}\n\n"

        yield emit("status", {"msg": "Finding your list in HubSpot..."})

        # Find the HubSpot list
        list_id = hs.search_lists(segment_name)
        if not list_id:
            yield emit("error", {"msg": f"List '{segment_name}' not found in HubSpot."})
            yield emit("done", {"session_id": None})
            return

        yield emit("status", {"msg": f"List found (ID {list_id}). Loading the contacts..."})

        try:
            contact_ids = hs.get_list_memberships(list_id)
        except Exception as e:
            yield emit("error", {"msg": f"Could not load the list members: {e}"})
            yield emit("done", {"session_id": None, "stats": stats})
            return
        stats["total"] = len(contact_ids)
        yield emit("status", {"msg": f"{len(contact_ids)} contacts on the list. Looking for existing prep notes..."})

        if not contact_ids:
            yield emit("done", {"session_id": None, "stats": stats})
            return

        # Contact details are fetched a chunk at a time, as the loop consumes
        # them. Reading all of them up front cost one request per 100 contacts
        # before any work started: on a 3,400-contact list that is 34 requests
        # the BDR waits through to make two calls. The loop stops at the
        # target, so this now costs roughly one request per 100 contacts
        # actually looked at.
        CONTACT_PROPERTIES = [
            "firstname", "lastname", "email", "company", "jobtitle",
            "phone", "mobilephone", "city", "state", "country", "hs_timezone",
        ]
        CHUNK = 100

        def contacts_in_chunks():
            for start in range(0, len(contact_ids), CHUNK):
                batch = contact_ids[start:start + CHUNK]
                try:
                    for c in hs.batch_get_contacts(batch, CONTACT_PROPERTIES):
                        yield c
                except Exception as e:
                    log.warning("Contact chunk at %d failed: %s", start, e)
                    stats["errors"] += 1

        # Check each contact for existing COLD CALL PREP notes
        for i, contact in enumerate(contacts_in_chunks()):
            if stats["prepped"] >= target:
                stats["not_reached"] = len(contact_ids) - i
                yield emit("status", {
                    "msg": f"Target of {target} reached. "
                           f"{stats['not_reached']} contacts left untouched for another day.",
                })
                break

            cid = contact["id"]
            props = contact.get("properties", {})
            name = f"{props.get('firstname', '')} {props.get('lastname', '')}".strip() or f"Contact {cid}"
            company_name = props.get("company", "Unknown")

            # Progress tracks the target, because that is the finish line the
            # BDR asked for. Showing progress against the full list would crawl
            # to 12% and stop, which reads as a hang.
            yield emit("progress", {
                "current": stats["prepped"] + 1,
                "total": target,
                "scanned": i + 1,
                "scanned_total": len(contact_ids),
                "name": name,
            })

            try:
                prep_notes = hs.get_all_prep_notes_for_contact(cid)
            except Exception as e:
                stats["errors"] += 1
                yield emit("error_contact", {"name": name, "msg": f"Note lookup failed: {e}"})
                continue

            if not prep_notes:
                stats["skipped_no_notes"] += 1
                yield emit("skip", {"name": name, "reason": "No prep note yet. Build the full list to write one."})
                continue

            # Use the most recent prep note
            latest_note = prep_notes[0]
            tz = resolve_timezone(props)
            tz_lbl = tz_label(tz)
            stats["tz_breakdown"][tz_lbl] = stats["tz_breakdown"].get(tz_lbl, 0) + 1
            stats["prepped"] += 1

            prepped_contacts.append({
                "contact": contact,
                "tz": tz,
                "tz_label": tz_lbl,
                "script_content": "",
                "email_data": {},
                "note_html": latest_note["body"],
            })

            yield emit("done_contact", {"name": name, "company": company_name, "tz": tz_lbl})

        if not prepped_contacts:
            yield emit("error", {"msg": "Nobody on this list has a prep note yet. Build the full list first."})
            yield emit("done", {"session_id": None, "stats": stats})
            return

        # Build call sheet
        blocks, unknowns = build_call_sheet(prepped_contacts)

        call_sheet = []
        for idx, block_info in enumerate(TIME_BLOCKS):
            block_contacts = []
            for item in blocks.get(idx, []):
                p = item["contact"].get("properties", {})
                block_contacts.append({
                    "name": f"{p.get('firstname', '')} {p.get('lastname', '')}".strip(),
                    "title": p.get("jobtitle", ""),
                    "company": p.get("company", ""),
                    "tz": item["tz_label"],
                    "phone": p.get("phone", "") or p.get("mobilephone", ""),
                    "email": p.get("email", ""),
                    "contact_id": item["contact"]["id"],
                })
            call_sheet.append({
                "label": block_info[2],
                "color": block_info[3],
                "description": block_info[4],
                "local_time": block_info[5],
                "contacts": block_contacts,
            })

        unknown_contacts = []
        for item in unknowns:
            p = item["contact"].get("properties", {})
            unknown_contacts.append({
                "name": f"{p.get('firstname', '')} {p.get('lastname', '')}".strip(),
                "title": p.get("jobtitle", ""),
                "company": p.get("company", ""),
                "tz": "???",
                "phone": p.get("phone", "") or p.get("mobilephone", ""),
                "email": p.get("email", ""),
                "contact_id": item["contact"]["id"],
            })

        # Store session
        session_data = {
            "session_id": session_id,
            "segment": segment_name,
            "calling_date": calling_date,
            "generation_complete": True,
            "quick_mode": True,
            "stats": stats,
            "call_sheet": call_sheet,
            "unknown_tz": unknown_contacts,
            "contacts": [{
                "contact_id": c["contact"]["id"],
                "name": f"{c['contact'].get('properties', {}).get('firstname', '')} {c['contact'].get('properties', {}).get('lastname', '')}".strip(),
                "company": c["contact"].get("properties", {}).get("company", ""),
                "note_html": c["note_html"],
                "script_content": c["script_content"],
                "tz": c["tz_label"],
            } for c in prepped_contacts],
        }
        set_session(session_id, session_data)
        save_session_to_disk(session_id, session_data)

        yield emit("complete", {
            "session_id": session_id,
            "stats": stats,
            "quick_mode": True,
            "msg": f"Route ready. {stats['prepped']} contacts already had prep notes. "
                   f"({stats['skipped_no_notes']} lack scrolls, {stats['errors']} errors.)",
        })

    return Response(stream(), mimetype="text/event-stream")


@app.route("/approve/<session_id>", methods=["POST"])
def approve(session_id):
    """SSE endpoint: writes all notes to HubSpot."""
    session_data = get_session(session_id) or load_session_from_disk(session_id)
    if not session_data:
        return jsonify({"error": "Session not found. The scrolls have been lost!"}), 404

    if not config.HUBSPOT_ACCESS_TOKEN:
        return jsonify({"error": "Missing HUBSPOT_ACCESS_TOKEN"}), 500

    hs = HubSpotClient(config.HUBSPOT_ACCESS_TOKEN)

    def stream():
        def emit(msg_type, data):
            return f"data: {json.dumps({'type': msg_type, **data})}\n\n"

        contacts = session_data.get("contacts", [])
        # On retry, only process contacts that failed previously
        prev_failed = set(str(cid) for cid in session_data.get("failed_contact_ids", []))
        if prev_failed:
            pending = [c for c in contacts if str(c["contact_id"]) in prev_failed]
            yield emit("status", {
                "msg": f"Retrying {len(pending)} notes that failed last time..."
            })
        else:
            pending = contacts
        total = len(pending)
        success = 0
        errors = 0
        failed_contact_ids = []

        yield emit("status", {"msg": f"Writing {total} prep notes to HubSpot..."})

        for i, c in enumerate(pending):
            name = c.get("name", "Unknown")
            try:
                note_id = hs.create_note_for_contact(c["contact_id"], c["note_html"])
                success += 1
                yield emit("inscribed", {
                    "current": i + 1,
                    "total": total,
                    "name": name,
                    "note_id": note_id,
                })
            except Exception as e:
                errors += 1
                failed_contact_ids.append(c["contact_id"])
                yield emit("error_contact", {
                    "name": name,
                    "msg": f"{str(e)}",
                })
            time.sleep(0.5)

        # Post battle plan to Slack — first full run only. A retry run
        # re-inscribes failed scrolls; reposting duplicates the whole plan.
        slack_ok = False
        if prev_failed:
            yield emit("status", {"msg": "Retry run. The call sheet already went to Slack, skipping."})
        else:
            yield emit("status", {"msg": "Posting the call sheet to Slack..."})
            slack_ok, slack_msg = post_to_slack(session_data)
            if slack_ok:
                yield emit("status", {"msg": f"⚡ {slack_msg}"})
            else:
                yield emit("status", {"msg": f"⚠️ {slack_msg}"})

        # Only delete session if ALL writes succeeded.
        # On partial failure, keep the session so user can retry.
        if errors == 0:
            delete_session(session_id)
            # The disk copy must go too — it still holds failed_contact_ids
            # from earlier runs and would drive duplicate notes on re-approve.
            delete_session_file(session_id)
        else:
            session_data["failed_contact_ids"] = failed_contact_ids
            session_data["approval_errors"] = errors
            set_session(session_id, session_data)
            save_session_to_disk(session_id, session_data)

        yield emit("approved_complete", {
            "success": success,
            "errors": errors,
            "slack_posted": slack_ok,
            "msg": f"Done. {success} prep notes written to HubSpot."
                   + (f" ({errors} failed — session preserved for retry.)" if errors else ""),
        })

    return Response(stream(), mimetype="text/event-stream")


@app.route("/discard/<session_id>", methods=["POST"])
def discard(session_id):
    """Discard a session without writing to HubSpot."""
    import os
    delete_session(session_id)
    path = f"sessions/prep_{session_id}.json"
    if os.path.exists(path):
        os.remove(path)
    return jsonify({"msg": "Route plan discarded."})


# ---------------------------------------------------------------------------
# Activity Refresh — Check which contacts have been dialed
# ---------------------------------------------------------------------------
@app.route("/api/contact-activity", methods=["POST"])
def api_contact_activity():
    """Check which contacts have logged calls since a given date."""
    data = request.json or {}
    contact_ids = data.get("contact_ids", [])
    since_date = data.get("since_date", "")

    if not contact_ids:
        return jsonify({"error": "No contact_ids provided"}), 400
    if not since_date:
        # Default to start of today (UTC)
        since_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")

    if not config.HUBSPOT_ACCESS_TOKEN:
        return jsonify({"error": "Missing HUBSPOT_ACCESS_TOKEN"}), 500

    try:
        hs = HubSpotClient(config.HUBSPOT_ACCESS_TOKEN)
        activity = hs.batch_check_call_activity(contact_ids, since_date)
        return jsonify({"activity": activity, "since_date": since_date})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Cleanup Routes — Purge old/duplicate COLD CALL PREP notes
# ---------------------------------------------------------------------------
@app.route("/cleanup/<session_id>", methods=["POST"])
def cleanup_scan(session_id):
    """Scan HubSpot for duplicate/old COLD CALL PREP notes per contact."""
    session_data = get_session(session_id) or load_session_from_disk(session_id)
    if not session_data:
        return jsonify({"error": "Session not found"}), 404

    if not config.HUBSPOT_ACCESS_TOKEN:
        return jsonify({"error": "Missing HUBSPOT_ACCESS_TOKEN"}), 500

    hs = HubSpotClient(config.HUBSPOT_ACCESS_TOKEN)

    def stream():
        def emit(msg_type, data):
            return f"data: {json.dumps({'type': msg_type, **data})}\n\n"

        contacts = session_data.get("contacts", [])
        total = len(contacts)
        manifest = []

        yield emit("status", {"msg": f"Scanning {total} contacts for duplicate prep notes."})

        total_remove = 0
        total_keep = 0

        for i, c in enumerate(contacts):
            cid = c["contact_id"]
            name = c.get("name", "Unknown")
            expected_html = c.get("note_html", "")
            expected_norm = normalize_html_for_compare(expected_html)

            yield emit("progress", {"current": i + 1, "total": total, "name": name})

            try:
                notes = hs.get_all_prep_notes_for_contact(cid)
            except Exception as e:
                yield emit("error_contact", {"name": name, "msg": f"Could not read notes: {e}"})
                continue

            if not notes:
                yield emit("scan_result", {"name": name, "found": 0, "remove": 0, "keep": 0})
                continue

            keep_id = None
            to_remove = []

            for note in notes:
                note_norm = normalize_html_for_compare(note["body"])
                if not keep_id and expected_norm and note_norm == expected_norm:
                    keep_id = note["id"]
                else:
                    preview = re.sub(r'<[^>]+>', '', note["body"] or "")[:120].strip()
                    to_remove.append({
                        "id": note["id"],
                        "preview": preview,
                        "created": note.get("created_at", ""),
                    })

            if not keep_id and notes:
                keep_id = notes[0]["id"]
                to_remove = [n for n in to_remove if n["id"] != keep_id]

            total_keep += (1 if keep_id else 0)
            total_remove += len(to_remove)

            manifest.append({
                "contact_id": str(cid),
                "name": name,
                "keep_id": keep_id,
                "total_found": len(notes),
                "remove": to_remove,
            })

            yield emit("scan_result", {
                "name": name,
                "found": len(notes),
                "remove": len(to_remove),
                "keep": 1 if keep_id else 0,
            })

            time.sleep(0.3)

        # Store manifest
        cleanup_key = f"cleanup_{session_id}"
        set_session(cleanup_key, manifest)
        save_session_to_disk(cleanup_key, {"manifest": manifest, "session_id": session_id})

        yield emit("scan_complete", {
            "total_contacts": total,
            "total_notes_found": total_keep + total_remove,
            "keeping": total_keep,
            "removing": total_remove,
            "manifest": manifest,
            "msg": f"Scan complete. Found {total_remove} stale prep notes across {total} contacts. "
                   f"({total_keep} true scrolls will be preserved.)",
        })

    return Response(stream(), mimetype="text/event-stream")


@app.route("/execute-cleanup/<session_id>", methods=["POST"])
def execute_cleanup(session_id):
    """Archive all flagged notes from the cleanup scan."""
    import os
    cleanup_key = f"cleanup_{session_id}"
    manifest = get_session(cleanup_key)
    if not manifest and os.path.exists(f"sessions/prep_{cleanup_key}.json"):
        data = load_session_from_disk(cleanup_key)
        manifest = data.get("manifest") if data else None
    if not manifest:
        return jsonify({"error": "No cleanup scan found. Run the scan first."}), 404

    if not config.HUBSPOT_ACCESS_TOKEN:
        return jsonify({"error": "Missing HUBSPOT_ACCESS_TOKEN"}), 500

    hs = HubSpotClient(config.HUBSPOT_ACCESS_TOKEN)

    def stream():
        def emit(msg_type, data):
            return f"data: {json.dumps({'type': msg_type, **data})}\n\n"

        total_to_remove = sum(len(entry.get("remove", [])) for entry in manifest)
        archived = 0
        errors = 0

        yield emit("status", {"msg": f"Removing {total_to_remove} stale prep notes from HubSpot..."})

        for entry in manifest:
            name = entry.get("name", "Unknown")
            for note in entry.get("remove", []):
                try:
                    hs.archive_note(note["id"])
                    archived += 1
                    yield emit("archived", {
                        "name": name,
                        "note_id": note["id"],
                        "current": archived + errors,
                        "total": total_to_remove,
                    })
                except Exception as e:
                    errors += 1
                    yield emit("error_contact", {
                        "name": name,
                        "msg": f"Could not remove note {note['id']}: {e}",
                    })
                time.sleep(0.3)

        delete_session(cleanup_key)
        cleanup_path = f"sessions/prep_{cleanup_key}.json"
        if os.path.exists(cleanup_path):
            os.remove(cleanup_path)

        yield emit("cleanup_complete", {
            "archived": archived,
            "errors": errors,
            "msg": f"Removed {archived} stale prep notes. "
                   f"{'Zeus wept ' + str(errors) + ' times.' if errors else 'Flawless victory!'}",
        })

    return Response(stream(), mimetype="text/event-stream")


# ---------------------------------------------------------------------------
# TODAY'S CLIMB — the call list the BDR works through
# ---------------------------------------------------------------------------
# The list comes from the session the BDR just generated, not from HubSpot
# custom properties. Completion is recorded in the session file, which lives
# on a persistent volume, so the list checks itself off and survives a
# refresh or a restart. HubSpot receives the outcome as a note on the
# contact, which needs no custom schema.

@app.route("/api/climb")
def api_climb():
    """Return the current call list with per-contact completion state."""
    session_id = request.args.get("session_id", "").strip()

    if session_id:
        data = get_session(session_id) or load_session_from_disk(session_id)
    else:
        session_id, data = find_latest_session()

    if not data:
        return jsonify({
            "session_id": None,
            "blocks": [],
            "unknown_tz": [],
            "dispositions": {},
            "totals": {"total": 0, "completed": 0, "remaining": 0},
            "msg": "No call list yet. Build one on Route Plan.",
        })

    dispositions = data.get("dispositions") or {}

    # Scripts live alongside the sheet; index them so each card carries its own.
    scripts = {
        str(c.get("contact_id")): c.get("script_content", "")
        for c in data.get("contacts", [])
    }

    def decorate(contact):
        cid = str(contact.get("contact_id"))
        done = dispositions.get(cid)
        return dict(
            contact,
            script=scripts.get(cid, ""),
            completed=bool(done),
            disposition=(done or {}).get("disposition", ""),
            notes=(done or {}).get("notes", ""),
        )

    blocks = [
        dict(block, contacts=[decorate(c) for c in block.get("contacts", [])])
        for block in data.get("call_sheet", [])
    ]
    unknown = [decorate(c) for c in data.get("unknown_tz", [])]

    total = sum(len(b["contacts"]) for b in blocks) + len(unknown)
    completed = sum(1 for b in blocks for c in b["contacts"] if c["completed"])
    completed += sum(1 for c in unknown if c["completed"])

    return jsonify({
        "session_id": data.get("session_id"),
        "segment": data.get("segment", ""),
        "calling_date": data.get("calling_date", ""),
        "blocks": blocks,
        "unknown_tz": unknown,
        "dispositions": dispositions,
        "totals": {
            "total": total,
            "completed": completed,
            "remaining": total - completed,
        },
    })


@app.route("/api/climb/complete", methods=["POST"])
def api_climb_complete():
    """Log a call outcome: mark it done in the session, note it in HubSpot."""
    data = request.json or {}
    session_id = (data.get("session_id") or "").strip()
    contact_id = (data.get("contact_id") or "").strip()
    disposition = (data.get("disposition") or "").strip()
    notes = (data.get("notes") or "").strip()

    if not session_id or not contact_id or not disposition:
        return jsonify({"error": "Missing session_id, contact_id, or disposition"}), 400

    route = get_route(disposition)
    if not route:
        return jsonify({"error": f"Unknown disposition: {disposition}"}), 400

    # Record it first. The BDR keeps their place even if HubSpot is down.
    dispositions = record_disposition(session_id, contact_id, disposition, notes)
    if dispositions is None:
        return jsonify({"error": "Session not found"}), 404

    # Then write the outcome to the contact as a note.
    hubspot_ok, hubspot_error = True, ""
    dnc_ok = None
    if config.HUBSPOT_ACCESS_TOKEN:
        hs = HubSpotClient(config.HUBSPOT_ACCESS_TOKEN)
        entry = route["log_entry"]
        if notes:
            entry += f" | Notes: {notes}"
        body = (
            f"<p><strong>\U0001f4de CALL OUTCOME</strong></p>"
            f"<p>{entry}</p>"
            f"<p>Logged by SUMMIT on {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</p>"
        )
        try:
            hs.create_note_for_contact(contact_id, body)
        except Exception as e:
            log.warning("Outcome note failed for contact %s: %s", contact_id, e)
            hubspot_ok, hubspot_error = False, str(e)

        # Compliance: do_not_call must reach the standard HubSpot property,
        # not only a note. A note stops nobody else from dialling. This runs
        # in its own call so a failed note cannot swallow the DNC flag, and a
        # failed flag is reported separately rather than hidden.
        if disposition == "do_not_call":
            try:
                hs.update_contact_properties(contact_id, {"donotcall": "true"})
                dnc_ok = True
            except Exception as e:
                log.error("DNC flag failed for contact %s: %s", contact_id, e)
                dnc_ok = False
                hubspot_error = (hubspot_error + " | " if hubspot_error else "") + \
                    f"Do Not Call flag NOT set: {e}"
    else:
        hubspot_ok, hubspot_error = False, "No HubSpot token configured"
        if disposition == "do_not_call":
            dnc_ok = False

    completed = len(dispositions)
    return jsonify({
        "ok": True,
        "contact_id": contact_id,
        "disposition": disposition,
        "completed": completed,
        "hubspot_note_written": hubspot_ok,
        # None unless this was a do_not_call. True means the standard HubSpot
        # Do Not Call flag is set. False must be escalated, not ignored.
        "dnc_flag_set": dnc_ok,
        # Surfaced, not fatal. The card is already checked off locally.
        "hubspot_error": hubspot_error,
        "msg": route["log_entry"],
    })


@app.route("/api/climb/undo", methods=["POST"])
def api_climb_undo():
    """Clear one contact's outcome so a misclick does not strand a call."""
    data = request.json or {}
    session_id = (data.get("session_id") or "").strip()
    contact_id = (data.get("contact_id") or "").strip()
    if not session_id or not contact_id:
        return jsonify({"error": "Missing session_id or contact_id"}), 400

    sess = get_session(session_id) or load_session_from_disk(session_id)
    if not sess:
        return jsonify({"error": "Session not found"}), 404

    dispositions = sess.get("dispositions") or {}
    dispositions.pop(str(contact_id), None)
    sess["dispositions"] = dispositions
    set_session(session_id, sess)
    save_session_to_disk(session_id, sess)
    return jsonify({"ok": True, "completed": len(dispositions)})
@app.route("/api/dispositions")
def api_dispositions():
    """Return all known dispositions for the UI dropdown."""
    return jsonify({"dispositions": list_dispositions()})


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  SUMMIT: DAILY CLIMB")
    print(f"  Navigate to http://localhost:{config.FLASK_PORT}")
    print("=" * 60 + "\n")
    app.run(debug=config.FLASK_DEBUG, port=config.FLASK_PORT, threaded=True)
