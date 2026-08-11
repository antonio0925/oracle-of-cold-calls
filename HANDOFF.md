# HANDOFF: SUMMIT, for a fresh session doing bug fixes

Antonio writes in ASD-STE100 register: short literal sentences, one idea each,
no idioms, **no em dashes anywhere**, including UI copy and commit messages.
Match it.

This file replaces an earlier handoff that described Supersend, campaigns, and
the Forge. None of that exists. If you find writing that references them, it is
stale and you should fix it.

---

## What the app is

One job: **a HubSpot contact list goes in, a worked call list comes out.**

1. Antonio picks a HubSpot contact list and a daily call target (default 50).
2. `/generate` walks the list, applies filters, and asks one Octave agent for a
   voicemail script, a live call script, and objection handling per contact. It
   stops at the target.
3. He reviews, then presses START THE CLIMB, which writes a prep note onto each
   HubSpot contact record. **`/generate` writes nothing to HubSpot.** Only the
   approve step does.
4. On **Today's Climb** the BDR (Theresa) works the list, logs each outcome, and
   the card ticks off. The outcome becomes a HubSpot note. `do_not_call` also
   sets the standard `donotcall` property.

Two views: **Route Plan** builds the list, **Today's Climb** works it.

## Live

- **URL:** https://summit-production-a582.up.railway.app
- **Password:** not in this file on purpose. Get it with
  `railway variables --service summit --kv | grep SUMMIT_PASSWORD`, or ask Antonio.
- **Railway project:** `summit`, service `summit`, volume `summit-volume`
  mounted at `/app/sessions`. One gunicorn worker, 8 threads, `--timeout 0`.
- **Branch:** `fix/review-top-three`, PR #11. Work here and push to the same PR.

## Run and verify

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
./verify.sh                 # pytest -> compileall -> Flask boot probe
.venv/bin/python app.py     # http://localhost:5001
```

`.env` is gitignored and already holds a local `SUMMIT_PASSWORD=climb`. Without
it the gate rejects every login, which is correct behaviour and looks like a bug.

**Deploy:** `railway up --service summit --ci`. Then check
`/healthz`, which reports `timezone_resolved`, `work_day`, and `utc_day`.

101 tests pass. 18 routes.

---

## Traps that already cost time

Read these before you debug anything. Each one cost a real detour.

**1. `/generate` writes nothing.** If someone says "the notes are not on the
contact", the usual cause is that START THE CLIMB was never pressed, not a
broken write. Check the server log for `POST /approve`. There is now a banner on
load that offers any unwritten plan back, because the write step used to be
unreachable after a reload.

**2. The Octave agent has a second, hidden set of instructions.** The agent
`ca_DLoI5XBlw9qGNEDBiV1a2` has `data.tools.crmActivity.customInstructions`,
which silently overrides the main prompt. Editing only `data.instructions`
changes nothing. The reviewable copy of the prompt is
`docs/octave-agent-instructions.md`; the live copy is in Octave and is the one
that runs. Read and write it with:

```
GET  https://app.octavehq.com/api/v2/agents/get?oId=<oid>     header: api_key
POST https://app.octavehq.com/api/v2/agents/update            body: {oId, data:{...}}
```

Partial updates work. `examples` is a list of strings.

**3. HubSpot lists are not all contact lists.** `objectTypeId` `0-1` is contacts,
`0-2` companies, `0-3` deals. A company list resolves to zero contacts and used
to produce a silent empty route. `/api/lists` now filters to `0-1`.

**4. No `oracle_*` custom property exists in this portal.** All nine are absent.
Anything that reads or writes them returns 400. That is why completion state
lives in the session file, not the CRM. `donotcall` did not exist either; it was
created on 2026-08-10 and is now written on `do_not_call`.

**5. `hs_timestamp` comes back as ISO on some endpoints and epoch millis on
others.** A raw string compare across the two silently never matches. See
`HubSpotClient._call_date`.

**6. Call pacing counts Antonio's calendar days, not UTC.** `services/timezone.
work_day` and `tzdata` in requirements.txt both matter. Without `tzdata` the
zone silently falls back to UTC on Linux only, and the cooldown misfires near
local midnight.

**7. Do not run two agent sessions against this working tree.** It happened on
2026-08-10 and one session swept the other's uncommitted files into its commit.
Check `git log` and `ps aux | grep claude` before assuming a change is yours.

**8. The Octave output format changes shape.** The agent now emits `#### Opener`
sub-headings. The section splitter used to match a bare `###` anywhere, so it
shredded those and dropped the entire live call script from the note with no
error. If a note looks short, run the raw script through
`services.formatting._split_octave_sections` before suspecting Octave.

---

## Where things live

```
app.py                     18 routes + SSE generators
config.py                  every env var, read once
services/
  hubspot.py               HubSpot client. Call pacing helpers live here.
  octave.py                One agent: OCTAVE_CONTENT_AGENT.
  sessions.py              Session store AND the completion record.
                           record_disposition / clear_disposition are locked.
  formatting.py            Octave markdown -> escaped HubSpot note HTML
  timezone.py              Zone resolution + work_day for pacing
  call_sheet.py            Time-zone bucketing, seniority ordering
  routing_config.py        Disposition -> journey log text (log only)
  filters.py, retry.py, slack.py
templates/
  index.html               The whole UI. One file.
  login.html               Password gate
docs/octave-agent-instructions.md   Reviewable copy of the agent prompt
CODEX_REVIEW.md            Adversarial review brief + what the last pass found
BDR_DAY_ONE.md             Plain-language instructions for Theresa
```

## Design decisions worth not re-litigating

- **Completion lives in the session file**, not HubSpot, because the `oracle_*`
  properties do not exist. The file sits on the Railway volume, so it survives a
  restart.
- **The outcome is recorded before the HubSpot write.** A HubSpot outage must not
  make the BDR lose their place in the list.
- **A failed `do_not_call` is its own UI state.** It un-ticks the card and shows a
  distinct message. It deliberately does not share a path with a failed note,
  because a routine "saved anyway" notice is what people learn to click past.
- **Railway, not Vercel.** `sessions/` is local disk and `/generate` holds one SSE
  response open across many 30 to 60 second Octave calls.

## Open items

Both are planned hardening, not blockers. Triggers, not dates. Full reasoning is
in `CODEX_REVIEW.md`.

- **CSRF on write routes.** Do it before multi-user auth, wider URL
  distribution, or any embedded context. `SameSite=Lax` is the only defence now,
  and it holds only because there is one shared password and no cross-site form
  target.
- **Filter ordering.** The subscriber check runs before the cheap cooldown and
  account-cap checks and costs 2+ HubSpot requests per contact. Pure speed.

## Recently verified, do not re-test blind

- The agent rewrite fixes the empty-script case. A contact with no logged email
  went from a 147-character script to 5,055, and the note from 99 bytes to 2,097.
- Hostile contact names cannot inject: `Ann" onmouseover="..."` renders as text.
- 40 concurrent completions lose none. That test fails against the pre-lock code.
- Notes reach HubSpot with only structural tags: `p`, `strong`, `br`, `ul`, `li`.

## Working agreements Antonio has enforced

- Verify against the real system, not just local output. Several bugs were only
  visible in what HubSpot actually stored or what the deployed box actually ran.
- Regression tests must fail against the old code. A test that never saw the bug
  it describes is a restatement of the implementation.
- Say plainly what was not done and why. Do not narrow scope silently.
