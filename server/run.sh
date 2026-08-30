#!/usr/bin/env bash
# One worker per core, minus two left for ClickHouse and the box's other work.
#
# A request costs about 0.026 CPU-seconds and is 92 % CPU-bound in Python, so a single
# process ceilings near 38 requests per second no matter how much concurrency it is
# offered — the GIL, not the database, is the limit. ClickHouse used 10.7 CPU-seconds
# across 844 queries during a saturating load test; it is nowhere near the constraint.
#
# Multiple workers are only safe because the concurrency counter lives in the service
# database rather than in process memory. If that ever moves back in-process, this must
# go back to 1.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${STRATIFY_KEY_PEPPER:?set STRATIFY_KEY_PEPPER before starting — the default is a development value}"
WORKERS="${WORKERS:-$(( $(nproc) > 3 ? $(nproc) - 2 : 1 ))}"
echo "starting ${WORKERS} workers on ${PORT:-8792}"
exec uvicorn server.app:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8792}" --workers "${WORKERS}"
