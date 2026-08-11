#!/usr/bin/env bash
# Canonical verification for this repo: test -> compile -> boot.
# Usage: ./verify.sh        (exits non-zero on any failure)
set -euo pipefail
cd "$(dirname "$0")"

PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

echo "=== 1/3 tests ==="
$PY -m pytest

echo "=== 2/3 compile ==="
$PY -m compileall -q app.py config.py services tests
echo "compile ok"

echo "=== 3/3 flask boot ==="
$PY - <<'EOF'
import app
rules = list(app.app.url_map.iter_rules())
client = app.app.test_client()

# The app is behind a shared-password gate: an unauthenticated /api/ call
# must be refused with 401. Asserting 200 here would assert the gate is broken.
anon = client.get("/api/dispositions").status_code
assert anon == 401, "expected 401 for unauthenticated API call, got %s" % anon

# With a session, the same route must serve real data.
with client.session_transaction() as sess:
    sess["summit_auth"] = True
authed = client.get("/api/dispositions")
assert authed.status_code == 200, "authed probe returned %s" % authed.status_code
assert authed.get_json(), "authed probe returned empty payload"

print("boot ok: %d routes, auth gate 401, authed /api/dispositions -> 200" % len(rules))
EOF

echo
echo "VERIFICATION PASSED"
