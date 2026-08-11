# The Agent Layer

The Oracle started as an app you drive. This layer makes it an agent that
drives itself: it wakes up, looks at HubSpot, decides what's worth doing,
does it, and writes down every decision.

## The loop

```
PERCEIVE   agent/perceive.py   Read the dial list + pending actions from HubSpot.
                               Resolve timezone, find the logged outbound email,
                               check whether a prep note already exists.
   |
DECIDE     agent/decide.py     Pure policy. Observations in, planned actions out.
                               No network calls, so the plan is fully testable.
   |
ACT        agent/act.py        The only module with side effects. Each executor
                               returns (ok, detail) and never raises into the loop.
   |
RECORD     agent/state.py      Append every event to .agent_state/ledger-DATE.jsonl
                               — the audit trail and the idempotency guard.
```

`agent/loop.py` runs one cycle. `agent/cli.py` is the entry point.

## Usage

```bash
# See exactly what it would do — no writes
python -m agent.cli run --campaign "Q3 Outbound" --list "Dial List" --dry-run

# Do it, capped at 25 prep notes
python -m agent.cli run --campaign "Q3 Outbound" --list "Dial List" --max-preps 25

# What happened today?
python -m agent.cli report

# Run continuously, one cycle an hour
python -m agent.cli serve --campaign "Q3 Outbound" --list "Dial List" --interval 3600
```

Daily at 6 AM via cron:

```
0 6 * * 1-5 cd /path/to/oracle-of-cold-calls && .venv/bin/python -m agent.cli \
  run --campaign "Q3 Outbound" --list "Dial List" --max-preps 50 >> agent.log 2>&1
```

## What it decides on its own

It **preps** a contact when the contact is dialable (has a phone), has a logged
outbound email to build the hook from, and has no prep note yet.

It **routes** a dispositioned contact only for `advance` and `retry` actions —
the reversible ones.

It **escalates** instead of acting for `transfer`, `finish`, and `remove`
(that includes `do_not_call` and `meeting_booked`). Those show up in the run
report for you to handle. `--allow-escalated` lifts the guard.

It **skips** with a written reason, never silently. Every skip is in the ledger.

## Safety properties

- **Idempotent.** The ledger records each successful action per contact per day.
  Rerun the same command and it does nothing. Verified by `test_rerun_is_idempotent`.
- **Bounded.** `--max-preps` caps Octave spend per run.
- **Honest dry run.** Because `decide.py` is pure, the dry-run plan is exactly
  the plan that executes.
- **Fault isolated.** One contact's Octave 500 fails that contact only; the run
  continues and the error lands in the report.
- **Nothing destructive without you.** Irreversible routes escalate by default.

## Tests

```bash
python -m pytest tests/test_agent.py -q
```

19 tests covering policy decisions, ledger idempotency, escalation, plan
assembly, and the full loop against fake HubSpot/Octave/Slack clients. No API
keys required.
