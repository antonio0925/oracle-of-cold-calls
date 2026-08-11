# SUMMIT

**A cold call preparation engine built by a Head of Sales who got tired of waiting for someone else to fix the workflow.**

I run outbound at [Octave](https://octavehq.com). Every morning before I pick up the phone, I need three things for every prospect on my call list: a tight voicemail script, a live call script with a pattern interrupt opener, and prospect-specific objections with responses. For 50+ contacts. By 7 AM.

No tool did this. So I built it.

---

## What This Does

One job: **a HubSpot list goes in, a worked call list comes out.**

1. Pick the HubSpot list you are calling today.
2. SUMMIT pulls the contacts, works out each person's time zone, and has Octave write a voicemail script, a live call script, and objection handling for each one.
3. You review the route, then write the prep notes to HubSpot.
4. On **Today's Climb** you work the list top to bottom. Open the script, dial, log what happened. The card ticks off and the outcome lands on the contact record.

Two views: **Route Plan** builds the list, **Today's Climb** works it.

---

## The pipeline

```
HubSpot list
    |
    v
Pull contacts, apply the pacing rules
    |
    v
Octave generates per contact:
  - Voicemail script (max 35 seconds)
  - Live call script (OPENER > HOOK > ASK > ENGAGE > SHUT IT DOWN)
  - Objections with response options
    |
    v
Review the route, then write HTML notes to the contact records
    |
    v
Today's Climb: grouped by time zone, checked off as you dial
```

Where a logged outbound email exists, it is passed to Octave as runtime context, because the phone is not email. You never repeat what the email said. You pull one sharp thread as a hook and pivot to the meeting ask. Contacts with no logged email still get a script, built from their profile.

---

## Call pacing

Two rules stop the tool burning your list. Both are enforced when the route is built, and skipped contacts are shown with the reason.

- **Two contacts per account per day** (`MAX_CONTACTS_PER_ACCOUNT_PER_DAY`). Never burn a whole account in one morning.
- **One day of cooldown after any logged call** (`CALL_COOLDOWN_DAYS`), voicemail included. Called yesterday or today, they rest. Two days apart is fine.

---

## Today's Climb

The call list is the session you just generated, not a CRM query. Completion is recorded in that session file, which sits on a persistent disk in the deployed service, so the list checks itself off across refreshes, reconnects, and restarts.

Logging an outcome records it in the session **first**, then writes it to HubSpot as a note. That order is deliberate: a HubSpot outage cannot make you lose your place. If the HubSpot write fails you are told, and the card stays checked off.

`do_not_call` also sets the standard HubSpot `donotcall` property, in its own request. That is a legal request, and a note alone stops nobody else from dialling.

---

## Architecture

```
app.py                        Flask routes + SSE generators (19 routes)
config.py                     Every env var, read once
services/
  hubspot.py                  HubSpot API client
  octave.py                   Octave AI agent client
  sessions.py                 Session store + the completion record
  call_sheet.py               Time-zone bucketing and seniority ordering
  timezone.py                 Area code and state to time zone resolution
  formatting.py               Octave markdown to HubSpot HTML
  filters.py                  US-only filtering
  routing_config.py           Disposition to journey-log routing (log-only)
  slack.py                    Call sheet posting
  retry.py                    Exponential-backoff HTTP retry
templates/
  index.html                  The app: Route Plan + Today's Climb
  login.html                  Password gate
tests/                        64 tests
verify.sh                     tests -> compile -> boot
```

---

## Auth

The whole app sits behind a shared password (`SUMMIT_PASSWORD`), because every route writes to production HubSpot. `/login`, `/healthz`, and `/static/*` are the only public paths. `/api` routes answer 401 JSON rather than redirecting, so a fetch never fails as a parse error.

`SUMMIT_PASSWORD` has no default. Empty means every login is rejected, never accepted.

---

## Setup

### Prerequisites

- Python 3.9+
- A HubSpot account with API access (Private App token)
- An Octave account with a content agent configured
- A Slack webhook URL (optional, for call sheet notifications)

### Install

```bash
git clone https://github.com/antonio0925/oracle-of-cold-calls.git
cd oracle-of-cold-calls
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
```

```
HUBSPOT_ACCESS_TOKEN=your-hubspot-private-app-token
OCTAVE_API_KEY=your-octave-api-key
OCTAVE_CONTENT_AGENT=your-content-agent-oid
SLACK_WEBHOOK_URL=your-slack-webhook-url

SUMMIT_PASSWORD=            # openssl rand -base64 18
FLASK_SECRET_KEY=           # openssl rand -hex 32
```

See `.env.example` for the full list.

### HubSpot properties

The disposition `do_not_call` writes the standard `donotcall` contact property. If your portal does not have it, create it as a boolean checkbox before going live, or that outcome is recorded only as a note.

### Run

```bash
python app.py            # http://localhost:5001
./verify.sh              # tests, compile, boot
```

### Deploy

Render or Railway, not Vercel. `sessions/` is a local-disk JSON store and `/generate` holds one SSE response open across many 30 to 60 second Octave calls. Vercel functions have an ephemeral filesystem and a duration limit, so completion state would vanish and long generations would be killed.

```
web: gunicorn app:app --bind 0.0.0.0:${PORT:-5001} --workers 1 --threads 8 --timeout 0
```

One worker, because sessions are in-process state. `--timeout 0` for the SSE streams. Mount a persistent disk at `sessions/`.

---

## Why I Built This

I'm the Head of Sales at Octave. Our product helps sales teams generate personalized outreach at scale using AI. Instead of just selling it, I use it every day to run my own outbound. This repo is the infrastructure I built to make that work.

Every morning at 6:30 AM, I build the route. By 7 AM, I have contacts fully prepped with personalized scripts in HubSpot and a time-zone-ordered call list ready to work. I make coffee, open the list, and start dialing.

If you run outbound and you're tired of winging it on the phone, fork this and make it yours. Or just steal the ideas. I don't care. The bar for cold calling is on the floor. Let's raise it.

---
