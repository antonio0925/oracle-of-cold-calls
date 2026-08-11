"""
Octave API client — cold call scripts, qualification, prospecting, enrichment.
"""
import json

import requests as http_requests
import config
from services.retry import retry_request


def script_text(script_data):
    """Normalize a generate_call_script() response into formatter-ready text.

    Octave returns a dict; format_note_html() needs the string body out of it.
    Single source of truth — app.py and the agent both call this.
    """
    if isinstance(script_data, str):
        return script_data
    if isinstance(script_data, dict):
        return script_data.get("content") or script_data.get("text") or json.dumps(script_data)
    return ""


class OctaveClient:
    BASE = "https://app.octavehq.com/api/v2"

    def __init__(self, api_key):
        self.api_key = api_key
        self.headers = {
            "api_key": api_key,
            "Content-Type": "application/json",
        }

    def _post(self, path, payload, timeout=120):
        """Low-level POST with retry.
        timeout is read-timeout only; connect timeout is always 10s.
        """
        r = retry_request(
            lambda: http_requests.post(
                f"{self.BASE}{path}",
                headers=self.headers, json=payload, timeout=(10, timeout),
            ),
            label=f"Octave POST {path}",
        )
        r.raise_for_status()
        return r.json()

    def generate_call_script(self, person, email_subject, email_body):
        """Call the Personalized Cold Call Content agent."""
        runtime_ctx = (
            "Here is the most recent outbound email sent to this prospect. "
            "Use this as your source material for all outputs.\n\n"
            f"Subject: {email_subject}\n\n{email_body}"
        )
        payload = {
            "agentOId": config.OCTAVE_CONTENT_AGENT,
            "firstName": person.get("firstname", ""),
            "lastName": person.get("lastname", ""),
            "email": person.get("email", ""),
            "companyName": person.get("company", ""),
            "jobTitle": person.get("jobtitle", ""),
            "runtimeContext": runtime_ctx,
        }
        data = self._post("/agents/generate-content/run", payload, timeout=120)
        return data.get("data", {})
