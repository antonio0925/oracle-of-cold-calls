# Adversarial review brief: SUMMIT, branch `fix/review-top-three`

> **Status: this review was run and all nine confirmed findings are fixed in
> `85dd0ed`.** The brief is kept as written, because the suspect list below is
> what a second reviewer should attack next, and because it records what the
> first pass missed. See "Outcome" at the end before re-reviewing.

You are reviewing a working tree that one agent wrote in one sitting, deployed to
production, and verified with its own tests. Assume that agent was wrong.

**Your job is to find defects, not to agree.** A review that returns "looks good"
is a failed review unless you can show the work that justifies it. Every claim
below is the previous agent's claim. Treat each one as a hypothesis to falsify.

---

## What the app is

A single-operator cold-call tool for one BDR.

1. She picks a HubSpot contact list and a daily call target (default 50).
2. `/generate` walks the list, applies filters, and asks Octave for a voicemail
   script, a live call script, and objection handling per contact. It stops at
   the target.
3. She reviews, then writes prep notes to the HubSpot contact records.
4. On **Today's Climb** she works the list, logs each outcome, and the card ticks
   off. The outcome becomes a HubSpot note; `do_not_call` also sets the standard
   `donotcall` property.

It is live at a public URL behind one shared password. Every route writes to a
**production** HubSpot portal. There is no staging.

## Scope

```bash
git log --oneline e62a7b2..HEAD       # 22 commits
git diff e62a7b2..HEAD                # ~2,100 added, ~3,900 deleted, 32 files
```

Concentrate on `app.py`, `services/sessions.py`, `services/hubspot.py`, and
`templates/index.html`. Ignore prose in `README.md` and `BDR_DAY_ONE.md` except
where it contradicts the code.

## How to run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest
./verify.sh          # pytest -> compileall -> Flask boot probe
```

76 tests pass. **Tests passing is not evidence of correctness here.** The same
agent wrote the code and the tests, so both encode the same assumptions. Where a
test asserts a behaviour, ask whether the behaviour is right, not whether the
assertion holds.

---

## Known suspects

These are already believed to be defects. Confirm or refute each with a concrete
failure path. Do not stop here; this list is a starting point and an honesty
signal, not a boundary.

### 1. `record_disposition` is an unlocked read-modify-write

`services/sessions.py`. It calls `get_session` (which returns a deep copy),
mutates the copy, then `set_session` + `save_session_to_disk`. The lock is inside
each accessor, not around the pair.

Gunicorn runs `--threads 8`. Two outcomes logged close together both read a
dispositions map, each adds one key, and the second write wins. One logged call
disappears silently and the card un-ticks on the next refresh.

Confirm the window, then judge: is a lock enough, or does the file need to be the
serialization point?

### 2. The call cooldown compares UTC dates to a Pacific workday

`app.py`, `cooldown_cutoff`, and `HubSpotClient._call_date`. The cutoff is
`utcnow() - CALL_COOLDOWN_DAYS`, formatted `%Y-%m-%d`. Call timestamps are
normalised to a UTC date. `config.USER_TIMEZONE` defaults to `US/Pacific` and is
not consulted anywhere in this path.

A call logged at 6pm Pacific carries the **next** UTC date. Work out what that
does to a contact called yesterday evening, and to a run started after 5pm
Pacific. State whether the tool over-blocks, under-blocks, or both.

### 3. The per-account cap does not survive a resume

`app.py`, `per_account`, Filter E. The counter is rebuilt empty each run, and
`find_resumable_session` can resume a partial session for the same list and date.

Two contacts at Acme prepped in run one, the run dies, run two resumes and counts
from zero. Can the session end with more than `MAX_CONTACTS_PER_ACCOUNT_PER_DAY`
contacts at one account? Follow the cached-script path specifically: it
increments `per_account` too.

### 4. `/api/climb` with no `session_id` guesses

`find_latest_session` picks the most recently modified session file on disk. The
app has one shared password and therefore no user identity.

Two lists built the same morning, or a second person signed in: which list does
Today's Climb show, and can one person's completions land on another's list?

### 5. A failed contact chunk silently skips 100 people

`app.py`, `contacts_in_chunks`. A raised exception logs a warning, bumps
`stats["errors"]`, and continues to the next chunk. The BDR sees an error count
with no indication that a hundred contacts were never considered. Decide whether
this should be fatal, retried, or surfaced differently.

### 6. `not_reached` counts the wrong thing

`stats["not_reached"] = len(contact_ids) - i`. `i` counts contacts *yielded* by
the chunk generator; `contact_ids` counts ids *requested*. HubSpot omits records
it cannot return, so the two drift. Establish the size of the error and whether
it can go negative.

### 7. The DNC contract is ambiguous on partial failure

`app.py`, `api_climb_complete`. If the note fails, `hubspot_note_written` is
`False`. If the DNC flag fails, `hubspot_error` is appended but
`hubspot_note_written` may still be `True`. Read `submitDisposition` in the
template and decide whether the BDR can end up seeing a success state for a
**failed legal request**. This is the highest-consequence path in the app.

### 8. Long runs against a held-open SSE response

`_clamp_target` allows 500. Octave takes roughly 19 seconds per contact,
sequentially. That is a single SSE response held open for over two hours.
`--timeout 0` covers gunicorn. Consider proxies, browser idle behaviour, laptop
sleep, and what the session file looks like if the stream dies at contact 300.

---

## Areas with no known finding — attack them anyway

- **Auth.** `_require_login`, the `_PUBLIC_PATHS` / `_PUBLIC_PREFIXES` split, and
  `hmac.compare_digest`. Is there a path that reaches a writing route without a
  session? Note there is **no CSRF protection** on any POST; judge the real risk
  given a shared-password app on a public URL.
- **XSS.** `renderBattleCard`, `formatScript`, and `log()` in the template.
  Contact names, company names, and Octave output are all attacker-influenceable
  if a prospect controls their own CRM fields. `formatScript` escapes, then
  applies markdown regexes. Check the order.
- **Idempotency.** Double-clicking "Log the call", or replaying
  `/api/climb/complete`. How many HubSpot notes result?
- **`create_note_for_contact`.** Association type id 202 is hardcoded. Verify it
  is `note_to_contact` and that the create-with-association shape is correct for
  v3.
- **Filter ordering** in `/generate`. The subscriber check runs before the
  cooldown and account-cap checks and costs 2+ HubSpot requests per contact.
  Is the cheapest, most selective filter first?
- **Deletions.** Five service modules and the whole agent layer were removed.
  Confirm nothing imports them and no route silently lost behaviour that mattered.

---

## Rules

1. **Evidence over opinion.** For each finding give file, line, the input or
   sequence that triggers it, and the wrong result. "This could be racy" is not a
   finding; "these two requests interleave here and drop a disposition" is.
2. **Rank by consequence to the BDR and to the CRM.** A lost `do_not_call` flag
   outranks any amount of duplicated code. Data written wrongly to a production
   CRM outranks anything cosmetic.
3. **Say when a suspect above is wrong.** If the UTC-vs-Pacific concern cannot
   actually misfire, show why. Refuting a listed suspect is as valuable as
   confirming one.
4. **Separate real defects from taste.** Put naming and structure preferences in
   their own section, clearly marked, and keep them short.
5. **Do not trust the commit messages.** They were written by the agent that made
   the changes and describe intent, not necessarily behaviour.
6. **Do not write to HubSpot to test.** The portal is production. Read-only calls
   and mocks only.

## Output

- **Confirmed defects**, ranked, each with reproduction and suggested fix.
- **Refuted suspects**, with the reasoning.
- **New risks** the brief did not anticipate.
- **Taste**, brief and clearly separated.
- One paragraph: would you let a BDR use this against a production CRM tomorrow,
  and what single change would most reduce the risk?


---

## Outcome of the first review

Codex confirmed nine defects, refuted three suspects, and raised three new
risks. All nine were fixed in `85dd0ed`; the refutations were correct and no
change was made for them.

**Fixed**

| # | Defect | Fix |
|---|---|---|
| 1 | Failed Do Not Call could read as success | Response carries `compliance_failure`; that path un-ticks the card and shows a distinct message |
| 2 | `record_disposition` lost completions under concurrency | One locked helper for record and undo; 40-thread test fails against the old code |
| 3 | Contact name could break out of an `onclick` attribute | Data attributes and delegated listeners; nothing prospect-controlled reaches executable markup |
| 4 | Cooldown compared UTC days to a Pacific workday | `services/timezone.work_day`, used by both the cutoff and `_call_date` |
| 5 | `/api/climb` guessed the session | Explicit id, then browser-session binding; only an unnamed request falls back |
| 6 | Failed chunk skipped 100 contacts silently | Chunk failures and omitted records counted and reported |
| 7 | Completions were not idempotent | Repeat completion writes no second note |
| 8 | `not_reached` overcounted | Subtracts unscanned; cannot go negative |
| 9 | Stale config comment | Corrected |

**Correctly refuted, unchanged:** association type 202, `formatScript` escape
order, and the per-account cap across a completed scan.

**Verified in production, not just in tests:** the hostile-name payload
`Ann" onmouseover="window.__pwned=1" x="` renders as literal text and fires
nothing; a named-but-missing `session_id` returns empty instead of another
list; `work_day` and the UTC date genuinely disagreed on the day this was
written, so #4 was live.

**Raised and still open**

- **No CSRF tokens on write routes.** `SameSite=Lax` is the only defence.
  Judged acceptable for now: a shared-password app, no cross-site form targets,
  and no cookie-authenticated GET that mutates. It should not stay this way if
  the app ever gains a second user or a real login.
- **Filter ordering.** The subscriber check runs before the cooldown and
  account-cap checks and costs 2+ HubSpot requests per contact. Reordering is
  a straight win and has not been done.
- ~~**`format_note_html` does not escape** contact fields or Octave output into
  HubSpot note HTML.~~ **Fixed.** Every text insertion point escapes; only
  structural tags (`p`, `strong`, `br`, `ul`, `li`) can reach a note now.
  `normalize_html_for_compare` decodes entities so notes already in HubSpot
  still match their escaped replacements and the cleanup does not churn.

The two remaining items are the best starting points for a second pass, along
with anything the suspect list above did not cover.
