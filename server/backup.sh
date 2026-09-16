#!/usr/bin/env bash
# Backup of the one piece of state this service cannot rebuild.
#
# WHAT IS AT STAKE. service.sqlite holds accounts, peppered API-key hashes, usage,
# results and the strategy book. There is DELIBERATELY no self-serve path from an email
# address back to a working key (see the 409 branch in /v1/signup), which is the right
# security posture and also means a lost database is a permanently locked-out user base.
#
# AND THE DATABASE ALONE IS NOT ENOUGH. Key verification peppers the hash with
# STRATIFY_KEY_PEPPER, which lives in exactly one file -- stratify_mcp/.env, mode 600,
# gitignored, on this box. Restore the database without that value and every key on it is
# unverifiable for ever. So the pepper is captured in the SAME archive as the database it
# unlocks; the two are useless apart and are therefore never separated.
#
# WHY `VACUUM INTO` AND NOT `cp`. The service writes to this file continuously. Copying a
# live SQLite database with cp can capture a torn page and yields a backup that only fails
# at restore time. VACUUM INTO takes a read lock and writes a consistent, already-compacted
# snapshot. Every snapshot is then integrity-checked before it is allowed to count as a
# backup -- an unverified copy is not a backup, it is a hope.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB="$ROOT/server/state/service.sqlite"
ENV_FILE="$ROOT/.env"
# Default: a `backups/` directory BESIDE the checkout, so an archive never sits inside the
# tree that gets committed. Override with STRATIFY_BACKUP_DIR.
DEST="${STRATIFY_BACKUP_DIR:-$ROOT/../backups/stratify_mcp}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

fail() { echo "$(date -uIs) FAIL $*" >&2; exit 1; }

[ -f "$DB" ] || fail "no database at $DB"
mkdir -p "$DEST" || fail "cannot create $DEST"
chmod 700 "$DEST"

# 1. Consistent snapshot. VACUUM INTO refuses to overwrite, so the target must not exist.
sqlite3 "$DB" "VACUUM INTO '$WORK/service.sqlite'" \
  || fail "VACUUM INTO failed -- database may be corrupt or locked"

# 2. Verify before trusting. A backup that has not been read back is not a backup.
CHECK="$(sqlite3 "$WORK/service.sqlite" 'PRAGMA integrity_check;' 2>&1)"
[ "$CHECK" = "ok" ] || fail "integrity_check on the snapshot returned: $CHECK"

# 3. Prove the tables a restore actually needs are present and populated. Corruption is
#    not the only way a backup goes wrong; so does backing up the wrong file.
ACCOUNTS="$(sqlite3 "$WORK/service.sqlite" 'SELECT count(*) FROM accounts;' 2>&1)"
KEYS="$(sqlite3 "$WORK/service.sqlite" 'SELECT count(*) FROM api_keys;' 2>&1)"
case "$ACCOUNTS$KEYS" in *[!0-9]*) fail "snapshot is missing accounts/api_keys: $ACCOUNTS/$KEYS";; esac
[ "$ACCOUNTS" -gt 0 ] || fail "snapshot has zero accounts -- refusing to call that a backup"

# 4. The pepper, without which the hashes above are noise.
if [ -f "$ENV_FILE" ]; then
  cp "$ENV_FILE" "$WORK/env"
else
  echo "$(date -uIs) WARN no .env at $ENV_FILE -- key pepper NOT captured" >&2
fi

cat > "$WORK/RESTORE.txt" <<TXT
Stratify MCP backup -- taken $STAMP (UTC)

  accounts: $ACCOUNTS
  api_keys: $KEYS

To restore:
  1. systemctl stop / docker compose down the stratify_mcp service.
  2. cp service.sqlite  <repo>/stratify_mcp/server/state/service.sqlite
     cp env             <repo>/stratify_mcp/.env          # chmod 600
  3. chown to the container user (uid 10001) if restoring into Docker:
     chown 10001:10001 <repo>/stratify_mcp/server/state/service.sqlite
  4. docker compose up -d, then confirm an existing key still authenticates.

The .env in this archive carries STRATIFY_KEY_PEPPER. Restoring the database WITHOUT it
leaves every issued API key permanently unverifiable. They travel together for that reason.
TXT

# 5. One archive, readable only by root.
OUT="$DEST/stratify-$STAMP.tar.gz"
tar -czf "$OUT" -C "$WORK" service.sqlite RESTORE.txt $([ -f "$WORK/env" ] && echo env) \
  || fail "archive failed"
chmod 600 "$OUT"

# 6. Read the archive back. Writing a file is not the same as having written it correctly.
tar -tzf "$OUT" >/dev/null || fail "archive is unreadable immediately after writing"

# 7. Retention: 14 daily, plus Sunday and 1st-of-month copies kept longer. Pruning by
#    count rather than age keeps a fixed floor of restore points even if the job misses days.
cd "$DEST" || fail "cannot enter $DEST"
ls -1t stratify-*.tar.gz 2>/dev/null | tail -n +15 | while read -r old; do
  d="${old#stratify-}"; d="${d%%T*}"
  case "$d" in
    *-01) continue;;                                   # monthly, keep
  esac
  [ "$(date -u -d "${d:0:4}-${d:4:2}-${d:6:2}" +%u 2>/dev/null)" = "7" ] && continue  # weekly
  rm -f "$old"
done
# Hard ceiling so a long-lived box cannot fill the disk with kept weeklies/monthlies.
ls -1t stratify-*.tar.gz 2>/dev/null | tail -n +61 | xargs -r rm -f

# 8. Optional off-box copy. THE BACKUP ABOVE SHARES A DISK WITH THE THING IT BACKS UP,
#    which protects against every failure except the one that loses the disk. Set
#    STRATIFY_BACKUP_REMOTE to an rsync target or an rclone remote to close that gap.
if [ -n "${STRATIFY_BACKUP_REMOTE:-}" ]; then
  case "$STRATIFY_BACKUP_REMOTE" in
    rclone:*) rclone copy "$OUT" "${STRATIFY_BACKUP_REMOTE#rclone:}" \
                || echo "$(date -uIs) WARN off-box rclone copy failed" >&2;;
    *)        rsync -a "$OUT" "$STRATIFY_BACKUP_REMOTE" \
                || echo "$(date -uIs) WARN off-box rsync failed" >&2;;
  esac
fi

echo "$(date -uIs) OK $OUT $(stat -c%s "$OUT") bytes accounts=$ACCOUNTS keys=$KEYS kept=$(ls -1 stratify-*.tar.gz | wc -l)"
