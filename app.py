"""
The Oracle of Cold Calls & The Forge — Flask routes only.

All business logic lives in services/. This file is routes + SSE generators.
"""
import json
import time
import re
import uuid
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Flask, render_template, request, Response, jsonify
import requests as http_requests

import config
from services.sessions import (
    get_session, set_session, delete_session,
    save_session_to_disk, load_session_from_disk, find_resumable_session,
    save_forge_session, load_forge_session, list_forge_sessions,
    utc_now_iso,
)
from services.timezone import resolve_timezone, tz_label
from services.filters import US_COUNTRY_ALIASES
from services.formatting import format_note_html, normalize_html_for_compare
from services.call_sheet import title_seniority, TIME_BLOCKS, TZ_TO_BLOCKS, build_call_sheet
from services.hubspot import HubSpotClient
from services.octave import OctaveClient
from services.notion import NotionClient
from services.slack import post_to_slack

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Flask Routes — The Oracle
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


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


@app.route("/api/campaigns")
def api_campaigns():
    """Return campaign enrollment options from the HubSpot contact property."""
    if not config.HUBSPOT_ACCESS_TOKEN:
        return jsonify({"error": "Missing HubSpot token"}), 500
    try:
        hs = HubSpotClient(config.HUBSPOT_ACCESS_TOKEN)
        prop = hs._get("/crm/v3/properties/contacts/current_campaign_enrollment")
        options = [
            {"value": opt["value"], "label": opt.get("label", opt["value"])}
            for opt in prop.get("options", [])
        ]
        return jsonify({"campaigns": options})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
                    "campaign": data.get("campaign", ""),
                    "calling_date": data.get("calling_date", ""),
                    "prepped_count": len(data.get("contacts", [])),
                    "is_complete": data.get("generation_complete", False),
                    "modified": datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M"),
                })
        except Exception:
            continue
    return jsonify({"sessions": results[:20]})


@app.route("/generate", methods=["POST"])
def generate():
    """SSE endpoint: runs Oracle Phases 1-2, streams progress, stores results."""
    data = request.json
    segment_name = data.get("segment", "").strip()
    campaign = data.get("campaign", "").strip()
    calling_date = data.get("calling_date", "").strip()
    skip_existing = data.get("skip_existing", False)

    if not segment_name or not campaign:
        return jsonify({"error": "Segment and campaign are required"}), 400

    if not config.HUBSPOT_ACCESS_TOKEN or not config.OCTAVE_API_KEY:
        return jsonify({"error": "Missing API credentials in .env"}), 500

    hs = HubSpotClient(config.HUBSPOT_ACCESS_TOKEN)
    octave = OctaveClient(config.OCTAVE_API_KEY)

    # Check for a resumable session
    prev_session_id, prev_session = find_resumable_session(segment_name, campaign, calling_date)
    if prev_session:
        session_id = prev_session_id or str(uuid.uuid4())[:8]
    else:
        session_id = str(uuid.uuid4())[:8]

    def stream():
        stats = {
            "total": 0, "prepped": 0,
            "skipped_subscriber": 0, "skipped_no_email": 0,
            "skipped_existing": 0, "skipped_cached": 0, "errors": 0,
            "tz_breakdown": {},
        }
        prepped_contacts = []

        # Build cache from previous session
        cached_scripts = {}
        if prev_session and prev_session.get("contacts"):
            for c in prev_session["contacts"]:
                if c.get("script_content"):
                    cached_scripts[str(c["contact_id"])] = c

        def emit(msg_type, payload):
            return f"data: {json.dumps({'type': msg_type, **payload})}\n\n"

        def _save_progress():
            partial_data = {
                "session_id": session_id,
                "segment": segment_name,
                "campaign": campaign,
                "calling_date": calling_date,
                "stats": stats,
                "generation_complete": False,
                "contacts": [{
                    "contact_id": c["contact"]["id"],
                    "name": f"{c['contact'].get('properties', {}).get('firstname', '')} {c['contact'].get('properties', {}).get('lastname', '')}".strip(),
                    "company": c["contact"].get("properties", {}).get("company", ""),
                    "note_html": c["note_html"],
                    "script_content": c["script_content"],
                    "tz": c["tz_label"],
                } for c in prepped_contacts],
            }
            save_session_to_disk(session_id, partial_data)

        # Phase 1: Pull contacts
        if cached_scripts:
            yield emit("status", {
                "msg": f"The Oracle remembers! Found {len(cached_scripts)} cached prophecies from a prior session. "
                       f"Only new warriors will be consulted..."
            })
        else:
            yield emit("status", {"msg": "The Oracle awakens... searching for thy Legion..."})

        list_id = hs.search_lists(segment_name)
        if not list_id:
            yield emit("error", {"msg": f"Zeus hurls a thunderbolt! Legion '{segment_name}' not found in HubSpot."})
            yield emit("done", {"session_id": None})
            return

        yield emit("status", {"msg": f"Legion found! (List ID: {list_id}). Summoning warriors..."})

        contact_ids = hs.get_list_memberships(list_id)
        stats["total"] = len(contact_ids)
        yield emit("status", {"msg": f"{len(contact_ids)} mortals found in the Legion. Beginning the trials..."})

        if not contact_ids:
            yield emit("done", {"session_id": None, "stats": stats})
            return

        contacts = hs.batch_get_contacts(contact_ids, [
            "firstname", "lastname", "email", "company", "jobtitle",
            "phone", "mobilephone", "city", "state", "country", "hs_timezone",
        ])

        # Phase 2: Filter + generate
        for i, contact in enumerate(contacts):
            cid = contact["id"]
            props = contact.get("properties", {})
            name = f"{props.get('firstname', '')} {props.get('lastname', '')}".strip() or f"Contact {cid}"
            company_name = props.get("company", "Unknown")

            yield emit("progress", {"current": i + 1, "total": len(contacts), "name": name})

            # Resume check
            if str(cid) in cached_scripts:
                cached = cached_scripts[str(cid)]
                tz = resolve_timezone(props)
                tz_lbl = tz_label(tz)
                stats["tz_breakdown"][tz_lbl] = stats["tz_breakdown"].get(tz_lbl, 0) + 1
                stats["skipped_cached"] += 1
                stats["prepped"] += 1
                fresh_html = format_note_html(props, campaign, cached["script_content"])
                prepped_contacts.append({
                    "contact": contact,
                    "tz": tz,
                    "tz_label": tz_lbl,
                    "script_content": cached["script_content"],
                    "email_data": {},
                    "note_html": fresh_html,
                })
                yield emit("done_contact", {
                    "name": name, "company": company_name, "tz": tz_lbl,
                    "cached": True,
                })
                continue

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
                            "reason": f"Already a loyal subject (${mrr:.0f}/mo)"
                        })
                        break
                if is_subscriber:
                    continue
            except Exception as e:
                yield emit("warn", {"name": name, "msg": f"Could not check subscription: {e}"})

            # Filter B: Must have outbound email
            try:
                email_data = hs.search_emails_for_contact(cid)
            except Exception as e:
                stats["errors"] += 1
                yield emit("error_contact", {"name": name, "msg": f"Email search failed: {e}"})
                continue
            if not email_data:
                stats["skipped_no_email"] += 1
                yield emit("skip", {"name": name, "reason": "No herald has been dispatched to this mortal"})
                continue

            # Filter C: Existing prep check
            if skip_existing:
                has_prep = hs.search_notes_for_contact(cid)
                if has_prep:
                    stats["skipped_existing"] += 1
                    yield emit("skip", {"name": name, "reason": "Has already received the Oracle's wisdom"})
                    continue

            # Generate script via Octave
            yield emit("generating", {"name": name, "company": company_name})

            try:
                script_data = octave.generate_call_script(
                    props,
                    email_data["subject"],
                    email_data.get("body_html") or email_data.get("body_text", ""),
                )
                script_content = ""
                if isinstance(script_data, dict):
                    script_content = script_data.get("content", "") or script_data.get("text", "") or json.dumps(script_data)
                elif isinstance(script_data, str):
                    script_content = script_data

                tz = resolve_timezone(props)
                tz_lbl = tz_label(tz)
                stats["tz_breakdown"][tz_lbl] = stats["tz_breakdown"].get(tz_lbl, 0) + 1

                prepped_contacts.append({
                    "contact": contact,
                    "tz": tz,
                    "tz_label": tz_lbl,
                    "script_content": script_content,
                    "email_data": email_data,
                    "note_html": format_note_html(props, campaign, script_content),
                })
                stats["prepped"] += 1
                yield emit("done_contact", {"name": name, "company": company_name, "tz": tz_lbl})

                try:
                    _save_progress()
                except Exception:
                    pass

            except http_requests.exceptions.Timeout:
                stats["errors"] += 1
                yield emit("error_contact", {
                    "name": name,
                    "msg": "The Oracle timed out consulting the stars! (120s timeout — skipping)",
                })
            except http_requests.exceptions.ConnectionError:
                stats["errors"] += 1
                yield emit("error_contact", {
                    "name": name,
                    "msg": "Lost connection to the Oracle of Octave! (Connection error — skipping)",
                })
            except Exception as e:
                stats["errors"] += 1
                yield emit("error_contact", {"name": name, "msg": f"Zeus hurls a thunderbolt! {str(e)}"})

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
            "campaign": campaign,
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
                f"The Oracle has spoken! {stats['prepped']} mortals prepared for battle "
                f"({cached_count} recalled from memory, {new_count} freshly consulted)."
            )
        else:
            completion_msg = f"The Oracle has spoken! {stats['prepped']} mortals prepared for battle."

        yield emit("complete", {
            "session_id": session_id,
            "stats": stats,
            "msg": completion_msg,
        })

    return Response(stream(), mimetype="text/event-stream")


@app.route("/approve/<session_id>", methods=["POST"])
def approve(session_id):
    """SSE endpoint: writes all notes to HubSpot."""
    session_data = get_session(session_id) or load_session_from_disk(session_id)
    if not session_data:
        return jsonify({"error": "Session not found. The scrolls have been lost!"}), 404

    hs = HubSpotClient(config.HUBSPOT_ACCESS_TOKEN)

    def stream():
        def emit(msg_type, data):
            return f"data: {json.dumps({'type': msg_type, **data})}\n\n"

        contacts = session_data.get("contacts", [])
        total = len(contacts)
        success = 0
        errors = 0

        yield emit("status", {"msg": f"THE KRAKEN IS RELEASED! Inscribing {total} sacred scrolls..."})

        for i, c in enumerate(contacts):
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
                yield emit("error_contact", {
                    "name": name,
                    "msg": f"The scroll crumbles! {str(e)}",
                })
            time.sleep(0.5)

        # Post battle plan to Slack
        yield emit("status", {"msg": "Dispatching the battle plan to Slack..."})
        slack_ok, slack_msg = post_to_slack(session_data)
        if slack_ok:
            yield emit("status", {"msg": f"⚡ {slack_msg}"})
        else:
            yield emit("status", {"msg": f"⚠️ {slack_msg}"})

        # Clean up session
        delete_session(session_id)

        yield emit("approved_complete", {
            "success": success,
            "errors": errors,
            "slack_posted": slack_ok,
            "msg": f"THE ORACLE HAS SPOKEN. {success} sacred scrolls inscribed in the annals of HubSpot!",
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
    return jsonify({"msg": "Banished to Tartarus! The scrolls have been destroyed."})


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

        yield emit("status", {"msg": f"Athena surveys the battlefield... scanning {total} contacts for duplicate scrolls."})

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
            "msg": f"Athena's survey complete! Found {total_remove} false scrolls to purge across {total} contacts. "
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

        yield emit("status", {"msg": f"⚔️ SMITING {total_to_remove} false scrolls from HubSpot..."})

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
                        "msg": f"Failed to smite note {note['id']}: {e}",
                    })
                time.sleep(0.3)

        delete_session(cleanup_key)
        cleanup_path = f"sessions/prep_{cleanup_key}.json"
        if os.path.exists(cleanup_path):
            os.remove(cleanup_path)

        yield emit("cleanup_complete", {
            "archived": archived,
            "errors": errors,
            "msg": f"⚔️ {archived} false scrolls have been smitten! "
                   f"{'Zeus wept ' + str(errors) + ' times.' if errors else 'Flawless victory!'}",
        })

    return Response(stream(), mimetype="text/event-stream")


# ---------------------------------------------------------------------------
# THE FORGE — Campaign Pipeline Routes (Stages 1-4)
# ---------------------------------------------------------------------------
@app.route("/api/forge/campaigns")
def forge_campaigns():
    """List campaigns from Notion for The Forge dropdown."""
    if not config.NOTION_API_KEY:
        return jsonify({"error": "Missing NOTION_API_KEY in .env"}), 500
    try:
        notion = NotionClient(config.NOTION_API_KEY)
        campaigns = notion.list_campaigns()
        return jsonify({"campaigns": campaigns})
    except Exception as e:
        return jsonify({"error": f"Notion error: {str(e)}"}), 500


@app.route("/api/forge/campaign-brief/<page_id>")
def forge_campaign_brief(page_id):
    """Fetch and parse a campaign brief from Notion."""
    if not config.NOTION_API_KEY:
        return jsonify({"error": "Missing NOTION_API_KEY"}), 500
    try:
        notion = NotionClient(config.NOTION_API_KEY)
        brief = notion.get_campaign_brief(page_id)
        brief.pop("raw_blocks", None)
        return jsonify({"brief": brief})
    except Exception as e:
        return jsonify({"error": f"Failed to parse campaign brief: {str(e)}"}), 500


@app.route("/api/forge/sessions")
def forge_sessions_list():
    """List recoverable Forge sessions."""
    return jsonify({"sessions": list_forge_sessions()})


@app.route("/api/forge/session/<session_id>")
def forge_session_get(session_id):
    """Fetch a Forge session by ID."""
    data = get_session(f"forge_{session_id}") or load_forge_session(session_id)
    if not data:
        return jsonify({"error": "Forge session not found"}), 404
    return jsonify(data)


@app.route("/api/forge/start", methods=["POST"])
def forge_start():
    """Claude calls this after MCP discovery to inject domains into the Forge pipeline."""
    data = request.json or {}
    campaign_id = data.get("campaign_id", "")
    campaign_name = data.get("campaign_name", "")
    playbook_id = data.get("playbook_id", "")
    domains = data.get("domains", [])
    brief_summary = data.get("brief_summary", "")

    if not domains:
        return jsonify({"error": "No domains provided"}), 400

    domains = list(dict.fromkeys(d.strip().lower() for d in domains if d.strip()))

    session_id = str(uuid.uuid4())[:8]
    forge_data = {
        "session_id": session_id,
        "campaign_id": campaign_id,
        "campaign_name": campaign_name,
        "playbook_id": playbook_id,
        "stage": 1,
        "status": "domains_ready",
        "discovered_domains": domains,
        "brief_summary": brief_summary,
        "companies": [],
        "enriched_companies": [],
        "people": [],
        "enriched_people": [],
        "created_at": utc_now_iso(),
    }
    set_session(f"forge_{session_id}", forge_data)
    save_forge_session(session_id, forge_data)

    return jsonify({
        "session_id": session_id,
        "domain_count": len(domains),
        "msg": f"Forge session created with {len(domains)} domains. The UI will auto-start qualification.",
    })


@app.route("/forge/prospect", methods=["POST"])
def forge_prospect():
    """SSE: Stage 2 — Qualify discovered companies."""
    data = request.json
    session_id = data.get("session_id") or str(uuid.uuid4())[:8]

    existing_session = get_session(f"forge_{session_id}") or load_forge_session(session_id)
    if existing_session and existing_session.get("discovered_domains"):
        domains = existing_session["discovered_domains"]
        campaign_id = existing_session.get("campaign_id", "")
        campaign_name = existing_session.get("campaign_name", "")
        playbook_id = existing_session.get("playbook_id", "")
        brief = existing_session.get("brief", {})
    else:
        domains = data.get("domains", [])
        campaign_id = data.get("campaign_id", "")
        campaign_name = data.get("campaign_name", "")
        playbook_id = data.get("playbook_id", "")
        brief = data.get("brief", {})

    domains = list(dict.fromkeys(d.strip().lower() for d in domains if d.strip()))

    if not config.OCTAVE_API_KEY:
        return jsonify({"error": "Missing OCTAVE_API_KEY"}), 500

    octave = OctaveClient(config.OCTAVE_API_KEY)

    def stream():
        def emit(msg_type, payload):
            return f"data: {json.dumps({'type': msg_type, **payload})}\n\n"

        companies = []
        seen_domains = set()
        filtered_out = 0

        if not domains:
            yield emit("error", {
                "msg": "No domains to qualify. Tell Claude to 'forge [campaign name]' first."
            })
            return

        # Deduplicate
        unique_domains = []
        for d in domains:
            if d not in seen_domains:
                seen_domains.add(d)
                unique_domains.append(d)

        yield emit("status", {"msg": f"Qualifying {len(unique_domains)} discovered companies (parallel)..."})

        def _qualify_one(domain):
            """Worker: qualify a single domain. Returns (domain, entry_or_None, error_msg)."""
            try:
                qual_result = octave.qualify_company(domain)
                entry = _parse_qualify_company_result(qual_result, domain)
                return (domain, entry, None)
            except Exception as e:
                return (domain, None, str(e))

        completed = 0
        with ThreadPoolExecutor(max_workers=5) as pool:
            future_map = {pool.submit(_qualify_one, d): d for d in unique_domains}
            for future in as_completed(future_map):
                completed += 1
                domain, entry, error_msg = future.result()

                yield emit("progress", {"current": completed, "total": len(unique_domains), "name": domain})

                if error_msg:
                    yield emit("error_contact", {"name": domain, "msg": f"Lookup failed: {error_msg}"})
                    continue
                if not entry:
                    yield emit("skip", {"name": domain, "reason": "Not found in Octave"})
                    continue
                if not entry.get("us_based"):
                    filtered_out += 1
                    yield emit("skip", {
                        "name": entry["name"],
                        "reason": f"Non-US ({entry.get('country', 'unknown')})",
                    })
                    continue
                companies.append(entry)
                yield emit("company_found", {
                    "name": entry["name"],
                    "domain": entry["domain"],
                    "industry": entry.get("industry", ""),
                    "employees": entry.get("employees", ""),
                    "location": entry.get("location", ""),
                    "score": entry["score"],
                    "qualified": entry["qualified"],
                    "source": "claude_discovery",
                })

        # Save session
        forge_data = {
            "session_id": session_id,
            "campaign_id": campaign_id,
            "campaign_name": campaign_name,
            "playbook_id": playbook_id,
            "stage": 2,
            "brief": brief,
            "companies": companies,
            "enriched_companies": [],
            "people": [],
            "enriched_people": [],
            "created_at": utc_now_iso(),
        }
        set_session(f"forge_{session_id}", forge_data)
        save_forge_session(session_id, forge_data)

        qualified_count = sum(1 for c in companies if c.get("qualified"))
        yield emit("prospect_complete", {
            "session_id": session_id,
            "total_found": len(companies),
            "qualified_count": qualified_count,
            "filtered_out": filtered_out,
            "companies": companies,
            "msg": f"Prospecting complete: {len(companies)} US-based companies found, "
                   f"{qualified_count} pass qualification (>= {config.QUAL_THRESHOLD}/10). "
                   f"{filtered_out} non-US filtered out. Review and approve below.",
        })

    return Response(stream(), mimetype="text/event-stream")


def _parse_qualify_company_result(result, domain):
    """Parse a qualify_company response into a standardized company entry."""
    if not result.get("found") and not result.get("data"):
        return None

    comp_data = result.get("data", {})
    company_info = comp_data.get("company") or {}
    location = company_info.get("location") or {}

    name = company_info.get("name", domain)
    country_code = (location.get("countryCode") or "").upper()

    score = comp_data.get("score") or 0
    if isinstance(score, str):
        try:
            score = float(score)
        except (ValueError, TypeError):
            score = 0

    return {
        "name": name,
        "domain": domain,
        "country": country_code,
        "industry": company_info.get("industry", ""),
        "employees": company_info.get("employeeCount", ""),
        "location": location.get("locality", ""),
        "description": (company_info.get("description") or "")[:200],
        "score": score,
        "reasoning": comp_data.get("rationale") or "",
        "qualified": score >= config.QUAL_THRESHOLD,
        "us_based": country_code in US_COUNTRY_ALIASES,
        "product": comp_data.get("product"),
        "segment": comp_data.get("segment"),
        "playbook": comp_data.get("playbook"),
    }


@app.route("/forge/enrich-companies", methods=["POST"])
def forge_enrich_companies():
    """SSE: Stage 3 — Deep enrichment of approved companies."""
    data = request.json
    session_id = data.get("session_id")
    approved_domains = data.get("approved_domains", [])

    if not session_id:
        return jsonify({"error": "Missing session_id"}), 400

    forge_data = get_session(f"forge_{session_id}") or load_forge_session(session_id)
    if not forge_data:
        return jsonify({"error": "Forge session not found"}), 404

    if not config.OCTAVE_API_KEY:
        return jsonify({"error": "Missing OCTAVE_API_KEY"}), 500

    octave = OctaveClient(config.OCTAVE_API_KEY)

    approved_set = set(approved_domains)
    approved_companies = [
        c for c in forge_data.get("companies", [])
        if c.get("domain") in approved_set
    ]

    def stream():
        def emit(msg_type, payload):
            return f"data: {json.dumps({'type': msg_type, **payload})}\n\n"

        yield emit("status", {
            "msg": f"Athena studies {len(approved_companies)} companies in depth (parallel)..."
        })

        enriched_companies = []
        errors = 0

        def _enrich_one(company):
            """Worker: enrich a single company. Returns (company, enriched_entry, error_msg)."""
            domain = company.get("domain", "")
            try:
                result = octave.enrich_company(domain)
                enrich_data = result.get("data", {})
                enriched_entry = {
                    **company,
                    "enrichment": enrich_data,
                    "enrichment_summary": (
                        enrich_data.get("summary")
                        or enrich_data.get("companyOverview")
                        or enrich_data.get("description")
                        or ""
                    )[:300],
                    "talking_points": enrich_data.get("talkingPoints", []),
                    "tech_stack": enrich_data.get("techStack", []),
                    "recent_news": enrich_data.get("recentNews", []),
                }
                return (company, enriched_entry, None)
            except Exception as e:
                return (company, None, str(e))

        completed = 0
        with ThreadPoolExecutor(max_workers=3) as pool:
            future_map = {pool.submit(_enrich_one, c): c for c in approved_companies}
            for future in as_completed(future_map):
                completed += 1
                company, enriched_entry, error_msg = future.result()
                company_name = company.get("name", "Unknown")
                domain = company.get("domain", "")

                yield emit("progress", {
                    "current": completed,
                    "total": len(approved_companies),
                    "name": company_name,
                })

                if error_msg:
                    errors += 1
                    yield emit("error_contact", {
                        "name": company_name,
                        "msg": f"Enrichment failed: {error_msg}",
                    })
                    continue

                enriched_companies.append(enriched_entry)
                yield emit("company_enriched", {
                    "name": company_name,
                    "domain": domain,
                    "industry": company.get("industry", ""),
                    "score": company.get("score", 0),
                    "summary": enriched_entry["enrichment_summary"],
                    "talking_points": enriched_entry["talking_points"][:3],
                })

        # Update session
        forge_data["enriched_companies"] = enriched_companies
        forge_data["stage"] = 3
        set_session(f"forge_{session_id}", forge_data)
        save_forge_session(session_id, forge_data)

        yield emit("enrich_companies_complete", {
            "session_id": session_id,
            "total_enriched": len(enriched_companies),
            "errors": errors,
            "companies": enriched_companies,
            "msg": f"Athena's deep study complete: {len(enriched_companies)} companies enriched"
                   f"{f', {errors} errors' if errors else ''}. "
                   f"Review the intelligence below and approve for people discovery.",
        })

    return Response(stream(), mimetype="text/event-stream")


@app.route("/forge/discover-enrich-people", methods=["POST"])
def forge_discover_enrich_people():
    """SSE: Stage 4 — Discover people at approved enriched companies,
    filter US-only, then enrich each person."""
    data = request.json
    session_id = data.get("session_id")
    approved_domains = data.get("approved_enriched_domains", data.get("approved_domains", []))

    if not session_id:
        return jsonify({"error": "Missing session_id"}), 400

    forge_data = get_session(f"forge_{session_id}") or load_forge_session(session_id)
    if not forge_data:
        return jsonify({"error": "Forge session not found"}), 404

    if not config.OCTAVE_API_KEY:
        return jsonify({"error": "Missing OCTAVE_API_KEY"}), 500

    octave = OctaveClient(config.OCTAVE_API_KEY)

    approved_set = set(approved_domains)
    target_companies = [
        c for c in forge_data.get("enriched_companies", [])
        if c.get("domain") in approved_set
    ]

    def stream():
        def emit(msg_type, payload):
            return f"data: {json.dumps({'type': msg_type, **payload})}\n\n"

        yield emit("status", {
            "msg": f"Hermes scouts {len(target_companies)} companies for decision-makers (parallel)..."
        })

        all_people = []
        enriched_people = []
        filtered_non_us = 0

        # --- Phase A: Parallel prospecting (discover people at each company) ---
        def _prospect_one(company):
            """Worker: prospect people at one company."""
            domain = company.get("domain", "")
            company_name = company.get("name", "Unknown")
            try:
                result = octave.prospect_people(domain)
                people_list = []
                result_data = result.get("data", {})
                contacts_data = result_data.get("contacts", [])
                if contacts_data:
                    for item in contacts_data:
                        if isinstance(item, dict) and "contact" in item:
                            people_list.append(item["contact"])
                        else:
                            people_list.append(item)
                elif isinstance(result_data, list):
                    people_list = result_data
                return (company, people_list, None)
            except Exception as e:
                return (company, [], str(e))

        # Collect all discovered people (with US filter) before enrichment
        pending_enrichment = []

        completed_prospect = 0
        with ThreadPoolExecutor(max_workers=5) as pool:
            future_map = {pool.submit(_prospect_one, c): c for c in target_companies}
            for future in as_completed(future_map):
                completed_prospect += 1
                company, people_list, error_msg = future.result()
                company_name = company.get("name", "Unknown")
                domain = company.get("domain", "")

                if error_msg:
                    yield emit("error", {
                        "msg": f"Error scouting {company_name}: {error_msg}",
                    })
                    continue

                if not people_list:
                    yield emit("status", {
                        "msg": f"No prospects found at {company_name}. Moving on..."
                    })
                    continue

                yield emit("status", {
                    "msg": f"Found {len(people_list)} prospects at {company_name} "
                           f"({completed_prospect}/{len(target_companies)} companies scouted)."
                })

                for person in people_list:
                    person_name = f"{person.get('firstName', '')} {person.get('lastName', '')}".strip()
                    if not person_name:
                        person_name = person.get("name", "Unknown")

                    # US-only filter
                    location = person.get("location") or {}
                    country_code = ""
                    if isinstance(location, dict):
                        country_code = location.get("countryCode", "")
                    person_country = person.get("countryCode", country_code)

                    if person_country and person_country.upper() not in US_COUNTRY_ALIASES:
                        filtered_non_us += 1
                        yield emit("skip", {"name": person_name, "reason": f"Non-US ({person_country})"})
                        continue

                    person_entry = {
                        "name": person_name,
                        "firstName": person.get("firstName", ""),
                        "lastName": person.get("lastName", ""),
                        "email": person.get("email", ""),
                        "title": person.get("title", person.get("jobTitle", "")),
                        "company": company_name,
                        "domain": domain,
                        "linkedin": person.get("profileUrl", person.get("linkedInProfile", "")),
                        "location": person.get("location", "") if isinstance(person.get("location"), str) else "",
                    }
                    all_people.append(person_entry)
                    pending_enrichment.append(person_entry)

        yield emit("status", {
            "msg": f"Scouting complete! {len(pending_enrichment)} US-based prospects found. "
                   f"Starting deep enrichment (parallel)..."
        })

        # --- Phase B: Parallel person enrichment ---
        def _enrich_one_person(person_entry):
            """Worker: enrich one person."""
            try:
                enrich_input = {
                    "firstName": person_entry["firstName"],
                    "lastName": person_entry["lastName"],
                    "email": person_entry["email"],
                    "companyDomain": person_entry["domain"],
                    "jobTitle": person_entry["title"],
                    "linkedInProfile": person_entry["linkedin"],
                }
                enrich_result = octave.enrich_person(enrich_input)
                enrich_data = enrich_result.get("data", {})
                enriched_entry = {
                    **person_entry,
                    "enrichment": enrich_data,
                    "enrichment_summary": (
                        enrich_data.get("summary")
                        or enrich_data.get("overview")
                        or ""
                    )[:300],
                    "talking_points": enrich_data.get("talkingPoints", []),
                }
                return (person_entry, enriched_entry, None)
            except Exception as e:
                failed_entry = {
                    **person_entry,
                    "enrichment": {},
                    "enrichment_summary": f"Enrichment failed: {e}",
                    "talking_points": [],
                }
                return (person_entry, failed_entry, str(e))

        completed_enrich = 0
        with ThreadPoolExecutor(max_workers=3) as pool:
            future_map = {pool.submit(_enrich_one_person, p): p for p in pending_enrichment}
            for future in as_completed(future_map):
                completed_enrich += 1
                person_entry, enriched_entry, error_msg = future.result()
                enriched_people.append(enriched_entry)

                if error_msg:
                    yield emit("warn", {
                        "msg": f"Enrichment failed for {person_entry['name']}: {error_msg}",
                    })
                else:
                    yield emit("person_enriched", {
                        "name": person_entry["name"],
                        "title": person_entry["title"],
                        "company": person_entry["company"],
                        "email": person_entry["email"],
                        "linkedin": person_entry["linkedin"],
                        "summary": enriched_entry["enrichment_summary"],
                        "progress": f"{completed_enrich}/{len(pending_enrichment)}",
                    })

        # Update session
        forge_data["people"] = all_people
        forge_data["enriched_people"] = enriched_people
        forge_data["stage"] = 4
        set_session(f"forge_{session_id}", forge_data)
        save_forge_session(session_id, forge_data)

        yield emit("people_complete", {
            "session_id": session_id,
            "total_found": len(all_people),
            "total_enriched": len(enriched_people),
            "filtered_non_us": filtered_non_us,
            "people": enriched_people,
            "msg": f"Hermes' report: {len(enriched_people)} enriched prospects "
                   f"from {len(target_companies)} companies. "
                   f"{filtered_non_us} non-US filtered out. "
                   f"Review and approve your final prospect list below.",
        })

    return Response(stream(), mimetype="text/event-stream")


@app.route("/api/forge/approve-stage", methods=["POST"])
def forge_approve_stage():
    """Save approved items and advance the Forge pipeline stage."""
    data = request.json
    session_id = data.get("session_id")
    stage = data.get("stage")

    if not session_id or not stage:
        return jsonify({"error": "Missing session_id or stage"}), 400

    forge_data = get_session(f"forge_{session_id}") or load_forge_session(session_id)
    if not forge_data:
        return jsonify({"error": "Forge session not found"}), 404

    approved_items = []
    if stage == 2:
        approved_items = data.get("approved_domains", [])
        forge_data["approved_company_domains"] = approved_items
    elif stage == 3:
        approved_items = data.get("approved_enriched_domains", [])
        forge_data["approved_enriched_domains"] = approved_items
    elif stage == 4:
        approved_items = data.get("approved_people", [])
        forge_data["approved_people"] = approved_items

    forge_data["stage"] = stage + 1
    set_session(f"forge_{session_id}", forge_data)
    save_forge_session(session_id, forge_data)

    return jsonify({
        "msg": f"Stage {stage} approved. {len(approved_items)} items confirmed.",
        "session_id": session_id,
        "next_stage": stage + 1,
    })


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  THE ORACLE OF COLD CALLS & THE FORGE AWAKEN")
    print(f"  Navigate to http://localhost:{config.FLASK_PORT}")
    print("=" * 60 + "\n")
    app.run(debug=config.FLASK_DEBUG, port=config.FLASK_PORT, threaded=True)
