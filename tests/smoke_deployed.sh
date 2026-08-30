#!/usr/bin/env bash
# Hit the DEPLOYED service and fail on any 5xx.
#
# WHY THIS EXISTS. Two dependencies -- httpx and python-multipart -- were present in every
# developer interpreter (FastAPI's test extras pull both in) and absent from the slim
# runtime image. 527 tests passed while the container crash-looped on import, and before
# that every HTML form POST on the live site had been returning 500 for weeks with a fully
# green suite. A test run cannot see a difference between two environments; only a request
# to the real one can.
#
# Usage: tests/smoke_deployed.sh [base_url]
set -uo pipefail
BASE="${1:-https://stratify.aeon-labs.site}"
fail=0

check() {  # path  expected-status  label  [extra curl args...]
  local path="$1" want="$2" label="$3"
  shift 3
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 15 "$@" "$BASE$path")
  if [ "$code" = "$want" ]; then
    printf '  ok    %-34s %s\n' "$label" "$code"
  else
    printf '  FAIL  %-34s got %s, want %s\n' "$label" "$code" "$want"
    fail=1
  fi
}

echo "smoke: $BASE"
check /            200 "landing"
check /docs        200 "docs"
check /privacy     200 "privacy"
check /terms       200 "terms"
check /healthz     200 "health"
check /app         302 "app redirects to sign-in"
check /auth/google 302 "google handshake starts"

# THE FORM PATHS, which are the ones that were silently broken. A 5xx here means a runtime
# dependency is missing from the image; 400/401/403 are healthy refusals.
echo "  -- form posts (refusals expected, 5xx is the bug) --"
check /auth/google/onetap 400 "one tap rejects a forged post" -X POST -d 'credential=a.b.c'
check /signup             400 "signup form parses" -X POST -d 'email=not-an-email'
check /dashboard/keys     401 "key form parses without a session" -X POST -d 'x=1'

# The MCP endpoint itself, unauthenticated: a JSON-RPC error, not an HTTP one.
check /mcp 200 "mcp answers" -X POST -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

[ "$fail" = 0 ] && echo "all good" || echo "SMOKE FAILED"
exit "$fail"
