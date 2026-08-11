"""
Centralised configuration — single source of truth for all env vars.

Every module imports from here instead of calling os.getenv() directly.
load_dotenv() is called exactly once, at import time.
"""
import os
import logging
from dotenv import load_dotenv

_log = logging.getLogger(__name__)

load_dotenv(override=True)

# --- Required API Keys ---
HUBSPOT_ACCESS_TOKEN = os.getenv("HUBSPOT_ACCESS_TOKEN", "")
OCTAVE_API_KEY = os.getenv("OCTAVE_API_KEY", "")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")

# --- Octave Agent OIDs ---
OCTAVE_CONTENT_AGENT = os.getenv("OCTAVE_CONTENT_AGENT", "ca_DLoI5XBlw9qGNEDBiV1a2")

# --- HubSpot ---
HUBSPOT_PORTAL_ID = os.getenv("HUBSPOT_PORTAL_ID", "46940643")
HUBSPOT_CREATOR_ID = os.getenv("HUBSPOT_CREATOR_ID", "87514817")

# --- Slack ---
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID", "C0AELNTNNDV")

# --- User Timezone ---
USER_TIMEZONE = os.getenv("USER_TIMEZONE", "US/Pacific")
USER_START_HOUR = float(os.getenv("USER_START_HOUR", "6.5"))  # 6:30 AM

# --- Call target ---
# How many calls a BDR plans for the day. The route builder stops here, so a
# 400-contact list does not cost 400 Octave calls to work 50.
DEFAULT_CALL_TARGET = int(os.getenv("DEFAULT_CALL_TARGET", "50"))
MAX_CALL_TARGET = int(os.getenv("MAX_CALL_TARGET", "500"))

# --- Call pacing ---
# Never burn an account in one day. Two people per company, maximum.
MAX_CONTACTS_PER_ACCOUNT_PER_DAY = int(os.getenv("MAX_CONTACTS_PER_ACCOUNT_PER_DAY", "2"))
# Days a contact rests after any logged call, voicemail included. 1 means a
# contact called yesterday is not dialable today, but one called two days
# ago is.
CALL_COOLDOWN_DAYS = int(os.getenv("CALL_COOLDOWN_DAYS", "1"))

# --- Thresholds ---
QUAL_THRESHOLD = int(os.getenv("QUAL_THRESHOLD", "8"))

# --- Octave ---
OCTAVE_CALL_PREP_AGENT = os.getenv("OCTAVE_CALL_PREP_AGENT", "ca_DLoI5XBlw9qGNEDBiV1a2")

# --- Legacy, unused ---
# Campaign enrollment was removed and its modules were deleted. These keys
# are read only so an old .env does not break a boot. Do not set them.
SUPERSEND_API_KEY = os.getenv("SUPERSEND_API_KEY", "")
SUPERSEND_TEAM_ID = os.getenv("SUPERSEND_TEAM_ID", "")
SUPERSEND_CAMPAIGN_ID = os.getenv("SUPERSEND_CAMPAIGN_ID", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# --- Auth ---
# Shared password for the UI. There is no default on purpose: an empty
# value makes the gate reject every login attempt instead of opening the
# app to the internet.
SUMMIT_PASSWORD = os.getenv("SUMMIT_PASSWORD", "")
# Signs the session cookie. No hardcoded fallback: a known secret key lets
# anyone forge a logged-in session. Generate one per deployment.
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "")

# --- Server ---
FLASK_PORT = int(os.getenv("FLASK_PORT", "5001"))
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "true").lower() in ("true", "1", "yes")

# --- Validation ---
_VALID_TIMEZONES = {"US/Eastern", "US/Central", "US/Mountain", "US/Pacific", "US/Alaska", "US/Hawaii"}

if not 1 <= QUAL_THRESHOLD <= 10:
    _log.warning("QUAL_THRESHOLD=%d is outside 1-10 range, clamping", QUAL_THRESHOLD)
    QUAL_THRESHOLD = max(1, min(10, QUAL_THRESHOLD))

if not 1 <= FLASK_PORT <= 65535:
    _log.warning("FLASK_PORT=%d is outside valid range, defaulting to 5001", FLASK_PORT)
    FLASK_PORT = 5001

if USER_TIMEZONE not in _VALID_TIMEZONES:
    _log.warning("USER_TIMEZONE='%s' not recognized, defaulting to US/Pacific", USER_TIMEZONE)
    USER_TIMEZONE = "US/Pacific"

for _key_name in ("HUBSPOT_ACCESS_TOKEN", "OCTAVE_API_KEY"):
    if not locals()[_key_name]:
        _log.warning("Required key %s is empty. Some features will be unavailable.", _key_name)
