"""
HubSpot API client — lists, contacts, emails, notes, associations.
"""
from datetime import datetime, timezone
import requests as http_requests
from services.retry import retry_request


class HubSpotClient:
    BASE = "https://api.hubapi.com"

    def __init__(self, token):
        self.token = token
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    # -- Low-level HTTP helpers (with retry) --
    # Default timeout: (connect=10s, read=120s) — prevents hung connections
    # from blocking threads indefinitely.
    DEFAULT_TIMEOUT = (10, 120)

    def _get(self, path, params=None):
        r = retry_request(
            lambda: http_requests.get(
                f"{self.BASE}{path}", headers=self.headers, params=params,
                timeout=self.DEFAULT_TIMEOUT,
            ),
            label=f"HubSpot GET {path}",
        )
        r.raise_for_status()
        return r.json()

    def _post(self, path, payload):
        r = retry_request(
            lambda: http_requests.post(
                f"{self.BASE}{path}", headers=self.headers, json=payload,
                timeout=self.DEFAULT_TIMEOUT,
            ),
            label=f"HubSpot POST {path}",
        )
        r.raise_for_status()
        return r.json()

    def _put(self, path, payload=None):
        r = retry_request(
            lambda: http_requests.put(
                f"{self.BASE}{path}", headers=self.headers, json=payload or {},
                timeout=self.DEFAULT_TIMEOUT,
            ),
            label=f"HubSpot PUT {path}",
        )
        r.raise_for_status()
        return r.json()

    def _delete(self, path):
        r = retry_request(
            lambda: http_requests.delete(
                f"{self.BASE}{path}", headers=self.headers,
                timeout=self.DEFAULT_TIMEOUT,
            ),
            label=f"HubSpot DELETE {path}",
        )
        r.raise_for_status()
        return r.status_code

    # -- Lists --
    def search_lists(self, name):
        """Search for a list by exact name. Returns list ID or None.

        Does NOT fall back to the first result on partial match —
        returning the wrong list silently corrupts downstream data.
        """
        try:
            data = self._post("/crm/v3/lists/search", {
                "query": name,
            })
            for lst in data.get("lists", []):
                if lst.get("name", "").lower().strip() == name.lower().strip():
                    return lst["listId"]
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

        Note: hs_email_direction = "EMAIL" means *outbound* in HubSpot's API.
        Valid values are: "EMAIL" (outbound), "INCOMING_EMAIL" (inbound),
        "FORWARDED_EMAIL" (forwarded).
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
                        "value": "EMAIL",  # "EMAIL" = outbound
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
                "hs_timestamp": datetime.now(timezone.utc).isoformat(),
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

    # ------------------------------------------------------------------
    # Oracle v2: Contact properties for webhook-driven pipeline
    # ------------------------------------------------------------------
    ORACLE_PROPERTIES = [
        "oracle_pending_action", "oracle_action_type", "oracle_campaign_id",
        "oracle_node_id", "oracle_step_number", "oracle_journey_log",
        "oracle_last_action_date", "oracle_call_disposition",
        "oracle_supersend_contact_id",
    ]

    def upsert_contact_oracle(self, email, properties):
        """Create or update a contact with oracle_ properties.

        Uses the v3 search + update (or create) pattern.
        Returns the contact ID.
        """
        # Search by email first
        search = self._post("/crm/v3/objects/contacts/search", {
            "filterGroups": [{
                "filters": [{
                    "propertyName": "email",
                    "operator": "EQ",
                    "value": email,
                }]
            }],
            "properties": ["email"] + self.ORACLE_PROPERTIES,
            "limit": 1,
        })
        results = search.get("results", [])
        if results:
            cid = results[0]["id"]
            self._patch(f"/crm/v3/objects/contacts/{cid}", {"properties": properties})
            return cid
        else:
            # Create with just email first (oracle_ properties may fail during creation)
            data = self._post("/crm/v3/objects/contacts", {
                "properties": {"email": email},
            })
            cid = data["id"]
            # Then patch the oracle_ properties onto the new contact
            if properties:
                self._patch(f"/crm/v3/objects/contacts/{cid}", {"properties": properties})
            return cid

    def _patch(self, path, payload):
        r = retry_request(
            lambda: http_requests.patch(
                f"{self.BASE}{path}", headers=self.headers, json=payload,
                timeout=self.DEFAULT_TIMEOUT,
            ),
            label=f"HubSpot PATCH {path}",
        )
        r.raise_for_status()
        return r.json()

    def update_contact_properties(self, contact_id, properties):
        """Update specific properties on an existing contact."""
        return self._patch(
            f"/crm/v3/objects/contacts/{contact_id}",
            {"properties": properties},
        )

    def get_pending_actions(self):
        """Find all contacts with oracle_pending_action = 'pending'.

        Returns list of contact dicts with full oracle_ properties.
        """
        search = self._post("/crm/v3/objects/contacts/search", {
            "filterGroups": [{
                "filters": [{
                    "propertyName": "oracle_pending_action",
                    "operator": "EQ",
                    "value": "pending",
                }]
            }],
            "properties": [
                "firstname", "lastname", "email", "company", "jobtitle",
                "phone", "mobilephone", "city", "state", "country",
                "hs_timezone",
            ] + self.ORACLE_PROPERTIES,
            "sorts": [{"propertyName": "oracle_last_action_date", "direction": "DESCENDING"}],
            "limit": 100,
        })
        return search.get("results", [])

    def append_journey_log(self, contact_id, entry):
        """Append an entry to the oracle_journey_log field.

        The journey log is an append-only text field. Each entry is a line.
        """
        # Read current value
        contact = self._get(
            f"/crm/v3/objects/contacts/{contact_id}",
            {"properties": "oracle_journey_log"},
        )
        current_log = contact.get("properties", {}).get("oracle_journey_log", "") or ""
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        new_log = f"{current_log}\n[{timestamp}] {entry}".strip()
        # Cap at ~60KB to stay under HubSpot's 65535 char limit
        if len(new_log) > 60000:
            lines = new_log.split("\n")
            while len("\n".join(lines)) > 60000 and len(lines) > 1:
                lines.pop(0)
            new_log = "\n".join(lines)
        self.update_contact_properties(contact_id, {"oracle_journey_log": new_log})

    def batch_check_call_activity(self, contact_ids, since_date):
        """Check which contacts have logged calls on or after since_date.

        Returns {contact_id: {"dialed": bool, "last_call_date": str}}.
        Uses the CRM associations API: contact -> calls.
        """
        results = {}
        for cid in contact_ids:
            cid_str = str(cid)
            results[cid_str] = {"dialed": False, "last_call_date": ""}
            try:
                assoc_data = self._get(
                    f"/crm/v3/objects/contacts/{cid}/associations/calls"
                )
                call_ids = [str(r["id"]) for r in assoc_data.get("results", [])]
                if not call_ids:
                    continue
                # Batch-read the call objects to check timestamps
                # HubSpot batch read allows up to 100 at a time
                for i in range(0, len(call_ids), 100):
                    batch = call_ids[i:i + 100]
                    call_data = self._post("/crm/v3/objects/calls/batch/read", {
                        "inputs": [{"id": c} for c in batch],
                        "properties": ["hs_timestamp", "hs_call_status"],
                    })
                    for call_obj in call_data.get("results", []):
                        call_ts = call_obj.get("properties", {}).get("hs_timestamp", "")
                        if call_ts and call_ts >= since_date:
                            results[cid_str] = {
                                "dialed": True,
                                "last_call_date": call_ts,
                            }
                            break
                    # Stop checking further batches if already found a match
                    if results[cid_str]["dialed"]:
                        break
            except Exception:
                pass
        return results
