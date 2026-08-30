"""ClickHouse access for the Stratify engine.

Deliberately self-contained: the MCP service deploys separately from the ingestion
repo, so it does not import clickhouse_db.utils. Read-only by construction -- the
engine never writes to the serving database.
"""
import contextvars
import os
import threading

import clickhouse_connect

DATABASE = os.getenv("STRATIFY_DB", "stratify")

# Which ClickHouse user this request runs as. The tier boundary is enforced by a row
# policy and a settings profile attached to these users, so connecting as the wrong one is
# the only way a free key could see paid data -- and it is set once, at the edge, from the
# authenticated key. A context variable rather than a parameter because it must reach
# every query in the call tree without every function growing an argument it never reads.
TIER_USER = {"free": "stratify_free", "plus": "stratify_paid", "pro": "stratify_paid",
             "bench": "stratify_paid"}
# DEFAULTS TO THE LEAST-PRIVILEGED IDENTITY, not to `default`.
#
# `default` holds access_management and FILE/URL/S3/REMOTE, and carries no row policy. With
# it as the fallback, any query that ran outside a use_tier() binding -- one forgotten call
# site, one import-time query -- would silently read the entire history with full DDL
# rights instead of failing. Every current path does bind a tier, which is exactly when to
# change this: the failure mode of the wrong default is total, and the failure mode of this
# one is a visible permission error.
current_user = contextvars.ContextVar("clickhouse_user",
                                      default=os.getenv("CLICKHOUSE_USER", "stratify_free"))


def use_tier(tier):
    """Bind this request to the ClickHouse user for `tier`. Returns the token to reset."""
    return current_user.set(TIER_USER.get(tier, "stratify_free"))


# ONE CLIENT PER THREAD, NOT ONE PER PROCESS.
#
# clickhouse_connect's client is a session, and a session cannot run two queries at once --
# it raises "Attempt to execute concurrent queries within the same session". Under uvicorn,
# which runs synchronous endpoints on a thread pool, a process-wide cached client therefore
# turned every concurrent request into a queue behind a single HTTP connection.
#
# The symptom was a service that flatlined at 4-5 backtests per second no matter how much
# concurrency it was offered, while ClickHouse itself sat almost idle -- 10.7 CPU-seconds
# across 844 queries, p50 3 ms. The bottleneck was never the database; it was one shared
# socket in front of it.
_local = threading.local()


# Each ClickHouse identity has its own credential. The tier users were password-less until
# 2026-08-25, which meant anyone who could reach the database could read the full paid
# history directly -- bypassing the API, the quotas and the anti-oracle floors entirely.
_PASSWORD_ENV = {
    "default":        "CLICKHOUSE_PASSWORD",
    "stratify_free":  "CLICKHOUSE_FREE_PASSWORD",
    "stratify_paid":  "CLICKHOUSE_PAID_PASSWORD",
}


def _password_for(username):
    return os.getenv(_PASSWORD_ENV.get(username, ""), "")


def _client_for(username):
    cache = getattr(_local, "clients", None)
    if cache is None:
        cache = _local.clients = {}
    conn = cache.get(username)
    if conn is None:
        conn = cache[username] = clickhouse_connect.get_client(
            host=os.getenv("CLICKHOUSE_HOST", "localhost"),
            port=int(os.getenv("CLICKHOUSE_HTTP_PORT", "8123")),
            username=username,
            password=_password_for(username),
            database=DATABASE,
        )
    return conn


def client():
    return _client_for(current_user.get())


# ClickHouse's own result cache. The serving tables are rebuilt, never mutated in place,
# so a cached result cannot go stale under a running query -- and several of the queries a
# backtest issues (the cycle calendar, the spot series, settlement prices) are identical
# for EVERY user running at that entry time, which is exactly what a shared cache is for.
QUERY_CACHE_SETTINGS = {
    "use_query_cache": 1,
    "query_cache_ttl": 3600,
    "query_cache_min_query_duration": 10,   # don't cache trivia
    "query_cache_share_between_users": 0,
    # ClickHouse refuses by default to cache a query containing a function it labels
    # non-deterministic -- argMax and the quantile family among them, because tie-breaking
    # is not specified. Within a FIXED dataset those results are stable, and the serving
    # tables are rebuilt rather than mutated, so there is no window in which a cached
    # answer and a fresh one could disagree. 'save' is therefore correct here and would
    # not be on a table that is written to underneath readers.
    "query_cache_nondeterministic_function_handling": "save",
}


class CapacityExceeded(RuntimeError):
    """The TIER's ClickHouse quota is spent, not the account's.

    Two different limits can stop a backtest and they mean opposite things. The account
    quota in server/quota.py is "you have used your share"; this one is "the shared
    serving identity for your tier has used ITS share of the database for this hour",
    which is a capacity fact about the service and nothing the caller did wrong.

    Without this the driver's error reached the dispatcher's blanket handler and the
    caller was told "internal error" -- the least useful sentence available, for the one
    failure that has an exact and honest explanation and a known time at which it lifts.
    """

    def __init__(self, message, retry_after_seconds=None):
        super().__init__(message)
        self.message = message
        self.retry_after_seconds = retry_after_seconds


_QUOTA_MARKERS = ("QUOTA_EXCEEDED", "Quota for user")


def _translate(exc):
    text = str(exc)
    if not any(m in text for m in _QUOTA_MARKERS):
        return exc
    # ClickHouse states the interval end in the message; pass the wait on rather than
    # guessing, and fall back to the top of the next hour when the format changes.
    import datetime as _dt
    import re as _re
    wait = None
    m = _re.search(r"Interval will end at ([\d]{4}-[\d]{2}-[\d]{2} [\d:]{8})", text)
    now = _dt.datetime.utcnow()
    if m:
        try:
            wait = max(1, int((_dt.datetime.fromisoformat(m.group(1)) - now)
                              .total_seconds()))
        except ValueError:
            wait = None
    if wait is None:
        wait = max(1, 3600 - (now.minute * 60 + now.second))
    return CapacityExceeded(
        f"the shared query budget for this tier is spent for the current hour; it "
        f"refills in about {wait // 60} minute(s). Nothing is wrong with the strategy — "
        f"re-run it after that.", retry_after_seconds=wait)


def rows(sql, parameters=None, cache=True):
    """Query returning a list of dicts."""
    try:
        r = client().query(sql, parameters=parameters or {},
                           settings=QUERY_CACHE_SETTINGS if cache else None)
    except Exception as exc:                      # noqa: BLE001
        raise _translate(exc) from exc
    return [dict(zip(r.column_names, row)) for row in r.result_rows]


def scalar(sql, parameters=None):
    try:
        r = client().query(sql, parameters=parameters or {})
    except Exception as exc:                      # noqa: BLE001
        raise _translate(exc) from exc
    return r.result_rows[0][0] if r.result_rows else None
