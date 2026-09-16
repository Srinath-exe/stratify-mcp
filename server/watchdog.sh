#!/usr/bin/env bash
# Does anyone know when this breaks? Until now, no.
#
# The service had a liveness probe that Docker read and nothing else. Nobody was told when
# the box stopped serving, and with a public launch that means the first report of an
# outage arrives from a stranger, hours late, in public. This closes that.
#
# WHAT IT CHECKS is the whole path a real user takes -- public DNS, TLS, nginx, the app,
# and ClickHouse behind it -- not the app in isolation. A backend that is perfectly healthy
# behind a broken certificate is down as far as anyone using it is concerned.
#
# WHY IT RETRIES BEFORE ACTING. A single failed probe is usually a blip. Alerting on one
# trains you to ignore alerts, and restarting on one turns a hiccup into an outage. Three
# consecutive failures a few seconds apart is a real fault.
#
# WHY IT CAN HEAL BUT BARELY. A restart cures exactly one thing: a wedged process. So the
# restart is attempted only when liveness itself is failing, at most once per cooldown, and
# it is always reported. A watchdog that restarts freely hides the bug it should surface.
set -uo pipefail

WEB="${STRATIFY_WEB_URL:-https://stratify.aeon-labs.site}"
MCP="${STRATIFY_MCP_URL:-https://stratify-mcp.aeon-labs.site}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="${STRATIFY_WATCHDOG_STATE:-$ROOT/server/state}"
BACKUP_DIR="${STRATIFY_BACKUP_DIR:-$ROOT/../backups/stratify_mcp}"
LOG="$STATE_DIR/watchdog.log"
FAILFILE="$STATE_DIR/.watchdog-failing"
COOLDOWN=1800          # seconds between self-heal attempts
CONTAINER=stratify_mcp

log() { echo "$(date -uIs) $*" >> "$LOG"; }

alert() {
  local msg="$1"
  log "ALERT $msg"
  # Telegram if a token is configured. Reuses the bot paper trading already alerts through;
  # set TELEGRAM_BOT_TOKEN in /etc/stratify/alerts.env to turn this on. Without it the
  # watchdog still logs and still self-heals -- it just cannot reach you.
  [ -f /etc/stratify/alerts.env ] && . /etc/stratify/alerts.env
  if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
    curl -sS -m 15 -o /dev/null \
      "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${TELEGRAM_CHAT_ID:-1348080359}" \
      --data-urlencode "text=Stratify: $msg" \
      || log "WARN telegram send failed"
  fi
}

# Three tries, because one failure is noise. Returns the last body for the alert text.
probe() {
  local url="$1" want="$2" i code
  for i in 1 2 3; do
    BODY="$(curl -sS -m 20 -o /tmp/.wd.$$ -w '%{http_code}' "$url" 2>/tmp/.wderr.$$)"
    code="$BODY"
    if [ "$code" = "$want" ]; then rm -f /tmp/.wd.$$ /tmp/.wderr.$$; return 0; fi
    sleep 4
  done
  BODY="http=$code $(head -c 400 /tmp/.wd.$$ 2>/dev/null)$(head -c 200 /tmp/.wderr.$$ 2>/dev/null)"
  rm -f /tmp/.wd.$$ /tmp/.wderr.$$
  return 1
}

FAILURES=()

# 1. Liveness through the public edge. Covers DNS, TLS, nginx and the process.
probe "$WEB/healthz" 200 || FAILURES+=("web /healthz unreachable: $BODY")

# 2. Readiness -- ClickHouse is answering AND serving a full window, service DB writable.
#    This is the check that catches a silently empty table, which every other probe calls
#    healthy while every backtest returns zero trades.
probe "$WEB/readyz" 200 || FAILURES+=("readiness failing: $BODY")

# 3. The MCP host separately. It is a different nginx server block with its own
#    certificate, so it can fail entirely on its own -- and it is the hostname every
#    connected client actually talks to.
probe "$MCP/healthz" 200 || FAILURES+=("mcp host unreachable: $BODY")

# 4. A real unauthenticated data-path call. Proves the app can still reach ClickHouse and
#    render a response, not merely that it can return a constant.
probe "$WEB/v1/coverage" 200 || FAILURES+=("coverage endpoint failing: $BODY")

# 5. Disk. The service writes SQLite, reports and logs; a full disk corrupts all three,
#    and it is the failure that gives the most warning if anyone is looking.
USE=$(df --output=pcent / | tail -1 | tr -dc '0-9')
[ "${USE:-0}" -ge 90 ] && FAILURES+=("disk at ${USE}% on /")

# 6. The backup is only a backup if it is recent. A silently dead backup job is discovered
#    at restore time, which is the worst possible moment.
NEWEST=$(ls -1t "$BACKUP_DIR"/stratify-*.tar.gz 2>/dev/null | head -1)
if [ -z "$NEWEST" ]; then
  FAILURES+=("no database backup exists")
elif [ "$(( ($(date +%s) - $(stat -c %Y "$NEWEST")) / 3600 ))" -gt 36 ]; then
  FAILURES+=("newest backup is $(( ($(date +%s) - $(stat -c %Y "$NEWEST")) / 3600 ))h old")
fi

if [ ${#FAILURES[@]} -eq 0 ]; then
  if [ -f "$FAILFILE" ]; then
    alert "RECOVERED — all checks passing again"
    rm -f "$FAILFILE"
  fi
  log "OK all checks passed"
  exit 0
fi

SUMMARY=$(printf '%s; ' "${FAILURES[@]}")

# Alert once per incident, not once per run. A five-minute cron that alerts every run
# during a two-hour outage sends 24 messages and gets muted.
if [ ! -f "$FAILFILE" ]; then
  alert "DOWN — $SUMMARY"
  date +%s > "$FAILFILE"
else
  log "STILL FAILING $SUMMARY"
fi

# Self-heal, narrowly. Only when liveness itself is down -- a restart cannot fix ClickHouse
# or a full disk, and trying would just add an outage to an outage.
if printf '%s' "$SUMMARY" | grep -q "healthz unreachable"; then
  LAST=$(cat "$STATE_DIR/.watchdog-last-restart" 2>/dev/null || echo 0)
  if [ "$(( $(date +%s) - LAST ))" -gt "$COOLDOWN" ]; then
    log "attempting restart of $CONTAINER"
    date +%s > "$STATE_DIR/.watchdog-last-restart"
    if docker restart "$CONTAINER" >/dev/null 2>&1; then
      sleep 20
      if probe "$WEB/healthz" 200; then alert "restarted $CONTAINER — service is back up"
      else alert "restarted $CONTAINER and it is STILL down — needs a human"; fi
    else
      alert "could not restart $CONTAINER — needs a human"
    fi
  else
    log "restart suppressed, within ${COOLDOWN}s cooldown"
  fi
fi
exit 1
