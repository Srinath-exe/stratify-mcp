"""Service state: accounts, API keys, quota counters, saved results.

SQLite on purpose. The serving data lives in ClickHouse; this holds only small,
write-heavy service state, and one file that can be backed up with `cp` is worth more at
this stage than another moving part.

KEYS ARE NEVER STORED. Only a peppered scrypt hash of the key is kept, and the pepper
lives outside the database in the environment, so a stolen database file does not yield
working keys. The plaintext is shown exactly once, at creation.
"""
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(os.getenv("STRATIFY_SERVICE_DB",
                         Path(__file__).resolve().parent / "state" / "service.sqlite"))
# A missing pepper is a configuration error in production, but a fixed development value
# keeps tests hermetic. The production deployment must set this.
PEPPER = os.getenv("STRATIFY_KEY_PEPPER", "dev-pepper-not-for-production").encode()

KEY_PREFIX = "sk_live_"
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  account_id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL,
  created_at REAL NOT NULL, tier TEXT NOT NULL DEFAULT 'free',
  session_token TEXT);
-- NOTE: no index on session_token here. Anything that depends on a MIGRATED column
-- belongs in POST_MIGRATION, because executescript runs before migrations and fails the
-- whole script -- on connect, which takes the service down rather than one request. This
-- exact mistake was made twice; the rule is written down so it is not made a third time.
CREATE TABLE IF NOT EXISTS api_keys (
  key_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, key_hash BLOB NOT NULL,
  last4 TEXT NOT NULL, created_at REAL NOT NULL, revoked_at REAL);
CREATE INDEX IF NOT EXISTS ix_keys_account ON api_keys(account_id);
CREATE TABLE IF NOT EXISTS usage (
  key_id TEXT NOT NULL, account_id TEXT NOT NULL, ts REAL NOT NULL,
  cpu_seconds REAL NOT NULL DEFAULT 0,
  price_points INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS signups (
  ts REAL NOT NULL, ip_hash TEXT NOT NULL, account_id TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_signups ON signups(ip_hash, ts);
CREATE TABLE IF NOT EXISTS inflight (
  token TEXT PRIMARY KEY, account_id TEXT NOT NULL, tier TEXT NOT NULL DEFAULT 'free',
  started REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_inflight ON inflight(account_id);
CREATE TABLE IF NOT EXISTS results (
  backtest_id TEXT PRIMARY KEY, key_id TEXT NOT NULL, created_at REAL NOT NULL,
  spec_json TEXT NOT NULL, spec_hash TEXT NOT NULL, payload_json TEXT NOT NULL,
  report_token TEXT UNIQUE NOT NULL);
CREATE INDEX IF NOT EXISTS ix_results_key ON results(key_id, created_at);

-- Every JSON-RPC call, with what the model sent us and what came back.
--
-- WHAT THIS CAN AND CANNOT HOLD. It is not a log of user prompts, because a prompt never
-- reaches this service: MCP delivers a tool name and its arguments, and whatever the
-- person typed stays between them and their model. What is recorded is the request the
-- model constructed, which is the closest thing that exists here -- and is often more
-- useful, because it is the machine-readable version of the intent.
--
-- The RESPONSE BODY is deliberately not stored. A full backtest payload is ~300 KB and is
-- already persisted once in `results`; keeping a second copy per call would grow this
-- database by gigabytes to hold duplicates. The size and the backtest_id are kept instead,
-- so any logged call that produced a result can be resolved to it.
CREATE TABLE IF NOT EXISTS calls (
  call_id TEXT PRIMARY KEY, ts REAL NOT NULL,
  account_id TEXT NOT NULL, key_id TEXT NOT NULL, tier TEXT NOT NULL,
  method TEXT NOT NULL, tool TEXT, arguments_json TEXT,
  outcome TEXT NOT NULL, refusal TEXT, backtest_id TEXT,
  cpu_seconds REAL NOT NULL DEFAULT 0,
  price_points INTEGER NOT NULL DEFAULT 0,
  response_bytes INTEGER NOT NULL DEFAULT 0,
  client TEXT);
CREATE INDEX IF NOT EXISTS ix_calls_account ON calls(account_id, ts);

-- Strategies that cleared the evidence bar, per account. See server/book.py for what the
-- bar is and why it is not "made money".
--
-- ONE ROW PER (account, spec_hash), enforced by the unique index below. A parameter sweep
-- is dozens of near-identical specs; without dedup the book fills with the same idea at
-- forty offsets and stops being a shortlist. Re-running a spec UPDATES its row.
CREATE TABLE IF NOT EXISTS strategy_book (
  entry_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, backtest_id TEXT NOT NULL,
  spec_hash TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
  times_seen INTEGER NOT NULL DEFAULT 1,
  structure TEXT NOT NULL, cadence TEXT NOT NULL DEFAULT 'weekly', spec_json TEXT NOT NULL,
  n_trades INTEGER, pnl_rupees REAL, mean_rom REAL, sharpe REAL, profit_factor REAL,
  max_drawdown_rupees REAL, peak_margin_points REAL,
  health_score INTEGER, verdict TEXT, deflated_sharpe REAL,
  oos_held_up INTEGER, folds_profitable INTEGER, folds_total INTEGER,
  worst_fold_rupees REAL, median_fold_rupees REAL,
  qualified_json TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS ix_book_dedup ON strategy_book(account_id, spec_hash);
CREATE INDEX IF NOT EXISTS ix_book_account ON strategy_book(account_id, worst_fold_rupees);

-- User-reported feedback: bugs, feature requests, data gaps, confusion, praise.
--
-- ONE TABLE, NOT FOUR. A bug and a feature request differ by a tag, not by a schema, and
-- the moment they live in separate tables the triage queue has to be assembled by union
-- and every new category needs a migration. `category` carries the distinction and
-- `tags_json` carries everything that does not fit one word.
--
-- WHY CONTEXT IS CAPTURED AT FILE TIME. A report that says "the backtest looked wrong" is
-- unactionable a week later. `backtest_id` resolves to the exact result, and
-- `context_json` holds the reporter's last few calls, so a triage agent can reproduce
-- without asking the reporter anything. This is what makes the loop close by itself.
--
-- DEDUP IS PER (account, dedup_key), enforced below. The same person filing the same
-- request twice bumps `times_seen`; different people filing it stay separate rows sharing
-- a dedup_key, which is how the console counts "9 accounts asked for this" -- the demand
-- signal that decides what gets built.
CREATE TABLE IF NOT EXISTS feedback (
  feedback_id TEXT PRIMARY KEY, ts REAL NOT NULL,
  account_id TEXT NOT NULL, tier TEXT NOT NULL DEFAULT 'free',
  source TEXT NOT NULL DEFAULT 'mcp',
  category TEXT NOT NULL, tags_json TEXT NOT NULL DEFAULT '[]',
  severity TEXT NOT NULL DEFAULT 'minor',
  title TEXT NOT NULL, body TEXT NOT NULL,
  backtest_id TEXT, context_json TEXT,
  dedup_key TEXT NOT NULL, times_seen INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'new', resolution TEXT,
  updated_at REAL NOT NULL, resolved_at REAL,
  triage_note TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ix_feedback_dedup ON feedback(account_id, dedup_key);
CREATE INDEX IF NOT EXISTS ix_feedback_queue ON feedback(status, severity, ts);
CREATE INDEX IF NOT EXISTS ix_feedback_theme ON feedback(dedup_key);
CREATE INDEX IF NOT EXISTS ix_feedback_account ON feedback(account_id, ts);
"""


# CREATE TABLE IF NOT EXISTS silently does nothing when the table already exists, so a
# new column in SCHEMA never reaches a database that predates it -- the service starts
# fine and then fails on first use with "no such column". Additive migrations are applied
# explicitly; they are idempotent and cheap enough to run on every connection.
MIGRATIONS = [
    ("accounts", "session_token", "ALTER TABLE accounts ADD COLUMN session_token TEXT"),
    ("inflight", "tier", "ALTER TABLE inflight ADD COLUMN tier TEXT NOT NULL DEFAULT 'free'"),
    ("usage", "account_id", "ALTER TABLE usage ADD COLUMN account_id TEXT NOT NULL DEFAULT ''"),
    # Real option prints returned to the caller. Rich results made the exposure real
    # rather than theoretical, so it gets a meter of its own next to CPU-seconds.
    ("usage", "price_points",
     "ALTER TABLE usage ADD COLUMN price_points INTEGER NOT NULL DEFAULT 0"),
    # Admin is a property of the ACCOUNT, not of a config file listing emails. An email
    # list would make "who can see every customer's data" a thing that changes silently on
    # deploy; a column makes granting it an explicit, auditable write.
    ("accounts", "is_admin", "ALTER TABLE accounts ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0"),
    # Google sign-in. `google_sub` is Google's stable subject id and is the thing we key
    # on, NOT the email: an address can be reassigned inside a Workspace domain and a
    # person can change theirs, and either would silently move an account to a new owner
    # or lock its owner out. The email is stored for display and for the accounts that
    # predate this.
    ("accounts", "google_sub", "ALTER TABLE accounts ADD COLUMN google_sub TEXT"),
    ("accounts", "display_name", "ALTER TABLE accounts ADD COLUMN display_name TEXT"),
    ("accounts", "avatar_url", "ALTER TABLE accounts ADD COLUMN avatar_url TEXT"),
    ("accounts", "last_seen_at", "ALTER TABLE accounts ADD COLUMN last_seen_at REAL"),
]

# Indexes that depend on a migrated column. They cannot live in SCHEMA: executescript runs
# top to bottom, so an index on a column that only exists after migration fails the whole
# script -- and it fails on CONNECT, which takes the service down rather than one request.
POST_MIGRATION = [
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_accounts_google ON accounts(google_sub) "
    "WHERE google_sub IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_usage ON usage(account_id, ts)",
    "CREATE INDEX IF NOT EXISTS ix_inflight_tier ON inflight(tier)",
    "CREATE INDEX IF NOT EXISTS ix_accounts_session ON accounts(session_token)",
]


def _migrate(con):
    for table, column, ddl in MIGRATIONS:
        table_exists = con.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?",
            (table,)).fetchone()[0]
        if not table_exists:
            continue
        has_column = con.execute(
            "SELECT count(*) FROM pragma_table_info(?) WHERE name = ?",
            (table, column)).fetchone()[0]
        if not has_column:
            con.execute(ddl)
    for ddl in POST_MIGRATION:
        con.execute(ddl)


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    _migrate(con)
    return con


def _hash(secret):
    return hashlib.scrypt(secret.encode(), salt=PEPPER,
                          n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)


# ---------------------------------------------------------------- accounts and keys

def create_account(email, tier="free"):
    """Self-serve signup. No approval step, no waitlist, no manual review (decision §0).

    RETURNS (account_id, created). The `created` flag is the whole point: this used to
    return the existing account_id for a known email, and both signup handlers then issued
    a key -- and a session cookie -- against it. Anyone who knew a customer's email address
    could type it into the signup form and land inside that account. Worse at launch:
    `authenticate` reads the tier off the ACCOUNT, so a key minted that way inherits the
    victim's paid history. Callers must now decide what to do when created is False; none
    of them may authenticate.
    """
    con = connect()
    try:
        # The lookup and the insert stay inside ONE transaction so two concurrent signups
        # for the same address cannot both see "absent" and both create an account.
        with con:
            row = con.execute("SELECT account_id FROM accounts WHERE email=?",
                              (email,)).fetchone()
            if row:
                return row["account_id"], False
            account_id = "acc_" + secrets.token_hex(8)
            con.execute("INSERT INTO accounts (account_id, email, created_at, tier, "
                        "session_token) VALUES (?,?,?,?,?)",
                        (account_id, email, time.time(), tier, secrets.token_urlsafe(32)))
            return account_id, True
    finally:
        # Closed in `finally`, never inside the `with`: sqlite3's connection context
        # manager COMMITS on exit, so closing first makes that commit raise
        # "Cannot operate on a closed database" on the way out.
        con.close()


def account_for_google(sub, email, name=None, picture=None, tier="free"):
    """Sign in (or sign up) a Google identity. RETURNS (account_row, created).

    WHY THIS MAY DO WHAT create_account MUST NOT. create_account refuses to return an
    existing account for a known email, because the signup form takes an address that
    anybody can type -- typing a customer's address there used to open their dashboard.
    Here the address is not typed, it is ASSERTED BY GOOGLE over a code exchange this
    server made itself, and only after email_verified came back true. Matching an existing
    account is therefore the correct behaviour rather than a hole: it is the difference
    between a claim and a proof.

    Matching happens on the SUBJECT first and the email only as a one-time link for
    accounts that predate Google sign-in. Once linked, the subject is what identifies
    them forever after.
    """
    con = connect()
    try:
        with con:
            row = con.execute("SELECT * FROM accounts WHERE google_sub=?", (sub,)).fetchone()
            created = False
            if row is None:
                # A pre-existing email-signup account, claimed by its verified owner. Only
                # ever runs once per account: after this the subject match above wins.
                row = con.execute("SELECT * FROM accounts WHERE email=? AND google_sub IS NULL",
                                  (email,)).fetchone()
            if row is None:
                account_id = "acc_" + secrets.token_hex(8)
                con.execute(
                    "INSERT INTO accounts (account_id, email, created_at, tier, "
                    "session_token, google_sub, display_name, avatar_url, last_seen_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (account_id, email, time.time(), tier, secrets.token_urlsafe(32),
                     sub, name, picture, time.time()))
                created = True
            else:
                account_id = row["account_id"]
                con.execute(
                    "UPDATE accounts SET google_sub=?, display_name=?, avatar_url=?, "
                    "email=?, last_seen_at=? WHERE account_id=?",
                    (sub, name, picture, email, time.time(), account_id))
            if not con.execute("SELECT session_token FROM accounts WHERE account_id=?",
                               (account_id,)).fetchone()["session_token"]:
                con.execute("UPDATE accounts SET session_token=? WHERE account_id=?",
                            (secrets.token_urlsafe(32), account_id))
        fresh = con.execute("SELECT * FROM accounts WHERE account_id=?",
                            (account_id,)).fetchone()
        return fresh, created
    finally:
        con.close()


def rotate_session(account_id):
    """Sign out everywhere. The old cookie stops working the instant this returns, which
    is what 'log out' has to mean on a shared or stolen machine -- clearing the cookie in
    the browser alone leaves the secret valid for anyone who copied it."""
    con = connect()
    token = secrets.token_urlsafe(32)
    with con:
        con.execute("UPDATE accounts SET session_token=? WHERE account_id=?",
                    (token, account_id))
    con.close()
    return token


def session_token(account_id):
    """The dashboard session secret. Deliberately NOT the API key: a browser cookie is a
    different threat model from a bearer token an agent holds, and one being stolen must
    not hand over the other."""
    con = connect()
    row = con.execute("SELECT session_token FROM accounts WHERE account_id=?",
                      (account_id,)).fetchone()
    if row and not row["session_token"]:
        token = secrets.token_urlsafe(32)
        with con:
            con.execute("UPDATE accounts SET session_token=? WHERE account_id=?",
                        (token, account_id))
        con.close()
        return token
    con.close()
    return row["session_token"] if row else None


def account_by_session(token):
    if not token:
        return None
    con = connect()
    row = con.execute("SELECT * FROM accounts WHERE session_token=?", (token,)).fetchone()
    con.close()
    return row


def list_keys(account_id):
    con = connect()
    rows = con.execute(
        "SELECT key_id, last4, created_at, revoked_at FROM api_keys "
        "WHERE account_id=? ORDER BY created_at DESC", (account_id,)).fetchall()
    con.close()
    return rows


def key_owner(key_id):
    con = connect()
    row = con.execute("SELECT account_id FROM api_keys WHERE key_id=?", (key_id,)).fetchone()
    con.close()
    return row["account_id"] if row else None


def issue_key(account_id):
    """-> (key_id, plaintext). The plaintext is returned once and never recoverable."""
    secret = KEY_PREFIX + secrets.token_urlsafe(32)
    key_id = "key_" + secrets.token_hex(8)
    con = connect()
    with con:
        con.execute("INSERT INTO api_keys (key_id, account_id, key_hash, last4, "
                    "created_at, revoked_at) VALUES (?,?,?,?,?,NULL)",
                    (key_id, account_id, _hash(secret), secret[-4:], time.time()))
    con.close()
    return key_id, secret


def revoke_key(key_id):
    con = connect()
    with con:
        con.execute("UPDATE api_keys SET revoked_at=? WHERE key_id=?", (time.time(), key_id))
    con.close()


def authenticate(presented):
    """-> row or None. Constant-time compare, and a revoked key is not a valid key.

    Every stored hash is checked rather than looked up by a derived index, so the work is
    the same whether the key exists or not.
    """
    if not presented or not presented.startswith(KEY_PREFIX):
        return None
    digest = _hash(presented)
    con = connect()
    rows = con.execute(
        "SELECT k.*, a.tier FROM api_keys k JOIN accounts a USING(account_id) "
        "WHERE k.revoked_at IS NULL").fetchall()
    con.close()
    found = None
    for row in rows:
        if hmac.compare_digest(row["key_hash"], digest):
            found = row
    return found


# ---------------------------------------------------------------- call log

def record_call(row, now=None):
    """One row per JSON-RPC call. Never raises into the request path -- an audit log that
    can take the service down is worse than no audit log. A dropped row is a gap in
    history; a raised exception is an outage."""
    try:
        con = connect()
        with con:
            con.execute(
                "INSERT INTO calls (call_id, ts, account_id, key_id, tier, method, tool, "
                "arguments_json, outcome, refusal, backtest_id, cpu_seconds, price_points, "
                "response_bytes, client) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("call_" + secrets.token_hex(8), now if now is not None else time.time(),
                 row.get("account_id", ""), row.get("key_id", ""), row.get("tier", "free"),
                 row.get("method", ""), row.get("tool"), row.get("arguments_json"),
                 row.get("outcome", "ok"), row.get("refusal"), row.get("backtest_id"),
                 float(row.get("cpu_seconds") or 0), int(row.get("price_points") or 0),
                 int(row.get("response_bytes") or 0), row.get("client")))
        con.close()
    except Exception:                       # noqa: BLE001 -- see docstring
        pass


def recent_calls(account_id, limit=50):
    con = connect()
    rows = con.execute(
        "SELECT * FROM calls WHERE account_id=? ORDER BY ts DESC LIMIT ?",
        (account_id, limit)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def call_stats(account_id, since):
    con = connect()
    rows = con.execute(
        "SELECT coalesce(tool, method) AS name, outcome, count(*) n, "
        "       coalesce(sum(cpu_seconds),0) cpu "
        "FROM calls WHERE account_id=? AND ts>=? GROUP BY name, outcome ORDER BY n DESC",
        (account_id, since)).fetchall()
    con.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- strategy book

BOOK_COLUMNS = (
    "account_id", "backtest_id", "spec_hash", "structure", "cadence", "spec_json",
    "n_trades", "pnl_rupees", "mean_rom", "sharpe", "profit_factor",
    "max_drawdown_rupees", "peak_margin_points", "health_score", "verdict",
    "deflated_sharpe", "oos_held_up", "folds_profitable", "folds_total",
    "worst_fold_rupees", "median_fold_rupees", "qualified_json")


def record_strategy(entry, now=None):
    """Insert, or refresh the existing row for this (account, spec_hash).

    UPSERT rather than insert: the same spec run twice is one strategy observed twice, not
    two strategies. times_seen counts the observations, created_at keeps the first sighting
    and updated_at moves -- so the book can distinguish an idea someone keeps returning to
    from one they ran once.
    """
    now = now if now is not None else time.time()
    cols = ", ".join(BOOK_COLUMNS)
    placeholders = ", ".join("?" for _ in BOOK_COLUMNS)
    updates = ", ".join(f"{c}=excluded.{c}" for c in BOOK_COLUMNS
                        if c not in ("account_id", "spec_hash"))
    con = connect()
    with con:
        con.execute(
            f"INSERT INTO strategy_book (entry_id, created_at, updated_at, {cols}) "
            f"VALUES (?,?,?,{placeholders}) "
            f"ON CONFLICT(account_id, spec_hash) DO UPDATE SET "
            f"  updated_at=excluded.updated_at, times_seen=times_seen+1, {updates}",
            ("sb_" + secrets.token_hex(8), now, now,
             *[entry.get(c) for c in BOOK_COLUMNS]))
        row = con.execute(
            "SELECT entry_id, times_seen FROM strategy_book WHERE account_id=? AND spec_hash=?",
            (entry["account_id"], entry["spec_hash"])).fetchone()
    con.close()
    return dict(row)


def list_strategies(account_id, limit=25, order="consistency"):
    """Default order is CONSISTENCY, not P&L.

    Worst fold first, then median fold. A strategy that made money in every walk-forward
    fold is a better candidate than one that made more money in one fold and lost in the
    other two, and sorting by total P&L puts the second one on top every time.
    """
    if order == "pnl":
        clause = "pnl_rupees DESC"
    elif order == "health":
        clause = "health_score DESC, worst_fold_rupees DESC"
    else:
        clause = "worst_fold_rupees DESC, median_fold_rupees DESC"
    con = connect()
    rows = con.execute(
        f"SELECT * FROM strategy_book WHERE account_id=? ORDER BY {clause} LIMIT ?",
        (account_id, limit)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def forget_strategy(account_id, entry_id):
    con = connect()
    with con:
        n = con.execute("DELETE FROM strategy_book WHERE account_id=? AND entry_id=?",
                        (account_id, entry_id)).rowcount
    con.close()
    return n


# ---------------------------------------------------------------- usage

def record_usage(key_id, account_id, cpu_seconds, now=None, price_points=0):
    # Columns are NAMED, never positional. A positional INSERT here silently misattributed
    # every one of 116 usage rows once, because ALTER TABLE appends a column to the END of
    # the row and the tuple did not move with it -- so the quota was enforced against a
    # column that held an account id.
    con = connect()
    with con:
        con.execute("INSERT INTO usage (key_id, account_id, ts, cpu_seconds, price_points) "
                    "VALUES (?,?,?,?,?)",
                    (key_id, account_id, now if now is not None else time.time(),
                     cpu_seconds, int(price_points or 0)))
    con.close()


def usage_since(account_id, since):
    """Metered on the ACCOUNT, not the key. Metering per key would let anyone multiply
    their quota by issuing more keys, which is not a limit at all."""
    con = connect()
    row = con.execute(
        "SELECT count(*) n, coalesce(sum(cpu_seconds),0) cpu, "
        "coalesce(sum(price_points),0) pts FROM usage "
        "WHERE account_id=? AND ts>=?", (account_id, since)).fetchone()
    con.close()
    return row["n"], row["cpu"], row["pts"]


def oldest_usage(account_id, since):
    con = connect()
    row = con.execute("SELECT min(ts) t FROM usage WHERE account_id=? AND ts>=?",
                      (account_id, since)).fetchone()
    con.close()
    return row["t"] if row else None


def active_key_count(account_id):
    con = connect()
    row = con.execute(
        "SELECT count(*) n FROM api_keys WHERE account_id=? AND revoked_at IS NULL",
        (account_id,)).fetchone()
    con.close()
    return row["n"]


# ---------------------------------------------------------------- signup throttling

def record_signup(ip_hash, account_id, now=None):
    con = connect()
    with con:
        con.execute("INSERT INTO signups (ts, ip_hash, account_id) VALUES (?,?,?)",
                    (now if now is not None else time.time(), ip_hash, account_id))
    con.close()


def signups_since(ip_hash, since):
    con = connect()
    row = con.execute("SELECT count(*) n FROM signups WHERE ip_hash=? AND ts>=?",
                      (ip_hash, since)).fetchone()
    con.close()
    return row["n"]


def hash_ip(ip):
    """Stored as a peppered hash, never in the clear: the throttle needs to recognise a
    repeat visitor, not to keep a log of who visited."""
    return hashlib.blake2b(ip.encode(), key=PEPPER[:64], digest_size=16).hexdigest()


# ---------------------------------------------------------------- retention

def purge(now=None, usage_days=30, spec_body_days=30, signup_days=7,
          call_days=30):
    """Retention, per decision G2: spec HASHES are kept indefinitely for metering and
    dedup, spec BODIES for 30 days for debugging, then blanked. Usage rows and signup
    records age out too -- an abuse counter does not need a permanent history.

    Rows are not deleted, so a backtest id keeps resolving; its spec and payload are
    replaced by a tombstone explaining that they were purged on schedule.
    """
    now = now if now is not None else time.time()
    tomb = '{"purged": true, "reason": "spec and result bodies are retained 30 days"}'
    con = connect()
    with con:
        n_usage = con.execute("DELETE FROM usage WHERE ts < ?",
                              (now - usage_days * 86400,)).rowcount
        n_signup = con.execute("DELETE FROM signups WHERE ts < ?",
                               (now - signup_days * 86400,)).rowcount
        n_spec = con.execute(
            "UPDATE results SET spec_json=?, payload_json=? "
            "WHERE created_at < ? AND spec_json != ?",
            (tomb, tomb, now - spec_body_days * 86400, tomb)).rowcount
        # The call log holds caller-authored content, so it ages out on the same clock as
        # the spec bodies rather than accumulating forever. strategy_book does NOT age out:
        # it is the durable artefact, it is small, and a shortlist that silently forgets
        # entries is worse than no shortlist.
        n_calls = con.execute("DELETE FROM calls WHERE ts < ?",
                              (now - call_days * 86400,)).rowcount
    con.close()
    return {"usage_rows_deleted": n_usage, "signup_rows_deleted": n_signup,
            "result_bodies_purged": n_spec, "call_rows_deleted": n_calls}


# ---------------------------------------------------------------- concurrency

# Cross-process, because the service runs one worker per core and an in-process counter
# would let each worker hand out the full limit independently -- N workers, N times the
# concurrency. SQLite is enough for a single box; a multi-box deployment moves this to
# Redis and nothing else changes.
INFLIGHT_TIMEOUT = 300.0   # a request still marked running after this crashed


def acquire_slot(account_id, limit, token, tier="free", tier_limit=None, now=None):
    """-> None if taken, else the reason it was refused ('account' or 'tier').

    Two ceilings, because they stop different things. The per-account limit stops one
    customer monopolising the service. The per-TIER limit stops an expensive workload
    monopolising the workers: a seven-year backtest holds a worker process for about a
    second, so a handful running at once starves the one-year traffic that makes up
    almost all requests. Measured before this existed: free p50 rose 1.56x and throughput
    fell to 72 % whenever four paid backtests were in flight.

    Stale entries are swept in the same transaction, so a worker that died holding a slot
    cannot lock anyone out permanently.
    """
    now = now if now is not None else time.time()
    con = connect()
    try:
        with con:
            con.execute("DELETE FROM inflight WHERE started < ?", (now - INFLIGHT_TIMEOUT,))
            n = con.execute("SELECT count(*) FROM inflight WHERE account_id=?",
                            (account_id,)).fetchone()[0]
            if n >= limit:
                return "account"
            if tier_limit is not None:
                t = con.execute("SELECT count(*) FROM inflight WHERE tier=?",
                                (tier,)).fetchone()[0]
                if t >= tier_limit:
                    return "tier"
            con.execute("INSERT OR REPLACE INTO inflight VALUES (?,?,?,?)",
                        (token, account_id, tier, now))
            return None
    finally:
        con.close()


def release_slot(token):
    con = connect()
    try:
        with con:
            con.execute("DELETE FROM inflight WHERE token=?", (token,))
    finally:
        con.close()


def active_slots(account_id):
    con = connect()
    try:
        return con.execute("SELECT count(*) FROM inflight WHERE account_id=?",
                           (account_id,)).fetchone()[0]
    finally:
        con.close()


# ---------------------------------------------------------------- results

def save_result(key_id, spec_json, spec_hash, payload_json, now=None):
    backtest_id = "bt_" + secrets.token_hex(10)
    token = secrets.token_urlsafe(24)
    con = connect()
    with con:
        con.execute("INSERT INTO results (backtest_id, key_id, created_at, spec_json, "
                    "spec_hash, payload_json, report_token) VALUES (?,?,?,?,?,?,?)",
                    (backtest_id, key_id, now if now is not None else time.time(),
                     spec_json, spec_hash, payload_json, token))
    con.close()
    return backtest_id, token


def get_result(backtest_id, key_id=None):
    con = connect()
    if key_id is None:
        row = con.execute("SELECT * FROM results WHERE backtest_id=?", (backtest_id,)).fetchone()
    else:
        row = con.execute("SELECT * FROM results WHERE backtest_id=? AND key_id=?",
                          (backtest_id, key_id)).fetchone()
    con.close()
    return row


def get_result_by_token(token):
    con = connect()
    row = con.execute("SELECT * FROM results WHERE report_token=?", (token,)).fetchone()
    con.close()
    return row


def recent_results_for_account(account_id, limit=20):
    con = connect()
    rows = con.execute(
        "SELECT r.backtest_id, r.created_at, r.spec_json FROM results r "
        "JOIN api_keys k USING(key_id) WHERE k.account_id=? "
        "ORDER BY r.created_at DESC LIMIT ?", (account_id, limit)).fetchall()
    con.close()
    return rows


def recent_results(key_id, limit=20):
    con = connect()
    rows = con.execute(
        "SELECT backtest_id, created_at, spec_json FROM results WHERE key_id=? "
        "ORDER BY created_at DESC LIMIT ?", (key_id, limit)).fetchall()
    con.close()
    return rows


# ---------------------------------------------------------------- feedback

def record_feedback(entry, now=None):
    """File one report. Returns (feedback_id, is_new).

    UPSERT ON (account_id, dedup_key). The same person hitting the same wall twice is one
    item seen twice, not two items -- otherwise the queue fills with duplicates from the
    handful of people who use the service most, which is exactly backwards: those are the
    reports worth reading.

    Re-filing REFRESHES the body and context and bumps times_seen, and it reopens an item
    that was marked fixed. If someone reports it again after we closed it, we did not fix
    it, and the queue should say so rather than quietly swallowing the second report.
    """
    now = time.time() if now is None else now
    con = connect()
    try:
        with con:
            row = con.execute(
                "SELECT feedback_id, times_seen, status FROM feedback "
                "WHERE account_id=? AND dedup_key=?",
                (entry["account_id"], entry["dedup_key"])).fetchone()
            if row is not None:
                reopened = "reopened" if row["status"] in ("fixed", "wontfix") else row["status"]
                con.execute(
                    "UPDATE feedback SET times_seen=?, ts=?, updated_at=?, body=?, "
                    "context_json=?, backtest_id=COALESCE(?, backtest_id), severity=?, "
                    "tags_json=?, status=? WHERE feedback_id=?",
                    (row["times_seen"] + 1, now, now, entry["body"],
                     entry.get("context_json"), entry.get("backtest_id"),
                     entry.get("severity", "minor"), entry.get("tags_json", "[]"),
                     reopened, row["feedback_id"]))
                return row["feedback_id"], False
            feedback_id = "fb_" + secrets.token_hex(6)
            con.execute(
                "INSERT INTO feedback (feedback_id, ts, account_id, tier, source, category, "
                "tags_json, severity, title, body, backtest_id, context_json, dedup_key, "
                "status, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'new',?)",
                (feedback_id, now, entry["account_id"], entry.get("tier", "free"),
                 entry.get("source", "mcp"), entry["category"],
                 entry.get("tags_json", "[]"), entry.get("severity", "minor"),
                 entry["title"], entry["body"], entry.get("backtest_id"),
                 entry.get("context_json"), entry["dedup_key"], now))
            return feedback_id, True
    finally:
        con.close()


def get_feedback(feedback_id):
    con = connect()
    row = con.execute("SELECT * FROM feedback WHERE feedback_id=?",
                      (feedback_id,)).fetchone()
    con.close()
    return dict(row) if row else None


def feedback_for_account(account_id, limit=25):
    con = connect()
    rows = con.execute(
        "SELECT feedback_id, ts, category, severity, title, status, times_seen "
        "FROM feedback WHERE account_id=? ORDER BY ts DESC LIMIT ?",
        (account_id, limit)).fetchall()
    con.close()
    return [dict(r) for r in rows]


# Triage order. Open work first, and inside that, worst first. Expressed in SQL rather
# than sorted in Python because the queue is the one query that has to stay correct when
# the table is large enough that LIMIT actually bites.
_STATUS_RANK = ("CASE status WHEN 'new' THEN 0 WHEN 'reopened' THEN 0 WHEN 'triaged' "
                "THEN 1 WHEN 'in_progress' THEN 2 WHEN 'fixed' THEN 3 "
                "WHEN 'wontfix' THEN 4 ELSE 5 END")
_SEVERITY_RANK = ("CASE severity WHEN 'blocker' THEN 0 WHEN 'major' THEN 1 "
                  "WHEN 'minor' THEN 2 ELSE 3 END")


def list_feedback(status=None, category=None, limit=200):
    where, args = [], []
    if status == "open":
        where.append("status IN ('new','reopened','triaged','in_progress')")
    elif status:
        where.append("status=?")
        args.append(status)
    if category:
        where.append("category=?")
        args.append(category)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    con = connect()
    rows = con.execute(
        f"SELECT f.*, a.email, "
        f"(SELECT COUNT(DISTINCT account_id) FROM feedback d WHERE d.dedup_key=f.dedup_key) "
        f"AS accounts_affected "
        f"FROM feedback f LEFT JOIN accounts a USING(account_id) {clause} "
        f"ORDER BY {_STATUS_RANK}, {_SEVERITY_RANK}, accounts_affected DESC, f.ts DESC "
        f"LIMIT ?", (*args, limit)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def feedback_themes(limit=15):
    """Distinct reports grouped across accounts. This is the demand signal: one person
    asking is an opinion, nine people asking is a roadmap item."""
    con = connect()
    rows = con.execute(
        "SELECT dedup_key, category, MIN(title) AS title, "
        "COUNT(DISTINCT account_id) AS accounts, SUM(times_seen) AS reports, "
        "MAX(ts) AS last_seen, "
        "SUM(CASE WHEN status IN ('new','reopened','triaged','in_progress') THEN 1 ELSE 0 END) "
        "AS open_count "
        "FROM feedback GROUP BY dedup_key "
        "ORDER BY accounts DESC, reports DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def update_feedback(feedback_id, status=None, severity=None, category=None,
                    resolution=None, triage_note=None, now=None):
    now = time.time() if now is None else now
    sets, args = ["updated_at=?"], [now]
    for column, value in (("status", status), ("severity", severity),
                          ("category", category), ("resolution", resolution),
                          ("triage_note", triage_note)):
        if value is not None:
            sets.append(f"{column}=?")
            args.append(value)
    if status in ("fixed", "wontfix", "duplicate"):
        sets.append("resolved_at=?")
        args.append(now)
    con = connect()
    try:
        with con:
            con.execute(f"UPDATE feedback SET {', '.join(sets)} WHERE feedback_id=?",
                        (*args, feedback_id))
    finally:
        con.close()
    return get_feedback(feedback_id)


def feedback_counts():
    con = connect()
    by_status = con.execute(
        "SELECT status, COUNT(*) n FROM feedback GROUP BY status").fetchall()
    by_category = con.execute(
        "SELECT category, COUNT(*) n, "
        "SUM(CASE WHEN status IN ('new','reopened','triaged','in_progress') THEN 1 ELSE 0 END) "
        "open FROM feedback GROUP BY category ORDER BY n DESC").fetchall()
    con.close()
    return {"status": {r["status"]: r["n"] for r in by_status},
            "category": [dict(r) for r in by_category]}


# ---------------------------------------------------------------- admin console

def is_admin(account_id):
    con = connect()
    row = con.execute("SELECT is_admin FROM accounts WHERE account_id=?",
                      (account_id,)).fetchone()
    con.close()
    return bool(row and row["is_admin"])


def grant_admin(email, on=True):
    con = connect()
    try:
        with con:
            cur = con.execute("UPDATE accounts SET is_admin=? WHERE email=?",
                              (1 if on else 0, email))
        return cur.rowcount
    finally:
        con.close()


def admin_overview(now=None):
    """Everything the console's top strip shows, in one connection.

    Deliberately a handful of small aggregates rather than one clever query: the console
    is read by a person and by a triage agent a few times a day, and a query that is
    obvious beats a query that is fast at this size.
    """
    now = time.time() if now is None else now
    day, week = now - 86400, now - 7 * 86400
    con = connect()
    q = lambda sql, *a: con.execute(sql, a).fetchone()[0]          # noqa: E731
    out = {
        "accounts": q("SELECT COUNT(*) FROM accounts"),
        "accounts_paid": q("SELECT COUNT(*) FROM accounts WHERE tier!='free'"),
        "signups_24h": q("SELECT COUNT(*) FROM accounts WHERE created_at>=?", day),
        "signups_7d": q("SELECT COUNT(*) FROM accounts WHERE created_at>=?", week),
        "keys_active": q("SELECT COUNT(*) FROM api_keys WHERE revoked_at IS NULL"),
        "calls_24h": q("SELECT COUNT(*) FROM calls WHERE ts>=?", day),
        "calls_7d": q("SELECT COUNT(*) FROM calls WHERE ts>=?", week),
        "refusals_24h": q("SELECT COUNT(*) FROM calls WHERE ts>=? AND outcome='refused'", day),
        "errors_24h": q("SELECT COUNT(*) FROM calls WHERE ts>=? AND outcome='error'", day),
        "cpu_24h": q("SELECT COALESCE(SUM(cpu_seconds),0) FROM calls WHERE ts>=?", day),
        "points_24h": q("SELECT COALESCE(SUM(price_points),0) FROM calls WHERE ts>=?", day),
        "backtests_total": q("SELECT COUNT(*) FROM results"),
        "book_entries": q("SELECT COUNT(*) FROM strategy_book"),
        "feedback_open": q("SELECT COUNT(*) FROM feedback WHERE status IN "
                           "('new','reopened','triaged','in_progress')"),
        "feedback_total": q("SELECT COUNT(*) FROM feedback"),
        # An account that signed up and never called is a funnel leak, not a user. It is
        # the single number most likely to be flattering if left uncounted.
        "activated": q("SELECT COUNT(DISTINCT account_id) FROM calls"),
        "active_7d": q("SELECT COUNT(DISTINCT account_id) FROM calls WHERE ts>=?", week),
    }
    out["tools_7d"] = [dict(r) for r in con.execute(
        "SELECT tool, COUNT(*) n, SUM(CASE WHEN outcome='refused' THEN 1 ELSE 0 END) refused, "
        "SUM(CASE WHEN outcome='error' THEN 1 ELSE 0 END) errored "
        "FROM calls WHERE ts>=? AND tool IS NOT NULL GROUP BY tool ORDER BY n DESC",
        (week,)).fetchall()]
    # Top refusal reasons: what the service says "no" to most is the shortest list of what
    # to build or explain better next.
    out["refusals_7d"] = [dict(r) for r in con.execute(
        "SELECT refusal, COUNT(*) n FROM calls WHERE ts>=? AND outcome='refused' "
        "AND refusal IS NOT NULL GROUP BY refusal ORDER BY n DESC LIMIT 10",
        (week,)).fetchall()]
    con.close()
    return out


def admin_accounts(limit=100):
    con = connect()
    rows = con.execute(
        "SELECT a.account_id, a.email, a.tier, a.created_at, a.is_admin, "
        "(SELECT COUNT(*) FROM api_keys k WHERE k.account_id=a.account_id "
        " AND k.revoked_at IS NULL) AS keys, "
        "(SELECT COUNT(*) FROM calls c WHERE c.account_id=a.account_id) AS calls, "
        "(SELECT MAX(ts) FROM calls c WHERE c.account_id=a.account_id) AS last_seen, "
        "(SELECT COALESCE(SUM(cpu_seconds),0) FROM calls c "
        " WHERE c.account_id=a.account_id) AS cpu, "
        "(SELECT COUNT(*) FROM feedback f WHERE f.account_id=a.account_id) AS reports "
        "FROM accounts a ORDER BY a.created_at DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def admin_recent_calls(limit=60):
    con = connect()
    rows = con.execute(
        "SELECT c.ts, c.tool, c.outcome, c.refusal, c.tier, c.cpu_seconds, "
        "c.price_points, c.backtest_id, a.email "
        "FROM calls c LEFT JOIN accounts a USING(account_id) "
        "ORDER BY c.ts DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def daily_series(days=14, now=None):
    """Signups and calls per day, for the console's sparklines."""
    now = time.time() if now is None else now
    start = now - days * 86400
    con = connect()
    calls = {int(r["d"]): r["n"] for r in con.execute(
        "SELECT CAST((ts - ?) / 86400 AS INT) d, COUNT(*) n FROM calls "
        "WHERE ts>=? GROUP BY d", (start, start)).fetchall()}
    signups = {int(r["d"]): r["n"] for r in con.execute(
        "SELECT CAST((created_at - ?) / 86400 AS INT) d, COUNT(*) n FROM accounts "
        "WHERE created_at>=? GROUP BY d", (start, start)).fetchall()}
    con.close()
    return {"calls": [calls.get(i, 0) for i in range(days)],
            "signups": [signups.get(i, 0) for i in range(days)]}


# ---------------------------------------------------------------- full reports

FULL_REPORT_SCHEMA = """
CREATE TABLE IF NOT EXISTS full_reports (
  token TEXT PRIMARY KEY, backtest_id TEXT NOT NULL, account_id TEXT NOT NULL,
  created_at REAL NOT NULL, html TEXT NOT NULL, bytes INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS ix_full_account ON full_reports(account_id, created_at);
"""


def _ensure_full_reports(con):
    con.executescript(FULL_REPORT_SCHEMA)


def save_full_report(backtest_id, account_id, html, now=None):
    """Store the rendered page and return its token.

    The HTML is stored rather than re-rendered on each view: it embeds a charting library
    and the index series, so rebuilding it per request would turn a link somebody shared
    into a repeated seven-year query.
    """
    con = connect()
    try:
        _ensure_full_reports(con)
        token = secrets.token_urlsafe(24)
        with con:
            con.execute(
                "INSERT INTO full_reports (token, backtest_id, account_id, created_at, "
                "html, bytes) VALUES (?,?,?,?,?,?)",
                (token, backtest_id, account_id, now or time.time(), html, len(html)))
        return token
    finally:
        con.close()


def get_full_report(token):
    con = connect()
    try:
        _ensure_full_reports(con)
        row = con.execute("SELECT html FROM full_reports WHERE token=?", (token,)).fetchone()
        return row["html"] if row else None
    finally:
        con.close()


def full_report_quota(account_id, limit, now=None):
    """(allowed, used_in_the_last_hour). Rolling window, not a calendar hour."""
    now = time.time() if now is None else now
    con = connect()
    try:
        _ensure_full_reports(con)
        used = con.execute(
            "SELECT COUNT(*) FROM full_reports WHERE account_id=? AND created_at>=?",
            (account_id, now - 3600)).fetchone()[0]
        return used < limit, used
    finally:
        con.close()
