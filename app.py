import os
import json
import time
import re
import uuid
from datetime import datetime, date
from dateutil import parser as dateparser
from dotenv import load_dotenv
from flask import Flask, render_template, request, Response, jsonify
import requests as http_requests

load_dotenv()

app = Flask(__name__)

# ---------------------------------------------------------------------------
# In-memory session store (survives between generate and approve)
# ---------------------------------------------------------------------------
sessions = {}


def save_session_to_disk(session_id, data):
    os.makedirs("sessions", exist_ok=True)
    path = f"sessions/prep_{session_id}.json"
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, default=str, indent=2)
    os.replace(tmp_path, path)  # Atomic write - no half-written files


def load_session_from_disk(session_id):
    path = f"sessions/prep_{session_id}.json"
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


def find_resumable_session(segment, campaign, calling_date):
    """Find an existing partial session that matches segment+campaign+date.
    Returns (session_id, session_data) or (None, None).
    A session is 'resumable' if it was generated but never approved
    (i.e. it still has contacts with scripts but wasn't written to HubSpot).
    """
    sessions_dir = "sessions"
    if not os.path.isdir(sessions_dir):
        return None, None
    best_session = None
    best_time = None
    for fname in os.listdir(sessions_dir):
        if not fname.startswith("prep_") or not fname.endswith(".json"):
            continue
        path = os.path.join(sessions_dir, fname)
        try:
            with open(path) as f:
                data = json.load(f)
            if (data.get("segment", "").lower().strip() == segment.lower().strip()
                    and data.get("campaign", "").lower().strip() == campaign.lower().strip()
                    and data.get("calling_date", "").strip() == calling_date.strip()
                    and data.get("contacts")):
                mtime = os.path.getmtime(path)
                if best_time is None or mtime > best_time:
                    best_session = data
                    best_time = mtime
        except Exception:
            continue
    if best_session:
        return best_session.get("session_id"), best_session
    return None, None


# ---------------------------------------------------------------------------
# HubSpot Client
# ---------------------------------------------------------------------------
class HubSpotClient:
    BASE = "https://api.hubapi.com"

    def __init__(self, token):
        self.token = token
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _get(self, path, params=None):
        r = http_requests.get(f"{self.BASE}{path}", headers=self.headers, params=params)
        r.raise_for_status()
        return r.json()

    def _post(self, path, payload):
        r = http_requests.post(f"{self.BASE}{path}", headers=self.headers, json=payload)
        r.raise_for_status()
        return r.json()

    def _put(self, path, payload=None):
        r = http_requests.put(f"{self.BASE}{path}", headers=self.headers, json=payload or {})
        r.raise_for_status()
        return r.json()

    def _delete(self, path):
        r = http_requests.delete(f"{self.BASE}{path}", headers=self.headers)
        r.raise_for_status()
        return r.status_code

    # -- Lists --
    def search_lists(self, name):
        """Search for a list by name. Returns list ID or None."""
        try:
            data = self._post("/crm/v3/lists/search", {
                "query": name,
            })
            for lst in data.get("lists", []):
                if lst.get("name", "").lower().strip() == name.lower().strip():
                    return lst["listId"]
            if data.get("lists"):
                return data["lists"][0]["listId"]
        except Exception:
            pass
        return None

    def get_list_memberships(self, list_id):
        """Get all contact IDs in a list. Handles pagination."""
        contact_ids = []
        url = f"/crm/v3/lists/{list_id}/memberships"
        params = {}
        while True:
            data = self._get(url, params)
            for r in data.get("results", []):
                if isinstance(r, dict):
                    contact_ids.append(str(r.get("recordId", r.get("id", ""))))
                else:
                    contact_ids.append(str(r))
            after = data.get("paging", {}).get("next", {}).get("after")
            if not after:
                break
            params["after"] = after
        return contact_ids

    def batch_get_contacts(self, ids, properties):
        """Batch read contacts. Handles batches of 100."""
        all_contacts = []
        for i in range(0, len(ids), 100):
            batch = ids[i:i + 100]
            data = self._post("/crm/v3/objects/contacts/batch/read", {
                "inputs": [{"id": cid} for cid in batch],
                "properties": properties,
            })
            all_contacts.extend(data.get("results", []))
        return all_contacts

    def get_associated_companies(self, contact_id):
        """Get company IDs associated with a contact."""
        try:
            data = self._get(f"/crm/v3/objects/contacts/{contact_id}/associations/companies")
            return [str(r["id"]) for r in data.get("results", [])]
        except Exception:
            return []

    def get_company_properties(self, company_id, properties):
        """Read specific properties from a company."""
        try:
            params = {"properties": ",".join(properties)}
            data = self._get(f"/crm/v3/objects/companies/{company_id}", params)
            return data.get("properties", {})
        except Exception:
            return {}

    def search_emails_for_contact(self, contact_id):
        """Find the most recent outbound email for a contact.
        Returns dict with subject/body or None.
        Raises on API errors so callers can surface them.
        """
        data = self._post("/crm/v3/objects/emails/search", {
            "filterGroups": [{
                "filters": [
                    {
                        "propertyName": "associations.contact",
                        "operator": "EQ",
                        "value": str(contact_id),
                    },
                    {
                        "propertyName": "hs_email_direction",
                        "operator": "EQ",
                        "value": "EMAIL",
                    },
                ]
            }],
            "properties": [
                "hs_email_subject",
                "hs_email_html",
                "hs_email_text",
                "hs_timestamp",
            ],
            "sorts": [{"propertyName": "hs_timestamp", "direction": "DESCENDING"}],
            "limit": 1,
        })
        results = data.get("results", [])
        if results:
            props = results[0].get("properties", {})
            return {
                "subject": props.get("hs_email_subject", ""),
                "body_html": props.get("hs_email_html", ""),
                "body_text": props.get("hs_email_text", ""),
            }
        return None

    def search_notes_for_contact(self, contact_id):
        """Check if contact has a COLD CALL PREP note."""
        try:
            data = self._post("/crm/v3/objects/notes/search", {
                "filterGroups": [{
                    "filters": [{
                        "propertyName": "associations.contact",
                        "operator": "EQ",
                        "value": str(contact_id),
                    }]
                }],
                "properties": ["hs_note_body"],
                "limit": 100,
            })
            for note in data.get("results", []):
                body = note.get("properties", {}).get("hs_note_body", "") or ""
                if "COLD CALL PREP" in body:
                    return True
        except Exception:
            pass
        return False

    def create_note_for_contact(self, contact_id, html_body):
        """Create a note and associate it with a contact."""
        note_data = self._post("/crm/v3/objects/notes", {
            "properties": {
                "hs_note_body": html_body,
                "hs_timestamp": datetime.utcnow().isoformat() + "Z",
            }
        })
        note_id = note_data["id"]
        self._put(
            f"/crm/v3/objects/notes/{note_id}/associations/contacts/{contact_id}/note_to_contact"
        )
        return note_id

    def get_all_prep_notes_for_contact(self, contact_id):
        """Get ALL notes containing 'COLD CALL PREP' for a contact.
        Returns list of {id, body, created_at} dicts.
        """
        notes = []
        try:
            data = self._post("/crm/v3/objects/notes/search", {
                "filterGroups": [{
                    "filters": [{
                        "propertyName": "associations.contact",
                        "operator": "EQ",
                        "value": str(contact_id),
                    }]
                }],
                "properties": ["hs_note_body", "hs_timestamp", "hs_createdate"],
                "sorts": [{"propertyName": "hs_createdate", "direction": "DESCENDING"}],
                "limit": 100,
            })
            for note in data.get("results", []):
                body = note.get("properties", {}).get("hs_note_body", "") or ""
                if "COLD CALL PREP" in body:
                    notes.append({
                        "id": note["id"],
                        "body": body,
                        "created_at": note.get("properties", {}).get("hs_createdate", ""),
                    })
        except Exception:
            pass
        return notes

    def archive_note(self, note_id):
        """Archive (soft-delete) a note by ID."""
        return self._delete(f"/crm/v3/objects/notes/{note_id}")


# ---------------------------------------------------------------------------
# Octave Client
# ---------------------------------------------------------------------------
class OctaveClient:
    BASE = "https://app.octavehq.com/api/v2"
    AGENT_OID = "ca_DLoI5XBlw9qGNEDBiV1a2"

    def __init__(self, api_key):
        self.api_key = api_key
        self.headers = {
            "api_key": api_key,
            "Content-Type": "application/json",
        }

    def generate_call_script(self, person, email_subject, email_body):
        """Call the Personalized Cold Call Content agent."""
        runtime_ctx = (
            "Here is the most recent outbound email sent to this prospect. "
            "Use this as your source material for all outputs.\n\n"
            f"Subject: {email_subject}\n\n{email_body}"
        )
        payload = {
            "agentOId": self.AGENT_OID,
            "firstName": person.get("firstname", ""),
            "lastName": person.get("lastname", ""),
            "email": person.get("email", ""),
            "companyName": person.get("company", ""),
            "jobTitle": person.get("jobtitle", ""),
            "runtimeContext": runtime_ctx,
        }
        r = http_requests.post(
            f"{self.BASE}/agents/generate-content/run",
            headers=self.headers,
            json=payload,
            timeout=120,
        )
        r.raise_for_status()
        data = r.json()
        return data.get("data", {})


# ---------------------------------------------------------------------------
# Timezone Resolver
# ---------------------------------------------------------------------------
STATE_TO_TZ = {
    # Eastern
    "CT": "US/Eastern", "DC": "US/Eastern", "DE": "US/Eastern", "FL": "US/Eastern",
    "GA": "US/Eastern", "IN": "US/Eastern", "MA": "US/Eastern", "MD": "US/Eastern",
    "ME": "US/Eastern", "MI": "US/Eastern", "NC": "US/Eastern", "NH": "US/Eastern",
    "NJ": "US/Eastern", "NY": "US/Eastern", "OH": "US/Eastern", "PA": "US/Eastern",
    "RI": "US/Eastern", "SC": "US/Eastern", "VA": "US/Eastern", "VT": "US/Eastern",
    "WV": "US/Eastern",
    "CONNECTICUT": "US/Eastern", "DISTRICT OF COLUMBIA": "US/Eastern",
    "DELAWARE": "US/Eastern", "FLORIDA": "US/Eastern", "GEORGIA": "US/Eastern",
    "INDIANA": "US/Eastern", "MASSACHUSETTS": "US/Eastern", "MARYLAND": "US/Eastern",
    "MAINE": "US/Eastern", "MICHIGAN": "US/Eastern", "NORTH CAROLINA": "US/Eastern",
    "NEW HAMPSHIRE": "US/Eastern", "NEW JERSEY": "US/Eastern", "NEW YORK": "US/Eastern",
    "OHIO": "US/Eastern", "PENNSYLVANIA": "US/Eastern", "RHODE ISLAND": "US/Eastern",
    "SOUTH CAROLINA": "US/Eastern", "VIRGINIA": "US/Eastern", "VERMONT": "US/Eastern",
    "WEST VIRGINIA": "US/Eastern",
    # Central
    "AL": "US/Central", "AR": "US/Central", "IA": "US/Central", "IL": "US/Central",
    "KS": "US/Central", "KY": "US/Central", "LA": "US/Central", "MN": "US/Central",
    "MO": "US/Central", "MS": "US/Central", "ND": "US/Central", "NE": "US/Central",
    "OK": "US/Central", "SD": "US/Central", "TN": "US/Central", "TX": "US/Central",
    "WI": "US/Central",
    "ALABAMA": "US/Central", "ARKANSAS": "US/Central", "IOWA": "US/Central",
    "ILLINOIS": "US/Central", "KANSAS": "US/Central", "KENTUCKY": "US/Central",
    "LOUISIANA": "US/Central", "MINNESOTA": "US/Central", "MISSOURI": "US/Central",
    "MISSISSIPPI": "US/Central", "NORTH DAKOTA": "US/Central", "NEBRASKA": "US/Central",
    "OKLAHOMA": "US/Central", "SOUTH DAKOTA": "US/Central", "TENNESSEE": "US/Central",
    "TEXAS": "US/Central", "WISCONSIN": "US/Central",
    # Mountain
    "AZ": "US/Mountain", "CO": "US/Mountain", "ID": "US/Mountain", "MT": "US/Mountain",
    "NM": "US/Mountain", "UT": "US/Mountain", "WY": "US/Mountain",
    "ARIZONA": "US/Mountain", "COLORADO": "US/Mountain", "IDAHO": "US/Mountain",
    "MONTANA": "US/Mountain", "NEW MEXICO": "US/Mountain", "UTAH": "US/Mountain",
    "WYOMING": "US/Mountain",
    # Pacific
    "CA": "US/Pacific", "NV": "US/Pacific", "OR": "US/Pacific", "WA": "US/Pacific",
    "HI": "US/Hawaii",
    "CALIFORNIA": "US/Pacific", "NEVADA": "US/Pacific", "OREGON": "US/Pacific",
    "WASHINGTON": "US/Pacific", "HAWAII": "US/Hawaii",
    # Alaska
    "AK": "US/Alaska", "ALASKA": "US/Alaska",
}

AREA_CODE_TO_TZ = {
    # Eastern
    "201": "US/Eastern", "202": "US/Eastern", "203": "US/Eastern", "207": "US/Eastern",
    "212": "US/Eastern", "215": "US/Eastern", "216": "US/Eastern", "239": "US/Eastern",
    "240": "US/Eastern", "248": "US/Eastern", "267": "US/Eastern", "301": "US/Eastern",
    "302": "US/Eastern", "305": "US/Eastern", "313": "US/Eastern", "315": "US/Eastern",
    "321": "US/Eastern", "336": "US/Eastern", "347": "US/Eastern", "352": "US/Eastern",
    "386": "US/Eastern", "401": "US/Eastern", "404": "US/Eastern", "407": "US/Eastern",
    "410": "US/Eastern", "412": "US/Eastern", "413": "US/Eastern", "434": "US/Eastern",
    "440": "US/Eastern", "443": "US/Eastern", "484": "US/Eastern", "508": "US/Eastern",
    "513": "US/Eastern", "516": "US/Eastern", "518": "US/Eastern", "540": "US/Eastern",
    "551": "US/Eastern", "561": "US/Eastern", "570": "US/Eastern", "571": "US/Eastern",
    "585": "US/Eastern", "586": "US/Eastern", "603": "US/Eastern", "609": "US/Eastern",
    "610": "US/Eastern", "614": "US/Eastern", "617": "US/Eastern", "631": "US/Eastern",
    "646": "US/Eastern", "678": "US/Eastern", "703": "US/Eastern", "704": "US/Eastern",
    "706": "US/Eastern", "716": "US/Eastern", "718": "US/Eastern", "732": "US/Eastern",
    "740": "US/Eastern", "754": "US/Eastern", "757": "US/Eastern", "770": "US/Eastern",
    "772": "US/Eastern", "774": "US/Eastern", "781": "US/Eastern", "786": "US/Eastern",
    "802": "US/Eastern", "803": "US/Eastern", "804": "US/Eastern", "813": "US/Eastern",
    "814": "US/Eastern", "828": "US/Eastern", "845": "US/Eastern", "848": "US/Eastern",
    "856": "US/Eastern", "857": "US/Eastern", "860": "US/Eastern", "862": "US/Eastern",
    "863": "US/Eastern", "904": "US/Eastern", "908": "US/Eastern", "910": "US/Eastern",
    "914": "US/Eastern", "917": "US/Eastern", "919": "US/Eastern", "941": "US/Eastern",
    "954": "US/Eastern", "973": "US/Eastern", "978": "US/Eastern",
    # Central
    "205": "US/Central", "210": "US/Central", "214": "US/Central", "217": "US/Central",
    "219": "US/Central", "224": "US/Central", "225": "US/Central", "228": "US/Central",
    "254": "US/Central", "256": "US/Central", "262": "US/Central", "281": "US/Central",
    "309": "US/Central", "312": "US/Central", "314": "US/Central", "316": "US/Central",
    "317": "US/Central", "318": "US/Central", "319": "US/Central", "320": "US/Central",
    "331": "US/Central", "334": "US/Central", "346": "US/Central", "361": "US/Central",
    "385": "US/Central", "402": "US/Central", "405": "US/Central", "409": "US/Central",
    "414": "US/Central", "417": "US/Central", "430": "US/Central", "432": "US/Central",
    "456": "US/Central", "469": "US/Central", "479": "US/Central", "501": "US/Central",
    "502": "US/Central", "504": "US/Central", "507": "US/Central", "512": "US/Central",
    "515": "US/Central", "531": "US/Central", "534": "US/Central", "563": "US/Central",
    "573": "US/Central", "601": "US/Central", "608": "US/Central", "612": "US/Central",
    "615": "US/Central", "618": "US/Central", "620": "US/Central", "630": "US/Central",
    "636": "US/Central", "641": "US/Central", "651": "US/Central", "660": "US/Central",
    "662": "US/Central", "682": "US/Central", "701": "US/Central", "708": "US/Central",
    "713": "US/Central", "715": "US/Central", "717": "US/Central", "720": "US/Central",
    "731": "US/Central", "737": "US/Central", "743": "US/Central", "763": "US/Central",
    "769": "US/Central", "773": "US/Central", "779": "US/Central", "806": "US/Central",
    "815": "US/Central", "816": "US/Central", "817": "US/Central", "830": "US/Central",
    "832": "US/Central", "847": "US/Central", "850": "US/Central", "870": "US/Central",
    "872": "US/Central", "901": "US/Central", "903": "US/Central", "913": "US/Central",
    "915": "US/Central", "920": "US/Central", "936": "US/Central", "940": "US/Central",
    "952": "US/Central", "956": "US/Central", "972": "US/Central", "979": "US/Central",
    # Mountain
    "303": "US/Mountain", "307": "US/Mountain", "385": "US/Mountain", "406": "US/Mountain",
    "435": "US/Mountain", "480": "US/Mountain", "505": "US/Mountain", "520": "US/Mountain",
    "575": "US/Mountain", "602": "US/Mountain", "623": "US/Mountain", "719": "US/Mountain",
    "720": "US/Mountain", "801": "US/Mountain", "928": "US/Mountain",
    # Pacific
    "206": "US/Pacific", "209": "US/Pacific", "213": "US/Pacific", "253": "US/Pacific",
    "310": "US/Pacific", "323": "US/Pacific", "360": "US/Pacific", "408": "US/Pacific",
    "415": "US/Pacific", "424": "US/Pacific", "425": "US/Pacific", "442": "US/Pacific",
    "503": "US/Pacific", "509": "US/Pacific", "510": "US/Pacific", "530": "US/Pacific",
    "541": "US/Pacific", "559": "US/Pacific", "562": "US/Pacific", "619": "US/Pacific",
    "626": "US/Pacific", "628": "US/Pacific", "650": "US/Pacific", "657": "US/Pacific",
    "661": "US/Pacific", "669": "US/Pacific", "702": "US/Pacific", "707": "US/Pacific",
    "714": "US/Pacific", "725": "US/Pacific", "747": "US/Pacific", "760": "US/Pacific",
    "775": "US/Pacific", "805": "US/Pacific", "818": "US/Pacific", "831": "US/Pacific",
    "858": "US/Pacific", "909": "US/Pacific", "916": "US/Pacific", "925": "US/Pacific",
    "949": "US/Pacific", "951": "US/Pacific", "971": "US/Pacific",
}

TZ_LABELS = {
    "US/Eastern": "ET",
    "US/Central": "CT",
    "US/Mountain": "MT",
    "US/Pacific": "PT",
    "US/Hawaii": "HT",
    "US/Alaska": "AKT",
}


def resolve_timezone(contact_props):
    """Resolve timezone using priority: hs_timezone > state > area code."""
    hs_tz = (contact_props.get("hs_timezone") or "").strip()
    if hs_tz:
        return hs_tz

    state = (contact_props.get("state") or "").strip().upper()
    if state and state in STATE_TO_TZ:
        return STATE_TO_TZ[state]

    for phone_field in ["mobilephone", "phone"]:
        phone = (contact_props.get(phone_field) or "").strip()
        digits = re.sub(r"\D", "", phone)
        if len(digits) >= 10:
            if digits.startswith("1") and len(digits) == 11:
                digits = digits[1:]
            area = digits[:3]
            if area in AREA_CODE_TO_TZ:
                return AREA_CODE_TO_TZ[area]

    return "UNKNOWN"


def tz_label(tz):
    return TZ_LABELS.get(tz, tz)


# ---------------------------------------------------------------------------
# Title Seniority Ranking
# ---------------------------------------------------------------------------
def title_seniority(title):
    """Return seniority rank (lower = more senior)."""
    if not title:
        return 99
    t = title.upper()
    # C-level: use word boundary regex to avoid matching substrings (e.g. "DIRECTOR" contains "CTO")
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
# Call Sheet Builder
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# HTML Note Template — Structured HubSpot Note Formatter
# ---------------------------------------------------------------------------
def _split_octave_sections(script_content):
    """Split Octave output into voicemail, objections, and live call sections."""
    sections = {"voicemail": "", "objections": "", "live_call": ""}

    # Octave uses: ### OUTPUT 1: VOICEMAIL SCRIPT, ### OUTPUT 2: ..., ### OUTPUT 3: ...
    # Also handle without "OUTPUT N:" prefix: ### VOICEMAIL SCRIPT
    parts = re.split(r'###\s*(?:OUTPUT\s*\d+\s*:\s*)?', script_content, flags=re.IGNORECASE)

    for part in parts:
        stripped = part.strip()
        upper = stripped[:60].upper()
        if upper.startswith("VOICEMAIL"):
            sections["voicemail"] = re.sub(
                r'^VOICEMAIL\s*SCRIPT\s*\n*', '', stripped, flags=re.IGNORECASE
            ).strip()
        elif upper.startswith("POTENTIAL OBJECTION") or upper.startswith("OBJECTION"):
            sections["objections"] = re.sub(
                r'^(?:POTENTIAL\s*)?OBJECTIONS?\s*\n*', '', stripped, flags=re.IGNORECASE
            ).strip()
        elif upper.startswith("LIVE CALL") or upper.startswith("CALL SCRIPT"):
            sections["live_call"] = re.sub(
                r'^(?:LIVE\s*)?CALL\s*SCRIPT\s*\n*', '', stripped, flags=re.IGNORECASE
            ).strip()

    return sections


def _strip_md(text):
    """Strip markdown formatting to plain text: remove **bold**, *italic*, etc."""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'\1', text)
    return text


def _format_voicemail_html(vm_text):
    """Format voicemail as clean HTML paragraphs."""
    if not vm_text:
        return ""
    clean = _strip_md(vm_text.strip())
    # Remove markdown horizontal rules
    clean = re.sub(r'^[\*\-_]{3,}\s*$', '', clean, flags=re.MULTILINE)
    # Convert double newlines to paragraph breaks, single newlines to <br>
    paragraphs = re.split(r'\n\s*\n', clean)
    return "".join(f"<p>{p.strip().replace(chr(10), '<br>')}</p>" for p in paragraphs if p.strip())


def _format_live_call_html(lc_text):
    """Format live call script: OPENER/HOOK/ASK/ENGAGE/SHUT IT DOWN subsections."""
    if not lc_text:
        return ""

    blocks = []
    current_label = None
    current_lines = []

    for line in lc_text.split("\n"):
        stripped = line.strip()
        if not stripped:
            if current_lines:
                current_lines.append("")
            continue

        # Detect section headers: **OPENER:** or **THE HOOK:** (with colon inside or outside bold)
        header_match = re.match(r'^\*\*([A-Z][A-Z\s\':]+?):?\*\*:?\s*$', stripped)
        # Also match plain "OPENER:" style
        if not header_match:
            header_match = re.match(r'^([A-Z][A-Z\s\':]{3,}):?\s*$', stripped)

        if header_match:
            if current_label is not None or current_lines:
                blocks.append((current_label, "\n".join(current_lines).strip()))
            current_label = header_match.group(1).strip().rstrip(":")
            current_lines = []
        else:
            current_lines.append(stripped)

    if current_label is not None or current_lines:
        blocks.append((current_label, "\n".join(current_lines).strip()))

    html_parts = []
    for label, content in blocks:
        if not content and not label:
            continue
        # Strip markdown from content, preserve structure
        content = _strip_md(content)
        # Convert paragraphs (double newline) and lines
        paragraphs = re.split(r'\n\s*\n', content)
        content_html = "".join(
            f"<p>{p.strip().replace(chr(10), '<br>')}</p>"
            for p in paragraphs if p.strip()
        )
        if label:
            html_parts.append(f"<p><strong>{label}:</strong></p>{content_html}")
        else:
            html_parts.append(content_html)

    return "".join(html_parts)


def _format_objections_html(obj_text):
    """Format objections: each with a quote header and bullet-point responses."""
    if not obj_text:
        return ""

    blocks = []
    current_category = None
    current_objection = None
    current_responses = []

    for line in obj_text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue

        # Match: **Objection:** "text"
        obj_match = re.match(
            r'^\*\*(?:Objection|OBJECTION)\s*:?\*\*\s*["\u201c](.+?)["\u201d]?\s*$',
            stripped,
        )
        # Match category-style: TIMING: "text" or STATUS QUO: "text"
        cat_match = re.match(
            r'^([A-Z][A-Z\s/\-]+?):\s*["\u201c](.+?)["\u201d]?\s*$',
            stripped,
        )

        if obj_match:
            if current_objection:
                blocks.append((current_category, current_objection, current_responses))
            current_category = None
            current_objection = obj_match.group(1).strip().rstrip('"').rstrip('\u201d')
            current_responses = []
        elif cat_match and not stripped.startswith("*"):
            if current_objection:
                blocks.append((current_category, current_objection, current_responses))
            current_category = cat_match.group(1).strip()
            current_objection = cat_match.group(2).strip().rstrip('"').rstrip('\u201d')
            current_responses = []
        elif stripped.startswith("**Response") or stripped.startswith("**Responses"):
            # Could be just a header "**Responses:**" OR inline "**Response 1:** actual text"
            inline = re.sub(r'^\*\*Responses?\s*\d*\s*:?\*\*:?\s*', '', stripped).strip()
            if inline:
                current_responses.append(_strip_md(inline))
            # else: bare header line like "**Responses:**" — skip it
        elif re.match(r'^[\*\-\u2022]\s+', stripped):
            resp = re.sub(r'^[\*\-\u2022]\s+', '', stripped).strip()
            resp = re.sub(r'^\*\*Response\s*\d*:?\*\*\s*', '', resp)
            current_responses.append(_strip_md(resp))

    if current_objection:
        blocks.append((current_category, current_objection, current_responses))

    html_parts = []
    for category, objection, responses in blocks:
        if category:
            html_parts.append(f"<p><strong>{category}:</strong> \u201c{objection}\u201d</p>")
        else:
            html_parts.append(f"<p><strong>\u201c{objection}\u201d</strong></p>")
        if responses:
            html_parts.append("<ul>")
            for r in responses:
                html_parts.append(f"<li>{r}</li>")
            html_parts.append("</ul>")

    return "".join(html_parts)


def format_note_html(contact_props, campaign, script_content):
    """Transform Octave markdown output into a structured HubSpot note
    matching the exact format:

      🔥 COLD CALL PREP - First Last | Company
      Campaign | Generated YYYY-MM-DD
      📞 VOICEMAIL SCRIPT  ...
      🎯 LIVE CALL SCRIPT  ... (with OPENER/HOOK/ASK/ENGAGE/SHUT IT DOWN)
      🛡️ OBJECTION HANDLING ... (with category + quote + bullet responses)
    """
    first = contact_props.get("firstname", "")
    last = contact_props.get("lastname", "")
    company = contact_props.get("company", "")
    today_str = date.today().strftime("%Y-%m-%d")

    sections = _split_octave_sections(script_content)

    parts = []

    # ── Header ──
    parts.append(
        f"<p><strong>\U0001f525 COLD CALL PREP - {first} {last} | {company}</strong></p>"
        f"<p>{campaign} | Generated {today_str}</p>"
    )

    # ── Voicemail ──
    if sections["voicemail"]:
        parts.append(
            f"<p><strong>\U0001f4de VOICEMAIL SCRIPT</strong></p>"
            f"{_format_voicemail_html(sections['voicemail'])}"
        )

    # ── Live Call Script ──
    if sections["live_call"]:
        parts.append(
            f"<p><strong>\U0001f3af LIVE CALL SCRIPT</strong></p>"
            f"{_format_live_call_html(sections['live_call'])}"
        )

    # ── Objection Handling ──
    if sections["objections"]:
        parts.append(
            f"<p><strong>\U0001f6e1\ufe0f OBJECTION HANDLING</strong></p>"
            f"{_format_objections_html(sections['objections'])}"
        )

    return "<br>".join(parts)


# ---------------------------------------------------------------------------
# Slack Dial Sheet Posting
# ---------------------------------------------------------------------------
HUBSPOT_PORTAL_ID = "46940643"
SLACK_CHANNEL_ID = "C0AELNTNNDV"  # #daily-cold-call-plan


def build_slack_messages(session_data):
    """Build Slack-formatted messages for the dial sheet.
    Returns a list of message strings: [header, block1, block2, ...].
    Header is the parent message; the rest are thread replies.
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
        f"Times in PST._\n\n"
        f"_Full battle plan in thread_ :thread:"
    )

    # Build contact lookup for prep status (all contacts in our list have prep)
    prepped_ids = {c["contact_id"] for c in contacts}

    # Thread replies — one per time block
    thread_messages = []

    # Map time blocks to mythology-themed PST times
    # TIME_BLOCKS are in ET, convert labels for display
    for block in call_sheet:
        if block["color"] == "red":
            continue  # Skip dead zone in Slack output

        if not block["contacts"]:
            continue  # Skip empty blocks

        # Build block header
        emoji = ":green_circle:" if block["color"] == "green" else ":large_yellow_circle:"
        block_header = f"_{block['label']}_ — _{block['description']}_ {emoji}\n\n"

        # Build contact lines
        lines = []
        for c in block["contacts"]:
            cid = c.get("contact_id", "")
            name = c.get("name", "Unknown")
            company = c.get("company", "")
            hs_url = f"https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}/record/0-1/{cid}"
            icon = ":scroll:" if cid in prepped_ids else ":crossed_swords:"
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
            hs_url = f"https://app.hubspot.com/contacts/{HUBSPOT_PORTAL_ID}/record/0-1/{cid}"
            icon = ":scroll:" if cid in prepped_ids else ":question:"
            unk_lines.append(f"{icon} <{hs_url}|{name}> — {company}")
        thread_messages.append(unk_header + "\n".join(unk_lines))

    # Afternoon redials block
    redial_msg = (
        "-------------------------\n\n"
        ":arrows_counterclockwise: _AFTERNOON RE-DIALS (Return from the Underworld)_\n\n"
        "_1:00–2:00p_ — Re-dial ET no-answers (their 4-5 PM)\n"
        "_2:00–3:00p_ — Re-dial CT no-answers (their 4-5 PM)\n"
        "_3:00–4:00p_ — Re-dial MT no-answers (their 4-5 PM)\n"
        "_4:00–5:00p_ — Re-dial PT no-answers (their 4-5 PM)\n\n"
        "_Sources: Orum (1B+ dials), Revenue.io, Cognism, HubSpot — "
        "10-11 AM local = highest connect rates_"
    )
    thread_messages.append(redial_msg)

    return header, thread_messages


def post_to_slack(session_data):
    """Post the dial sheet to Slack via webhook. Returns (success, message)."""
    webhook_url = os.getenv("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        return False, "No SLACK_WEBHOOK_URL configured — skipping Slack post"

    header, thread_messages = build_slack_messages(session_data)

    try:
        # Post header
        resp = http_requests.post(webhook_url, json={"text": header})
        if resp.status_code != 200:
            return False, f"Slack webhook failed: {resp.status_code} {resp.text}"

        # For thread replies, we need the Slack API (webhooks can't thread).
        # So we'll concatenate all blocks into one or two follow-up messages.
        # Slack webhook messages can't reply to threads, so we'll pack it.
        full_body = "\n\n".join(thread_messages)

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

        for chunk in chunks:
            resp = http_requests.post(webhook_url, json={"text": chunk})
            if resp.status_code != 200:
                return False, f"Slack webhook failed on chunk: {resp.status_code}"
            time.sleep(0.5)

        return True, f"Battle plan dispatched to Slack! ({len(chunks) + 1} messages)"

    except Exception as e:
        return False, f"Slack post error: {str(e)}"


# ---------------------------------------------------------------------------
# Flask Routes
# ---------------------------------------------------------------------------
ANTONIO_CREATOR_ID = "87514817"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/lists")
def api_lists():
    """Return all HubSpot lists created by Antonio for the dropdown."""
    hs_token = os.getenv("HUBSPOT_ACCESS_TOKEN", "")
    if not hs_token:
        return jsonify({"error": "Missing HubSpot token"}), 500
    hs = HubSpotClient(hs_token)
    all_lists = []
    offset = 0
    while True:
        try:
            data = hs._post("/crm/v3/lists/search", {"query": "", "offset": offset})
            for lst in data.get("lists", []):
                if lst.get("createdById") == ANTONIO_CREATOR_ID:
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
    hs_token = os.getenv("HUBSPOT_ACCESS_TOKEN", "")
    if not hs_token:
        return jsonify({"error": "Missing HubSpot token"}), 500
    try:
        hs = HubSpotClient(hs_token)
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
    """Fetch full session data for review (called after generate completes)."""
    session_data = sessions.get(session_id) or load_session_from_disk(session_id)
    if not session_data:
        return jsonify({"error": "Session not found"}), 404
    return jsonify(session_data)


@app.route("/api/recoverable-sessions")
def api_recoverable_sessions():
    """List session files that can be resumed (have contacts with scripts)."""
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
    """SSE endpoint: runs Phases 1-2, streams progress, stores results for review.
    Supports progressive saving (each Octave result saved immediately) and
    resume (re-uses scripts from a prior partial session for the same inputs).
    """
    data = request.json
    segment_name = data.get("segment", "").strip()
    campaign = data.get("campaign", "").strip()
    calling_date = data.get("calling_date", "").strip()
    skip_existing = data.get("skip_existing", False)

    if not segment_name or not campaign:
        return jsonify({"error": "Segment and campaign are required"}), 400

    hs_token = os.getenv("HUBSPOT_ACCESS_TOKEN", "")
    octave_key = os.getenv("OCTAVE_API_KEY", "")

    if not hs_token or not octave_key:
        return jsonify({"error": "Missing API credentials in .env"}), 500

    hs = HubSpotClient(hs_token)
    octave = OctaveClient(octave_key)

    # Check for a resumable session with matching inputs
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

        # Build a cache of already-generated scripts from previous session
        cached_scripts = {}  # contact_id -> {script_content, note_html, tz, ...}
        if prev_session and prev_session.get("contacts"):
            for c in prev_session["contacts"]:
                if c.get("script_content"):
                    cached_scripts[str(c["contact_id"])] = c

        def emit(msg_type, payload):
            return f"data: {json.dumps({'type': msg_type, **payload})}\n\n"

        def _save_progress():
            """Save current progress to disk (called after each successful Octave gen)."""
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

        contact_map = {c["id"]: c for c in contacts}

        # Phase 1: Filter each contact
        for i, contact in enumerate(contacts):
            cid = contact["id"]
            props = contact.get("properties", {})
            name = f"{props.get('firstname', '')} {props.get('lastname', '')}".strip() or f"Contact {cid}"
            company_name = props.get("company", "Unknown")

            yield emit("progress", {"current": i + 1, "total": len(contacts), "name": name})

            # Resume check: if we already have a cached script for this contact, use it
            if str(cid) in cached_scripts:
                cached = cached_scripts[str(cid)]
                tz = resolve_timezone(props)
                tz_lbl = tz_label(tz)
                stats["tz_breakdown"][tz_lbl] = stats["tz_breakdown"].get(tz_lbl, 0) + 1
                stats["skipped_cached"] += 1
                stats["prepped"] += 1
                # Always re-format note_html from script_content using current formatter
                fresh_html = format_note_html(props, campaign, cached["script_content"])
                prepped_contacts.append({
                    "contact": contact,
                    "tz": tz,
                    "tz_label": tz_lbl,
                    "script_content": cached["script_content"],
                    "email_data": {},  # Not needed for review/approve
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

            # Passed all filters - generate script via Octave
            yield emit("generating", {"name": name, "company": company_name})

            try:
                script_data = octave.generate_call_script(
                    props,
                    email_data["subject"],
                    email_data.get("body_html") or email_data.get("body_text", ""),
                )
                # Extract the generated content text
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

                # PROGRESSIVE SAVE: write to disk after every successful Octave generation
                try:
                    _save_progress()
                except Exception:
                    pass  # Don't let a disk write failure kill the stream

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

            time.sleep(1)  # Rate limiting

        # Build call sheet
        blocks, unknowns = build_call_sheet(prepped_contacts)

        # Build serializable call sheet
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

        # Store final complete session
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
        sessions[session_id] = session_data
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

        # Send a lightweight complete event (full data fetched via /api/session)
        yield emit("complete", {
            "session_id": session_id,
            "stats": stats,
            "msg": completion_msg,
        })

    return Response(stream(), mimetype="text/event-stream")


@app.route("/approve/<session_id>", methods=["POST"])
def approve(session_id):
    """SSE endpoint: writes all notes to HubSpot."""
    session_data = sessions.get(session_id) or load_session_from_disk(session_id)
    if not session_data:
        return jsonify({"error": "Session not found. The scrolls have been lost!"}), 404

    hs_token = os.getenv("HUBSPOT_ACCESS_TOKEN", "")
    hs = HubSpotClient(hs_token)

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
        if session_id in sessions:
            del sessions[session_id]

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
    if session_id in sessions:
        del sessions[session_id]
    path = f"sessions/prep_{session_id}.json"
    if os.path.exists(path):
        os.remove(path)
    return jsonify({"msg": "Banished to Tartarus! The scrolls have been destroyed."})


# ---------------------------------------------------------------------------
# Cleanup Routes — Purge old/duplicate COLD CALL PREP notes
# ---------------------------------------------------------------------------
def _normalize_html_for_compare(html):
    """Normalize HTML to a stable string for comparison.
    HubSpot may alter whitespace, entity encoding, etc.
    We strip it all down to just visible text content.
    """
    if not html:
        return ""
    # Remove all HTML tags
    text = re.sub(r'<[^>]+>', ' ', html)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    # Normalize unicode quotes
    text = text.replace('\u201c', '"').replace('\u201d', '"')
    text = text.replace('\u2014', '-').replace('\u2013', '-')
    return text


@app.route("/cleanup/<session_id>", methods=["POST"])
def cleanup_scan(session_id):
    """Scan HubSpot for duplicate/old COLD CALL PREP notes per contact.
    Returns a manifest of what will be kept vs archived.
    """
    session_data = sessions.get(session_id) or load_session_from_disk(session_id)
    if not session_data:
        return jsonify({"error": "Session not found"}), 404

    hs_token = os.getenv("HUBSPOT_ACCESS_TOKEN", "")
    if not hs_token:
        return jsonify({"error": "Missing HUBSPOT_ACCESS_TOKEN"}), 500

    hs = HubSpotClient(hs_token)

    def stream():
        def emit(msg_type, data):
            return f"data: {json.dumps({'type': msg_type, **data})}\n\n"

        contacts = session_data.get("contacts", [])
        total = len(contacts)
        manifest = []  # list of {contact_id, name, keep_id, remove: [{id, preview, created}]}

        yield emit("status", {"msg": f"Athena surveys the battlefield... scanning {total} contacts for duplicate scrolls."})

        total_remove = 0
        total_keep = 0

        for i, c in enumerate(contacts):
            cid = c["contact_id"]
            name = c.get("name", "Unknown")
            expected_html = c.get("note_html", "")
            expected_norm = _normalize_html_for_compare(expected_html)

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
                note_norm = _normalize_html_for_compare(note["body"])
                # Match: the normalized text of the note matches our expected output
                if not keep_id and expected_norm and note_norm == expected_norm:
                    keep_id = note["id"]
                else:
                    # Extract a short preview for the review UI
                    preview = re.sub(r'<[^>]+>', '', note["body"] or "")[:120].strip()
                    to_remove.append({
                        "id": note["id"],
                        "preview": preview,
                        "created": note.get("created_at", ""),
                    })

            # If we didn't find an exact match, keep the NEWEST one (first in list, sorted DESC)
            if not keep_id and notes:
                keep_id = notes[0]["id"]
                # Remove it from to_remove if it ended up there
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

            time.sleep(0.3)  # Rate limiting

        # Store manifest for the execute step
        cleanup_key = f"cleanup_{session_id}"
        sessions[cleanup_key] = manifest
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
    cleanup_key = f"cleanup_{session_id}"
    manifest = sessions.get(cleanup_key)
    if not manifest and os.path.exists(f"sessions/prep_{cleanup_key}.json"):
        data = load_session_from_disk(cleanup_key)
        manifest = data.get("manifest") if data else None
    if not manifest:
        return jsonify({"error": "No cleanup scan found. Run the scan first."}), 404

    hs_token = os.getenv("HUBSPOT_ACCESS_TOKEN", "")
    if not hs_token:
        return jsonify({"error": "Missing HUBSPOT_ACCESS_TOKEN"}), 500

    hs = HubSpotClient(hs_token)

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

        # Clean up the manifest
        if cleanup_key in sessions:
            del sessions[cleanup_key]
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


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("  THE ORACLE OF COLD CALLS AWAKENS")
    print("  Navigate to http://localhost:5001")
    print("=" * 60 + "\n")
    app.run(debug=True, port=5001, threaded=True)
