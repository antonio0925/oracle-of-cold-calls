"""
Call sheet builder — title seniority ranking and time-block scheduling.

Display labels are dynamically converted to the user's timezone (config.USER_TIMEZONE).
Internal scheduling logic always uses ET hours — only the *labels* change.
"""
import re
import config


def title_seniority(title):
    """Return seniority rank (lower = more senior)."""
    if not title:
        return 99
    t = title.upper()
    # C-level: use word boundary regex to avoid matching substrings
    if re.search(r'\bCHIEF\b', t) or re.search(r'\b(CEO|CFO|CTO|CRO|CMO|COO|CIO)\b', t):
        return 0
    if re.search(r'\b(FOUNDER|OWNER|PRESIDENT)\b', t) and "VICE" not in t:
        return 0
    if "SVP" in t or "SENIOR VICE" in t:
        return 1
    if re.search(r'\bVP\b', t) or "VICE PRESIDENT" in t:
        return 1
    if "DIRECTOR" in t or "HEAD OF" in t:
        return 2
    if "MANAGER" in t or re.search(r'\bLEAD\b', t):
        return 3
    return 4


# ---------------------------------------------------------------------------
# Timezone-aware label helpers
# ---------------------------------------------------------------------------

# Hours behind ET for each US timezone
_TZ_OFFSET_FROM_ET = {
    "US/Eastern": 0,
    "US/Central": 1,
    "US/Mountain": 2,
    "US/Pacific": 3,
    "US/Alaska": 4,
    "US/Hawaii": 5,
}

# Short abbreviations for display
_TZ_ABBREVS = {
    "US/Eastern": "ET",
    "US/Central": "CT",
    "US/Mountain": "MT",
    "US/Pacific": "PT",
    "US/Alaska": "AKT",
    "US/Hawaii": "HT",
}


def _user_tz_offset():
    """Hours the user's timezone is behind ET (e.g. 3 for Pacific)."""
    return _TZ_OFFSET_FROM_ET.get(config.USER_TIMEZONE, 0)


def user_tz_abbrev():
    """Short abbreviation for the user's configured timezone."""
    return _TZ_ABBREVS.get(config.USER_TIMEZONE, "ET")


def et_to_user_hour(et_hour):
    """Convert an ET hour (0-23) to the user's local hour."""
    return et_hour - _user_tz_offset()


def format_hour(h):
    """Format an hour integer as '5:00 AM' / '12:00 PM' etc."""
    while h <= 0:
        h += 24
    period = "AM" if h < 12 else "PM"
    display = h if h <= 12 else h - 12
    if display == 0:
        display = 12
    return f"{display}:00 {period}"


def _build_time_blocks():
    """Generate TIME_BLOCKS with labels in the user's timezone.

    Structure is identical to the old static list:
        (start_et_hour, end_et_hour, label, color, description, their_local)
    Only the *label* string changes — internal ET hours stay the same.
    """
    tz = user_tz_abbrev()

    # Static block definitions: (start_et, end_et, color, description, their_local)
    _RAW = [
        (8,  9,  "green",  "Eastern Prospects",              "8-9 AM PRIME"),
        (9,  10, "green",  "Eastern + Central Prospects",    "PRIME"),
        (10, 11, "green",  "Central + Mountain Prospects",   "PRIME"),
        (11, 12, "green",  "Mountain + Pacific Prospects",   "PRIME"),
        (12, 13, "green",  "Pacific Prospects",              "9-10 AM PRIME"),
        (13, 15, "red",    "THE UNDERWORLD",                 "Hades' Domain"),
        (15, 16, "yellow", "Eastern Afternoon",              "3-4 PM SECONDARY"),
        (16, 17, "yellow", "Eastern + Central Afternoon",    "SECONDARY"),
        (17, 18, "yellow", "Central + Mountain Afternoon",   "SECONDARY"),
        (18, 19, "yellow", "Mountain + Pacific Afternoon",   "SECONDARY"),
        (19, 20, "yellow", "Pacific Afternoon",              "4-5 PM SECONDARY"),
    ]

    blocks = []
    for start_et, end_et, color, desc, their_local in _RAW:
        user_start = et_to_user_hour(start_et)
        user_end = et_to_user_hour(end_et)
        label = f"{format_hour(user_start)} - {format_hour(user_end)} {tz}"
        blocks.append((start_et, end_et, label, color, desc, their_local))
    return blocks


# Computed once at import time — labels are in the user's timezone
TIME_BLOCKS = _build_time_blocks()

# Map: tz -> list of (et_block_index, priority) where priority 0 = prime
TZ_TO_BLOCKS = {
    "US/Eastern": [(0, 0), (1, 0), (6, 1), (7, 1)],
    "US/Central": [(1, 0), (2, 0), (7, 1), (8, 1)],
    "US/Mountain": [(2, 0), (3, 0), (8, 1), (9, 1)],
    "US/Pacific": [(3, 0), (4, 0), (9, 1), (10, 1)],
    "US/Hawaii": [(4, 0)],
    "US/Alaska": [(3, 0), (4, 0)],
}


def build_call_sheet(contacts_with_data):
    """
    Takes list of dicts with keys: contact, tz, script, email_data
    Returns dict of block_index -> sorted contact list, plus unknowns.
    """
    blocks = {i: [] for i in range(len(TIME_BLOCKS))}
    unknowns = []
    placed = set()

    for item in contacts_with_data:
        tz = item["tz"]
        cid = item["contact"]["id"]

        if tz == "UNKNOWN" or tz not in TZ_TO_BLOCKS:
            unknowns.append(item)
            continue

        tz_blocks = TZ_TO_BLOCKS[tz]
        first_block = tz_blocks[0][0]
        blocks[first_block].append(item)
        placed.add(cid)

    for idx in blocks:
        blocks[idx].sort(key=lambda x: title_seniority(x["contact"].get("properties", {}).get("jobtitle", "")))

    unknowns.sort(key=lambda x: title_seniority(x["contact"].get("properties", {}).get("jobtitle", "")))

    return blocks, unknowns
