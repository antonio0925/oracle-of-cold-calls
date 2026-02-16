"""
Call sheet builder — title seniority ranking and time-block scheduling.
"""
import re


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


# Time blocks: (start_et_hour, end_et_hour, label, color, who_called, their_local)
TIME_BLOCKS = [
    (8, 9, "8:00 - 9:00 AM ET", "green", "Eastern Prospects", "8-9 AM PRIME"),
    (9, 10, "9:00 - 10:00 AM ET", "green", "Eastern + Central Prospects", "PRIME"),
    (10, 11, "10:00 - 11:00 AM ET", "green", "Central + Mountain Prospects", "PRIME"),
    (11, 12, "11:00 AM - 12:00 PM ET", "green", "Mountain + Pacific Prospects", "PRIME"),
    (12, 13, "12:00 - 1:00 PM ET", "green", "Pacific Prospects", "9-10 AM PRIME"),
    (13, 15, "1:00 - 3:00 PM ET", "red", "THE UNDERWORLD", "Hades' Domain"),
    (15, 16, "3:00 - 4:00 PM ET", "yellow", "Eastern Afternoon", "3-4 PM SECONDARY"),
    (16, 17, "4:00 - 5:00 PM ET", "yellow", "Eastern + Central Afternoon", "SECONDARY"),
    (17, 18, "5:00 - 6:00 PM ET", "yellow", "Central + Mountain Afternoon", "SECONDARY"),
    (18, 19, "6:00 - 7:00 PM ET", "yellow", "Mountain + Pacific Afternoon", "SECONDARY"),
    (19, 20, "7:00 - 8:00 PM ET", "yellow", "Pacific Afternoon", "4-5 PM SECONDARY"),
]

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
