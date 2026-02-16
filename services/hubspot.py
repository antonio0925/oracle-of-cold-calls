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
