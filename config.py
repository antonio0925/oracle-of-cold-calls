"""
Centralised configuration — single source of truth for all env vars.

Every module imports from here instead of calling os.getenv() directly.
load_dotenv() is called exactly once, at import time.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# --- Required API Keys ---
HUBSPOT_ACCESS_TOKEN = os.getenv("HUBSPOT_ACCESS_TOKEN", "")
OCTAVE_API_KEY = os.getenv("OCTAVE_API_KEY", "")
NOTION_API_KEY = os.getenv("NOTION_API_KEY", "")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")

# --- Octave Agent OIDs ---
OCTAVE_CONTENT_AGENT = os.getenv("OCTAVE_CONTENT_AGENT", "ca_DLoI5XBlw9qGNEDBiV1a2")
OCTAVE_QUALIFY_COMPANY_AGENT = os.getenv("OCTAVE_QUALIFY_COMPANY_AGENT", "ca_rCvK8bkJaM92LaocJVWJj")
OCTAVE_QUALIFY_PERSON_AGENT = os.getenv("OCTAVE_QUALIFY_PERSON_AGENT", "ca_7VSc6ryMeAY7xPj4sVeYn")
OCTAVE_PROSPECTOR_AGENT = os.getenv("OCTAVE_PROSPECTOR_AGENT", "ca_cHVXNhMbGZQ7L6qdFz6Kr")
OCTAVE_ENRICH_COMPANY_AGENT = os.getenv("OCTAVE_ENRICH_COMPANY_AGENT", "ca_GsNZuDXi1K3zmgtKLOAif")
OCTAVE_ENRICH_PERSON_AGENT = os.getenv("OCTAVE_ENRICH_PERSON_AGENT", "ca_VKO6KxdcyO7MDA0j1nL7z")

# --- HubSpot ---
HUBSPOT_PORTAL_ID = os.getenv("HUBSPOT_PORTAL_ID", "46940643")
HUBSPOT_CREATOR_ID = os.getenv("HUBSPOT_CREATOR_ID", "87514817")

# --- Notion ---
NOTION_CAMPAIGNS_DB_ID = os.getenv("NOTION_CAMPAIGNS_DB_ID", "8224ce5d13dd4db69a2618476d527910")
# Legacy page ID — kept for reference, no longer used by list_campaigns()
NOTION_CAMPAIGNS_PAGE_ID = os.getenv("NOTION_CAMPAIGNS_PAGE_ID", "2f8c1b1ae5518079b71bdf94212cbda6")

# --- Slack ---
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID", "C0AELNTNNDV")

# --- User Timezone ---
USER_TIMEZONE = os.getenv("USER_TIMEZONE", "US/Pacific")
USER_START_HOUR = float(os.getenv("USER_START_HOUR", "6.5"))  # 6:30 AM

# --- Thresholds ---
QUAL_THRESHOLD = int(os.getenv("QUAL_THRESHOLD", "8"))

# --- Oracle v2: Supersend + Signal Pipeline ---
SUPERSEND_API_KEY = os.getenv("SUPERSEND_API_KEY", "")
ORACLE_WEBHOOK_SECRET = os.getenv("ORACLE_WEBHOOK_SECRET", "")
SIGNAL_WEBHOOK_API_KEY = os.getenv("SIGNAL_WEBHOOK_API_KEY", "")
OCTAVE_CALL_PREP_AGENT = os.getenv("OCTAVE_CALL_PREP_AGENT", "ca_DLoI5XBlw9qGNEDBiV1a2")

# --- Server ---
FLASK_PORT = int(os.getenv("FLASK_PORT", "5001"))
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "true").lower() in ("true", "1", "yes")
