#!/usr/bin/env bash
# Decision G2 retention, actually executed.
#
# store.purge() existed, was correct, and had NO CALLER -- no cron, no scheduler, no
# startup hook, no endpoint. So spec bodies, full backtest payloads (every option print),
# caller-authored call arguments, usage rows and signup IP hashes were retained for ever,
# and every /r/<token> URL stayed live, while the service told users bodies are kept 30
# days. The promise was in the code and not in the world.
set -euo pipefail
cd "$(dirname "$0")/.."
exec /usr/bin/python3 -c "
import sys; sys.path.insert(0, '.')
from server import store
print(store.purge(), flush=True)
"
