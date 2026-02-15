# The Oracle of Cold Calls

**An AI-powered cold call preparation engine built by a Head of Sales who got tired of waiting for someone else to fix the workflow.**

I run outbound at [Octave](https://octavehq.com). Every morning before I pick up the phone, I need three things for every prospect on my call list: a tight voicemail script, a live call script with a pattern interrupt opener, and 5-7 prospect-specific objections with responses. For 50+ contacts. By 7 AM.

No tool did this. So I built it.

---

## What This Does

The Oracle connects to your HubSpot CRM and Octave AI to generate hyper-personalized cold call content for every contact on your dial list. It then writes structured HTML notes directly to each contact record in HubSpot and produces a time-zone-optimized call sheet so you know exactly who to call and when.

**The pipeline:**

```
HubSpot Segment List
    |
    v
Pull contacts + filter by logged outbound email
    |
    v
Octave AI generates per-contact:
  - Voicemail script (max 35 seconds)
  - Live call script (OPENER > HOOK > ASK > ENGAGE > SHUT IT DOWN)
  - 5-7 objections with response options
    |
    v
QA Review (approve, reject, or edit before writing)
    |
    v
Write formatted HTML notes to HubSpot contact records
    |
    v
Time-zone-optimized call sheet posted to Slack
```

**Every call script is built from the actual email the prospect received.** The phone is not email. You never repeat what the email said. You pull one sharp thread as a hook and pivot to the meeting ask. The Oracle enforces this by using the logged HubSpot email as runtime context for the AI agent.

---

## The Philosophy

Most sales tools treat cold calling as an afterthought. You get a list of names and phone numbers and figure out the rest. The research happens in your head between dials, and the quality drops after the first hour because you're mentally exhausted from context-switching between prospects.

The Oracle flips this. Every contact is fully prepped before you touch the phone. You read the note, you dial, you execute. Your brain does the selling, not the research.

**My cold call pattern (baked into every script):**

> "Full transparency, this is a cold call. You're welcome to hang up on me, I genuinely will not be offended."

Gets a laugh. Softens the prospect. Creates a window. Then:

> "That email you got was written entirely by our AI. Every word. It's a demo of what we do for GTM teams. If that precision landed for you as a buyer, imagine what it does when your reps have it."

The email IS the demo. The call closes the loop.

---

## Features

- **Segment-based generation** - Pick a HubSpot list, generate scripts for everyone on it
- **Email-as-context** - Each script is informed by the actual outbound email the prospect received
- **Time-zone optimization** - Call sheet orders contacts by their local time, optimized for connect rates (8-10 AM local is prime, 11 AM-2 PM is dead zone)
- **Structured HTML notes** - Formatted with emoji section headers, clean typography, bullet-point objection responses. Looks professional in HubSpot.
- **QA gate** - Review every script before it writes to HubSpot. No auto-publish without approval.
- **Progressive saving** - If generation fails halfway, your progress is saved. Resume where you left off.
- **Slack integration** - Posts the daily call plan to your Slack channel automatically
- **Note cleanup tool** - Scans for and archives duplicate/outdated prep notes from HubSpot

---

## Tech Stack

- **Backend:** Python / Flask with SSE (Server-Sent Events) for real-time progress streaming
- **Frontend:** Single-page HTML. The aesthetic is 90s GeoCities meets Greek mythology. Torches, fire dividers, gold text on black. Because outbound is war and your tools should look like it.
- **AI:** [Octave](https://octavehq.com) content agents for script generation
- **CRM:** HubSpot (contacts, companies, notes, email history)
- **Notifications:** Slack webhooks

---

## Setup

### Prerequisites

- Python 3.9+
- A HubSpot account with API access (Private App token)
- An Octave account with a configured cold call content agent
- A Slack webhook URL (optional, for call sheet notifications)

### Install

```bash
git clone https://github.com/antonio0925/oracle-of-cold-calls.git
cd oracle-of-cold-calls
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Configure

Copy the example environment file and add your credentials:

```bash
cp .env.example .env
```

Edit `.env` with your API keys:

```
HUBSPOT_ACCESS_TOKEN=your-hubspot-private-app-token
OCTAVE_API_KEY=your-octave-api-key
SLACK_WEBHOOK_URL=your-slack-webhook-url
```

### Run

```bash
python app.py
```

Navigate to `http://localhost:5001`

---

## How It Works

### Phase 1-2: Generate

Select a HubSpot segment list and campaign. The Oracle pulls every contact, finds their most recent outbound email, and sends it to the Octave AI agent as runtime context. The agent generates three outputs per contact: voicemail, live call script, and objection handling.

### Review

Every contact's generated content is displayed in expandable cards. Click to review. The formatted HTML you see is exactly what gets written to HubSpot.

### Phase 3: Release the Kraken

Hit the button. The Oracle writes formatted notes to every contact record in HubSpot, then posts the time-zone-optimized call sheet to Slack.

---

## What's Coming: The Forge

The Oracle solves the last mile: turning prepped contacts into call-ready scripts. But the upstream pipeline (prospecting, qualification, enrichment, email generation, delivery) currently runs through Clay and manual steps.

**The Forge** is the upstream automation engine being built into this same app. It reads campaign briefs from Notion, runs AI-powered prospecting and qualification through Octave, handles contact enrichment, generates personalized email content, pushes to email delivery via SuperSend, syncs everything to HubSpot, and then hands off to The Oracle for the cold call layer.

One app. The Forge builds the army. The Oracle arms them.

---

## Why I Built This

I'm the Head of Sales at Octave. Our product helps sales teams generate personalized outreach at scale using AI. Instead of just selling it, I use it every day to run my own outbound. This repo is the infrastructure I built to make that work.

Every morning at 6:30 AM, I run The Oracle. By 7 AM, I have 50+ contacts fully prepped with personalized scripts in HubSpot and a sequenced call sheet in Slack. I make coffee, open the call sheet, and start dialing.

If you run outbound and you're tired of winging it on the phone, fork this and make it yours. Or just steal the ideas. I don't care. The bar for cold calling is on the floor. Let's raise it.

---

## License

MIT. Do whatever you want with it.

---

*Built with [Claude Code](https://claude.ai/code) and [Octave](https://octavehq.com). The entire codebase was pair-programmed with AI, which is fitting for a tool that uses AI to prepare for human conversations.*
