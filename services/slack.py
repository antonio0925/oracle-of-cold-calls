"""
Slack dial sheet posting — build and send battle plan messages.
"""
import time
import requests as http_requests
from dateutil import parser as dateparser
import config
from services.retry import retry_request
from services.call_sheet import user_tz_abbrev, et_to_user_hour, format_hour


def build_slack_messages(session_data):
    """Build Slack-formatted messages for the dial sheet.
    Returns (header, thread_messages) — header is the parent message;
    thread_messages are follow-up chunks.
    """
    campaign = session_data.get("campaign", "Unknown Crusade")
    calling_date = session_data.get("calling_date", "")
    stats = session_data.get("stats", {})
    call_sheet = session_data.get("call_sheet", [])
    unknown_tz = session_data.get("unknown_tz", [])
    contacts = session_data.get("contacts", [])
    total_prepped = stats.get("prepped", 0)

    # Parse calling date for display
    try:
        dt = dateparser.parse(calling_date)
        date_display = dt.strftime("%A %b %d")
    except Exception:
        date_display = calling_date

    # Header message
    header = (
        f":crossed_swords: _{date_display} Battle Plan — {campaign}_\n"
        f"_{total_prepped} warriors armed for battle_ | "
        f":scroll: _= prophecy inscribed_\n"
        f"_Strategy: Every prospect called at their 10-11 AM local. "
        f"Times in {user_tz_abbrev()}._\n\n"
        f"_Full battle plan below_ :point_down:"
    )

    # Build contact lookup for prep status — normalize to str because
    # contact_id can be int or str depending on JSON round-trip.
    prepped_ids = {str(c["contact_id"]) for c in contacts}

    # Thread replies — one per time block
    thread_messages = []

    for block in call_sheet:
        if block["color"] == "red":
            continue
        if not block["contacts"]:
            continue

        emoji = ":green_circle:" if block["color"] == "green" else ":large_yellow_circle:"
        block_header = f"_{block['label']}_ — _{block['description']}_ {emoji}\n\n"

        lines = []
        for c in block["contacts"]:
            cid = c.get("contact_id", "")
            name = c.get("name", "Unknown")
            company = c.get("company", "")
            hs_url = f"https://app.hubspot.com/contacts/{config.HUBSPOT_PORTAL_ID}/record/0-1/{cid}"
            icon = ":scroll:" if str(cid) in prepped_ids else ":crossed_swords:"
            lines.append(f"{icon} <{hs_url}|{name}> — {company}")

        msg = block_header + "\n".join(lines)
        thread_messages.append(msg)

    # Unknown TZ block
    if unknown_tz:
        unk_header = ":warning: _LOST IN THE LABYRINTH — Unknown Time Zone_ :compass:\n\n"
        unk_lines = []
        for c in unknown_tz:
            cid = c.get("contact_id", "")
            name = c.get("name", "Unknown")
            company = c.get("company", "")
            hs_url = f"https://app.hubspot.com/contacts/{config.HUBSPOT_PORTAL_ID}/record/0-1/{cid}"
            icon = ":scroll:" if str(cid) in prepped_ids else ":question:"
            unk_lines.append(f"{icon} <{hs_url}|{name}> — {company}")
        thread_messages.append(unk_header + "\n".join(unk_lines))

    # Afternoon redials block — times converted to user's timezone
    tz_abbr = user_tz_abbrev()
    # Redial schedule: re-call each US timezone's no-answers at their 4-5 PM
    # ET 4-5 PM = redial ET contacts, CT 4-5 PM = ET 5-6 PM, etc.
    redial_lines = []
    for label, et_start in [("ET", 16), ("CT", 17), ("MT", 18), ("PT", 19)]:
        user_start = et_to_user_hour(et_start)
        user_end = user_start + 1
        start_str = format_hour(user_start).replace(" AM", "a").replace(" PM", "p")
        end_str = format_hour(user_end).replace(" AM", "a").replace(" PM", "p")
        redial_lines.append(f"_{start_str}–{end_str}_ — Re-dial {label} no-answers (their 4-5 PM)")

    redial_msg = (
        "-------------------------\n\n"
        ":arrows_counterclockwise: _AFTERNOON RE-DIALS (Return from the Underworld)_\n\n"
        + "\n".join(redial_lines) + "\n\n"
        "_Sources: Orum (1B+ dials), Revenue.io, Cognism, HubSpot — "
        "10-11 AM local = highest connect rates_"
    )
    thread_messages.append(redial_msg)

    return header, thread_messages


def post_to_slack(session_data):
    """Post the dial sheet to Slack via webhook. Returns (success, message)."""
    webhook_url = config.SLACK_WEBHOOK_URL
    if not webhook_url:
        return False, "No SLACK_WEBHOOK_URL configured — skipping Slack post"

    header, thread_messages = build_slack_messages(session_data)

    try:
        # Post header (with retry)
        resp = retry_request(
            lambda: http_requests.post(webhook_url, json={"text": header}, timeout=(10, 30)),
            label="Slack webhook (header)",
        )
        if resp.status_code != 200:
            return False, f"Slack webhook failed: {resp.status_code} {resp.text}"

        # Slack has a ~4000 char limit per message. Split if needed.
        chunks = []
        current_chunk = ""
        for block in thread_messages:
            if len(current_chunk) + len(block) + 2 > 3800:
                chunks.append(current_chunk)
                current_chunk = block
            else:
                current_chunk += ("\n\n" + block) if current_chunk else block
        if current_chunk:
            chunks.append(current_chunk)

        for i, chunk in enumerate(chunks):
            resp = retry_request(
                lambda c=chunk: http_requests.post(webhook_url, json={"text": c}, timeout=(10, 30)),
                label=f"Slack webhook (chunk {i + 1}/{len(chunks)})",
            )
            if resp.status_code != 200:
                return False, f"Slack webhook failed on chunk: {resp.status_code}"
            time.sleep(0.5)

        return True, f"Battle plan dispatched to Slack! ({len(chunks) + 1} messages)"

    except Exception as e:
        return False, f"Slack post error: {str(e)}"
