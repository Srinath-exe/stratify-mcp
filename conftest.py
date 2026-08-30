"""Make the suite runnable without the serving database, honestly.

THE SITUATION. Most of what this engine does is read ClickHouse, and this repository ships
no data -- that is deliberate and permanent. So a clone, and every CI runner, has ~322 of
the ~536 tests it can actually run and ~174 it cannot. Both numbers matter: the first is
the signal, and pretending the second does not exist is how a green tick starts meaning
nothing.

WHAT THIS DOES. It probes ClickHouse once per session. If the database answers, nothing
here changes anything and the full suite runs exactly as it does in production. If it does
not answer, a test that fails *because of that* is reported as a SKIP naming the reason,
rather than as a failure -- while a test that fails for any other reason still fails.

WHY NOT A MARKER LIST. Marking each database test by hand means a list that goes stale the
first time somebody adds one and forgets, and a stale list silently converts a real failure
into a skip. Deciding from the actual exception cannot go stale, and it cannot swallow a
genuine bug: the error has to be a connection error, AND the database has to be confirmed
unreachable, before anything is reclassified.

Run `pytest -rs` to see every skip and its reason. CI does.
"""
import os
import socket

import pytest

_SKIP_REASON = (
    "needs the serving ClickHouse database, which is not reachable from here. "
    "This repository ships no market data -- see data/README.md. Point "
    "CLICKHOUSE_HOST/CLICKHOUSE_HTTP_PORT at an instance to run these."
)

# Substrings that identify a failure to REACH the database, as opposed to a query that
# reached it and was wrong. Kept narrow on purpose: a widening here is a widening of what
# can be silently downgraded from a failure to a skip.
_CONNECTION_ERRORS = (
    "connection refused", "failed to connect", "name or service not known",
    "temporary failure in name resolution", "connection reset",
    "max retries exceeded", "timed out", "no route to host",
)

_SIGNALS_SKIP = (
    "needs the signals module, an optional runtime input this repository does not ship. "
    "See vendor/README.md -- it is two dicts, GATES and BIASES, and any module satisfying "
    "that contract works. Point STRATIFY_SIGNALS_PATH at one to run these."
)

_reachable = None


def _database_is_reachable():
    """One TCP probe per session. Cached, because this is asked once per failing test."""
    global _reachable
    if _reachable is None:
        host = os.getenv("CLICKHOUSE_HOST", "localhost")
        port = int(os.getenv("CLICKHOUSE_HTTP_PORT", "8123"))
        try:
            with socket.create_connection((host, port), timeout=2):
                _reachable = True
        except OSError:
            _reachable = False
    return _reachable


def _looks_like_a_connection_failure(excinfo):
    if excinfo is None:
        return False
    text = f"{excinfo.typename}: {excinfo.value}".lower()
    if any(marker in text for marker in _CONNECTION_ERRORS):
        return True
    # ONE SPECIFIC EXTRA SHAPE. Tests that drive the HTTP path never see a connection
    # error: the endpoint catches it and answers with its own generic error, so the test
    # dies on a missing "result" key instead. That KeyError has exactly one cause in this
    # suite -- an MCP response that carried an error rather than a result -- and it is
    # still gated on the database being confirmed unreachable, so it cannot mask a fault
    # on a machine where the database is up.
    return excinfo.typename == "KeyError" and str(excinfo.value) in ("'result'", '"result"')


def _is_missing_signals(excinfo):
    if excinfo is None or excinfo.typename != "FileNotFoundError":
        return False
    from engine import signals
    return "signals.py" in str(excinfo.value) and not signals.PRODUCTION_SIGNALS.exists()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.outcome != "failed":
        return
    # BOTH conditions, deliberately. A connection-shaped error while the database is up is
    # a real fault and must stay a failure -- that is exactly how an intermittent outage or
    # a misconfigured pool would announce itself.
    if _looks_like_a_connection_failure(call.excinfo) and not _database_is_reachable():
        report.outcome = "skipped"
        report.longrepr = (str(item.path), item.location[1], f"Skipped: {_SKIP_REASON}")
        return
    # The signals module is the OTHER optional runtime input. Gates and biases are loaded
    # from the production definitions rather than reimplemented, so this service and the
    # live trading daemon cannot drift into two definitions of "bullish" -- which means a
    # clone without it genuinely cannot evaluate a gate. Same two conditions: the error has
    # to be the file being missing, and the file has to actually be missing.
    if _is_missing_signals(call.excinfo):
        report.outcome = "skipped"
        report.longrepr = (str(item.path), item.location[1], f"Skipped: {_SIGNALS_SKIP}")


def skip_if_the_database_is_why_this_failed(response):
    """Turn "the database is not here" into a skip, for tests that drive the HTTP path.

    Those tests never see a connection exception: the endpoint catches it and returns its
    own generic internal error, and the test then fails on a missing "result" key -- which
    reports a missing database as a broken feature. The hook above cannot see through that.
    This can, because it is holding the response.

    Both conditions again. An internal error while the database IS up stays a failure.
    """
    error = (response or {}).get("error") or {}
    if error.get("code") == -32603 and not _database_is_reachable():
        pytest.skip(_SKIP_REASON)
    return response


def pytest_report_header(config):
    state = "reachable" if _database_is_reachable() else "NOT reachable"
    return (f"clickhouse: {state} "
            f"({os.getenv('CLICKHOUSE_HOST', 'localhost')}:"
            f"{os.getenv('CLICKHOUSE_HTTP_PORT', '8123')})")
