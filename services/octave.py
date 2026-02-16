"""
Octave API client — cold call scripts, qualification, prospecting, enrichment.
"""
import requests as http_requests
import config
from services.retry import retry_request


class OctaveClient:
    BASE = "https://app.octavehq.com/api/v2"

    def __init__(self, api_key):
        self.api_key = api_key
        self.headers = {
            "api_key": api_key,
            "Content-Type": "application/json",
        }

    def _post(self, path, payload, timeout=120):
        """Low-level POST with retry."""
        r = retry_request(
            lambda: http_requests.post(
                f"{self.BASE}{path}",
                headers=self.headers, json=payload, timeout=timeout,
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

    def qualify_company(self, domain):
        """Qualify a company via agents/qualify-company/run."""
        payload = {
            "agentOId": config.OCTAVE_QUALIFY_COMPANY_AGENT,
            "companyDomain": domain,
        }
        return self._post("/agents/qualify-company/run", payload, timeout=120)

    def prospect_people(self, company_domain):
        """Find people at a company via agents/prospector/run."""
        payload = {
            "agentOId": config.OCTAVE_PROSPECTOR_AGENT,
            "companyDomain": company_domain,
        }
        return self._post("/agents/prospector/run", payload, timeout=120)

    def qualify_person(self, person):
        """Qualify a person via agents/qualify-person/run."""
        payload = {
            "agentOId": config.OCTAVE_QUALIFY_PERSON_AGENT,
            "person": person,
        }
        return self._post("/agents/qualify-person/run", payload, timeout=120)

    def enrich_company(self, domain):
        """Deep company enrichment via agents/enrich-company/run."""
        payload = {
            "agentOId": config.OCTAVE_ENRICH_COMPANY_AGENT,
            "companyDomain": domain,
        }
        return self._post("/agents/enrich-company/run", payload, timeout=180)

    def enrich_person(self, person):
        """Deep person enrichment via agents/enrich-person/run."""
        payload = {
            "agentOId": config.OCTAVE_ENRICH_PERSON_AGENT,
            "person": person,
        }
        return self._post("/agents/enrich-person/run", payload, timeout=180)
