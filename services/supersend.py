"""
Supersend API client — bulk-action sequences (assign_step, transfer, finish).

Docs: https://docs.supersend.io
"""
import requests as http_requests
from services.retry import retry_request


class SupersendClient:
    BASE = "https://api.supersend.io/v1"

    def __init__(self, api_key):
        self.api_key = api_key
        self.headers = {
            "X-API-Key": api_key,
            "Content-Type": "application/json",
        }

    DEFAULT_TIMEOUT = (10, 60)

    def _post(self, path, payload):
        r = retry_request(
            lambda: http_requests.post(
                f"{self.BASE}{path}", headers=self.headers, json=payload,
                timeout=self.DEFAULT_TIMEOUT,
            ),
            label=f"Supersend POST {path}",
        )
        r.raise_for_status()
        return r.json()

    def _get(self, path, params=None):
        r = retry_request(
            lambda: http_requests.get(
                f"{self.BASE}{path}", headers=self.headers, params=params,
                timeout=self.DEFAULT_TIMEOUT,
            ),
            label=f"Supersend GET {path}",
        )
        r.raise_for_status()
        return r.json()

    def assign_step(self, contact_id, sequence_id, step_number):
        """Move a contact to a specific step in a Supersend sequence."""
        return self._post("/bulk-action", {
            "action": "assign_step",
            "contact_ids": [contact_id],
            "sequence_id": sequence_id,
            "step_number": step_number,
        })

    def transfer_contact(self, contact_id, from_sequence_id, to_sequence_id, step_number=1):
        """Transfer a contact from one sequence to another."""
        return self._post("/bulk-action", {
            "action": "transfer",
            "contact_ids": [contact_id],
            "from_sequence_id": from_sequence_id,
            "to_sequence_id": to_sequence_id,
            "step_number": step_number,
        })

    def finish_contact(self, contact_id, sequence_id):
        """Mark a contact as finished in a sequence."""
        return self._post("/bulk-action", {
            "action": "finish",
            "contact_ids": [contact_id],
            "sequence_id": sequence_id,
        })

    def get_contact(self, contact_id):
        """Get contact details from Supersend."""
        return self._get(f"/contacts/{contact_id}")
