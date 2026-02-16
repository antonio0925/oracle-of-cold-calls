# The Oracle of Cold Calls & The Forge

**An AI-powered cold call preparation engine and prospecting pipeline built by a Head of Sales who got tired of waiting for someone else to fix the workflow.**

I run outbound at [Octave](https://octavehq.com). Every morning before I pick up the phone, I need three things for every prospect on my call list: a tight voicemail script, a live call script with a pattern interrupt opener, and 5-7 prospect-specific objections with responses. For 50+ contacts. By 7 AM.

No tool did this. So I built it.

---

## What This Does

Two views. One app.

**The Oracle** connects to your HubSpot CRM and Octave AI to generate hyper-personalized cold call content for every contact on your dial list. It writes structured HTML notes directly to each contact record and produces a time-zone-optimized call sheet so you know exactly who to call and when.

**The Forge** is the upstream automation engine. It reads campaign briefs from Notion, discovers and qualifies companies through Octave AI, enriches contacts, and hands everything off to The Oracle for the cold call layer.

**The Battle Plan** is your real-time call dashboard. Contacts are bucketed into Hot/Warm/Parked tiers based on signal classification, with inline disposition tracking and one-click sequence routing through Supersend.

---

## Architecture

```
templates/index.html          Single-page frontend (GeoCities meets Greek mythology)
app.py                        Flask routes + SSE generators
config.py                     Environment config with validation
services/
  hubspot.py                  HubSpot API client (lists, contacts, emails, notes)
  notion.py                   Notion API client (campaign briefs)
  octave.py                   Octave API client (scripts, qualification, prospecting)
  formatting.py               Markdown-to-HTML note formatting
  call_sheet.py               Time-zone-optimized call sheet builder
  timezone.py                 Timezone resolution (HubSpot > state > area code)
  sessions.py                 Dual-layer session store (in-memory + JSON files)
  filters.py                  US-only filtering for Forge pipeline
  dedup.py                    Signal deduplication with TTL
  signal_classifier.py        Signal-to-tier classification (Hot/Warm/Parked)
  routing_config.py           Disposition-to-Supersend sequence routing
  slack.py                    Slack dial sheet posting
  supersend.py                Supersend sequence API client
  retry.py                    Exponential-backoff HTTP retry utility
```

---

## The Oracle Pipeline

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

## The Forge Pipeline

The Forge automates the upstream prospecting pipeline that feeds The Oracle. It's designed as a Claude-orchestrated workflow: Claude handles the MCP-powered discovery steps, then hands domains to the Flask backend for qualification and enrichment.

```
Notion Campaign Brief
    |
    v
Claude discovers companies via Octave MCP
(find_company, find_similar_companies)
    |
    v
POST /api/forge/start (Claude injects domains)
    |
    v
Stage 1: UI auto-detects session via polling
    |
    v
Stage 2: Qualify companies (Octave REST, score >= 8/10, US-only)
    |                    [QA Gate: human reviews qualified companies]
    v
Stage 3: Enrich qualified companies (Octave REST)
    |                    [QA Gate: human reviews enriched data]
    v
Stage 4: Discover & enrich people (Octave Prospector + Enrich Person)
    |                    [QA Gate: human reviews discovered contacts]
    v
Stage 5: Export to HubSpot + Supersend
```

Every stage has a human-in-the-loop QA gate. Nothing writes to your CRM without approval.

---

## The Battle Plan

Real-time call dashboard with signal-based contact prioritization:

- **Hot (Tier 1):** Demo requests, pricing page visits, high-intent signals. Call immediately.
- **Warm (Tier 2):** Feature exploration, content engagement. Enrich then decide.
- **Parked (Tier 3):** Low-signal contacts. Keep in sequence, revisit later.

Inline disposition tracking lets you log call outcomes and route contacts to Supersend sequences without leaving the dashboard.

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

### The Oracle
- **Segment-based generation** - Pick a HubSpot list, generate scripts for everyone on it
- **Email-as-context** - Each script is informed by the actual outbound email the prospect received
- **Time-zone optimization** - Call sheet orders contacts by local time, optimized for connect rates (8-10 AM local is prime, 11 AM-2 PM is dead zone)
- **Structured HTML notes** - Formatted with emoji section headers, clean typography, bullet-point objection responses
- **QA gate** - Review every script before it writes to HubSpot. No auto-publish without approval.
- **Progressive saving** - If generation fails halfway, your progress is saved. Resume where you left off.
- **Slack integration** - Posts the daily call plan to your Slack channel automatically
- **Note cleanup tool** - Scans for and archives duplicate/outdated prep notes from HubSpot

### The Forge
- **Notion campaign briefs** - Reads ICP, personas, target titles, keywords, and value props from your Campaign Central
- **Claude-orchestrated company discovery** - MCP-powered search via Octave's find_company and find_similar_companies
- **Automated qualification** - Scores companies against your ICP (threshold configurable, default 8/10)
- **US-only filtering** - Filters at both company and person level
- **Multi-stage enrichment** - Company enrichment, then people discovery, then person enrichment
- **QA gates at every stage** - Human reviews every batch before proceeding
- **Manual domain fallback** - Hidden toggle for power users who want to add domains directly

### The Battle Plan
- **Signal-based tiering** - Contacts automatically classified into Hot/Warm/Parked
- **Inline dispositions** - Log call outcomes without leaving the dashboard
- **Supersend routing** - One-click sequence assignment based on disposition
- **Real-time activity refresh** - Auto-polling for new HubSpot activity signals

---

## Tech Stack

- **Backend:** Python / Flask with SSE (Server-Sent Events) for real-time progress streaming
- **Frontend:** Single-page HTML with a 90s GeoCities meets Greek mythology aesthetic. Torches, fire dividers, gold text on black. Because outbound is war and your tools should look like it.
- **AI:** [Octave](https://octavehq.com) for script generation, qualification, prospecting, and enrichment
- **CRM:** HubSpot (contacts, companies, notes, email history)
- **Campaign Management:** Notion (campaign briefs and ICP definitions)
- **Notifications:** Slack webhooks (call sheets and battle plans)
- **Email Sequences:** Supersend (disposition-based sequence routing)

---

## Setup

### Prerequisites

- Python 3.9+
- A HubSpot account with API access (Private App token)
- An Octave account with configured agents (content, qualify, prospector, enrich)
- A Notion workspace with a Campaign Central database
- A Slack webhook URL (optional, for call sheet notifications)
- A Supersend account (optional, for sequence routing)

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

Edit `.env` with your API keys and agent OIDs:

```
HUBSPOT_ACCESS_TOKEN=your-hubspot-private-app-token
OCTAVE_API_KEY=your-octave-api-key
NOTION_API_KEY=your-notion-api-key
SLACK_WEBHOOK_URL=your-slack-webhook-url

# Octave Agent OIDs (from your Octave workspace)
OCTAVE_CONTENT_AGENT=your-content-agent-oid
OCTAVE_QUALIFY_COMPANY_AGENT=your-qualify-company-agent-oid
OCTAVE_QUALIFY_PERSON_AGENT=your-qualify-person-agent-oid
OCTAVE_PROSPECTOR_AGENT=your-prospector-agent-oid
OCTAVE_ENRICH_COMPANY_AGENT=your-enrich-company-agent-oid
OCTAVE_ENRICH_PERSON_AGENT=your-enrich-person-agent-oid
```

See `.env.example` for the full list of configuration options.

### Run

```bash
python app.py
```

Navigate to `http://localhost:5001`

---

## How It Works

### The Oracle

1. **Select** a HubSpot segment list and campaign
2. **Generate** - The Oracle pulls every contact, finds their most recent outbound email, and sends it to Octave as runtime context. The agent generates voicemail, live call script, and objection handling per contact.
3. **Review** - Every contact's generated content is displayed in expandable cards. The formatted HTML you see is exactly what gets written to HubSpot.
4. **Release the Kraken** - Writes formatted notes to every contact record in HubSpot, then posts the time-zone-optimized call sheet to Slack.

### The Forge

1. **Select** a campaign from your Notion Campaign Central
2. **Brief loads** with ICP, personas, target titles, and keywords
3. **Tell Claude** to forge the campaign - Claude discovers companies via Octave MCP tools and injects domains into the app
4. **Qualify** - The app scores each company and filters to US-only, 8+/10
5. **Enrich** companies, then discover and enrich people at qualified companies
6. **Export** approved contacts to HubSpot and Supersend sequences

### The Battle Plan

Navigate to the Battle Plan tab to see your contacts prioritized by signal tier. Click any contact to open the disposition panel, log your call outcome, and route them to the appropriate sequence.

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
