# HANDOFF: SUMMIT ship-out (strip Supersend, add auth, deploy)

Executor: this document is your work order. Read it fully before editing.
Written by a prior Claude session that reviewed, hardened, and reskinned this app.
Antonio communicates in ASD-STE100 register: short literal sentences, no idioms,
no em dashes anywhere, including UI copy and commit messages.

## Current state

- Repo: /Users/antoniogarcia/oracle-of-cold-calls, branch `fix/review-top-three`,
  open PR #11. Work on this branch; push updates to the same PR.
- Flask app (app.py, ~2100 lines) + services/ modules + one template
  (templates/index.html, "SUMMIT" alpine theme with a mountain-progress
  gamification hero). 33-test pytest suite in tests/.
- Setup: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest`
- Verify before you start: `.venv/bin/python -m pytest tests/ -q` (33 pass) and
  `.venv/bin/python -c "import app"` (30 routes).
- A prior instance may still be serving on port 5001 from a scratchpad clone.
  Kill it before running yours: `pkill -f "python app.py"`.

## Rules

1. Do not rename or remove any HTML element id or class that the template's
   `<script>` block references. The mountain component depends on `.battle-card`,
   `.completed`, `#trailPath`, and the battle counter ids.
2. Run the pytest suite after every task. Add tests for what you change.
3. Conventional commits. End commit messages with the Claude Code co-author line.
4. No em dashes in any user-visible string.
5. Do not touch the four already-landed commits. Additive commits only.
6. `op` (1Password CLI) works in this environment, service account, read-only,
   vault `sales-brain`. Never print full secret values to the terminal or logs.

## Task 1: Strip Supersend campaign enrollment

Antonio's direction: campaign enrollment does not matter. Remove the Supersend
choreography. The tool's job is: HubSpot list in, call sheet + scripts out,
dispositions logged back to HubSpot.

1. In `api_action_complete` (app.py, route `/api/action/complete`): delete the
   entire Supersend execution block (the `if config.SUPERSEND_API_KEY and
   route["action"] in (...)` block, the supersend_error handling, and the 502
   branch). Dispositions now only: update oracle_ properties, append the journey
   log, return ok.
2. do_not_call replacement (compliance, do not skip): when disposition is
   `do_not_call`, also set the standard HubSpot property `donotcall` to `true`
   in the same `update_contact_properties` call. Change the route's log_entry in
   services/routing_config.py from "Do Not Call — permanently removed" to
   "Do Not Call: marked donotcall in HubSpot". Note the em dash in the old
   string; it must not survive.
3. services/routing_config.py: keep the dispositions and log entries (the UI
   dropdown uses them). The `action`, `next_step`, `transfer_to`, `delay_hours`
   keys become inert metadata; leave them, they are harmless, but update the
   module docstring to say routing is log-only now.
4. Remove the VM follow-up flow (it exists only to push Supersend emails):
   the `/api/vm-followup` route(s) in app.py, its UI section in the template
   (find the panel and its toggle; delete both), and
   services/anthropic.py's usage if nothing else imports it. If removing it
   orphans ANTHROPIC_API_KEY, drop it from the config warn-list and .env.example.
5. Remove the `/api/webhook/supersend-task` route. Keep `/api/webhook/signal`
   (that is HubSpot-side signal ingestion, not campaign enrollment) and keep its
   fail-closed auth.
6. Keep services/supersend.py on disk (the refactor may revive it) but nothing
   may import it after this task. Delete tests/test_supersend.py and the
   routing test assertions about EXECUTED_ACTIONS; replace with a test that
   do_not_call sets donotcall=true (mock HubSpotClient).
7. Grep-verify: `grep -rn "SupersendClient\|SUPERSEND" app.py` returns nothing
   (config.py entries may stay; remove them from .env.example).

## Task 2: Password gate (required before any public deploy)

The UI routes are unauthenticated. A public URL without a gate exposes
production HubSpot writes to the internet.

1. Add `SUMMIT_PASSWORD` to config.py (no default; empty means the gate
   rejects everything except /login with a clear message) and .env.example.
2. Flask session cookie auth: a minimal `/login` page (style it with the
   existing alpine CSS variables; it is the first thing the BDR sees, so make
   it a small centered card with the SUMMIT logo), a `before_request` guard
   exempting `/login` and `/static/*`, and `app.secret_key` from a new
   `FLASK_SECRET_KEY` env var (no hardcoded fallback; generate the value at
   deploy time).
3. The two webhook routes must stay reachable without the cookie; they have
   their own header auth. Exempt paths starting with `/api/webhook/`.
4. Constant-time compare for the password (hmac.compare_digest). Add a test:
   unauthenticated request to `/` redirects to /login; wrong password 401;
   right password sets the session and reaches `/`.

## Task 3: Deploy for the BDR

Recommendation to present to Antonio before executing: Render or Railway, not
Vercel. Reasons: sessions/ is a local-disk JSON store (Vercel functions have an
ephemeral filesystem, resume and retry would silently break) and /generate
holds a single SSE response through many 30-60s Octave calls (function duration
limits kill it). On Render/Railway the app runs unmodified with a persistent
disk. If Antonio still chooses Vercel, stop and tell him the sessions store
must first move to a hosted KV and generation must move to polling; that is
refactor-sized, not tonight-sized.

Primary path: Railway via its CLI. Antonio installs it with `brew install
railway` and pre-authenticates with `railway login` before your session. Verify
auth with `railway whoami` first; if it fails, stop and ask him to run
`! railway login`. Use `railway init` (project name: summit), `railway up` or
GitHub-connected deploy, `railway variables set` for the env vars, and attach a
volume for `sessions/`. The CLI also bundles an MCP server (`railway mcp`) if
tool-based control is easier. Render is the fallback; steps below map 1:1:
1. Add `gunicorn` to requirements.txt and a `Procfile`:
   `web: gunicorn app:app --workers 1 --threads 8 --timeout 0`
   (threads + no timeout because of the long SSE streams; one worker because
   sessions and dedup state are in-process).
2. Create the service with a persistent disk mounted where the app runs so
   `sessions/` survives restarts.
3. Environment variables, sourced from 1Password vault `sales-brain`
   (`op item list --vault sales-brain` to find items; pipe values directly into
   the platform CLI, never echo them): HUBSPOT_ACCESS_TOKEN, OCTAVE_API_KEY,
   NOTION_API_KEY, SLACK_WEBHOOK_URL, SIGNAL_WEBHOOK_API_KEY,
   ORACLE_WEBHOOK_SECRET (generate fresh: `openssl rand -hex 32`),
   SUMMIT_PASSWORD (generate, give to Antonio to pass to the BDR),
   FLASK_SECRET_KEY (generate). FLASK_DEBUG=false.
4. Smoke test the deployed URL: /login gate works, wrong password rejected,
   after login the three views render, the mountain hero renders.
5. Give Antonio: the URL, the SUMMIT_PASSWORD value location (write it to a new
   item in the sales-brain vault if op is writable; it is read-only, so
   instead print instructions for Antonio to store it), and one paragraph of
   BDR instructions in plain language.

## Task 4: Close out

1. Push the branch, confirm PR #11 updated, comment on the PR with what changed.
2. Run the full pytest suite and a Playwright click-through (base camp, mid,
   summit states; pattern for simulating progress is in the git history of the
   prior session; simplest: inject hidden `.battle-card completed` divs and
   call `updateMountainProgress()`).
3. Report to Antonio in ASD-STE100: what shipped, the URL, what the BDR does
   on day one, what was removed, what is deferred.

## Deferred (do not do tonight)

- App-wide refactor (strangler plan: routes/workflows/integrations/policies/
  state/auth, disposition slice first). Hermes/Nous agent surface goes on top
  of the refactored API later, never underneath.
- Backend SSE flavor text still speaks Greek mythology inside the log panels.
  Harmless; the refactor owns copy.
- timezone.py state-level ambiguity (KY spans two zones; state-level mapping
  picks Central). Area-code layer already corrected.
