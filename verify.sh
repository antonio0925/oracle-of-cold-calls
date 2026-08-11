#!/usr/bin/env bash
# Canonical verification for this repo: test -> compile -> boot -> agent smoke.
# Usage: ./verify.sh        (exits non-zero on any failure)
set -euo pipefail
cd "$(dirname "$0")"

PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

echo "=== 1/4 tests ==="
$PY -m pytest

echo "=== 2/4 compile ==="
$PY -m compileall -q app.py agent services tests
echo "compile ok"

echo "=== 3/4 flask boot ==="
$PY - <<'EOF'
import app
rules = list(app.app.url_map.iter_rules())
client = app.app.test_client()
status = client.get("/api/dispositions").status_code
assert status == 200, "readiness probe returned %s" % status
print("boot ok: %d routes, /api/dispositions -> %d" % (len(rules), status))
EOF

echo "=== 4/4 agent loop smoke ==="
$PY - <<'EOF'
import os, sys, tempfile
os.environ["AGENT_STATE_DIR"] = tempfile.mkdtemp()
sys.path[:0] = [".", "tests"]
import agent.state as state
state.STATE_DIR = os.environ["AGENT_STATE_DIR"]
from test_agent import FakeHubSpot, FakeOctave
import agent.loop as L, agent.act as A

hs, posted = FakeHubSpot(), []
L.HubSpotClient = lambda *a, **k: hs
L.OctaveClient = lambda *a, **k: FakeOctave()
L.SupersendClient = lambda *a, **k: object()
A.slack.post_to_slack = lambda d: posted.append(d)

dry = L.run_once(campaign="verify", list_name="Dial", dry_run=True)
assert dry["plan"]["prep_contact"] == 1, dry["plan"]
assert not hs.notes and not posted, "dry run must not write"

live = L.run_once(campaign="verify", list_name="Dial")
assert all(r["ok"] for r in live["results"]), live["results"]
assert len(hs.notes) == 1 and len(posted) == 1

L.run_once(campaign="verify", list_name="Dial")
assert len(hs.notes) == 1, "rerun must be idempotent"
print("agent ok: dry-run clean, live wrote 1 note + 1 sheet, rerun idempotent")
EOF

echo
echo "VERIFICATION PASSED"
