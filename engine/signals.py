"""Gates and biases — reused from production, not reimplemented.

`BACKTEST/weekly_options_research/signals.py` is loaded by path and its functions are
called directly. Porting them would create two definitions of "bullish" that could drift;
the whole point of decision A1 is that the MCP, the research pipeline and paper trading
speak one language. 4 gates x 11 biases, exposed by name.

LOOKAHEAD, which is the real hazard here. Production's signals take an `asof_date` and read
every row up to and including it. Handing them a frame whose entry-day row holds that day's
CLOSE would let a strategy entering at 09:30 see the 15:29 close — a backtest that prints
beautiful results for a reason that has nothing to do with the strategy.

So the frame this module builds is truncated at the entry minute: for every day before the
entry day the row is the full session, and for the entry day itself open/high/low/close are
computed only from bars at or before `entry_time`. Everything in the frame was knowable at
the moment of entry. That also makes `bias_gap` work honestly — it compares today's open to
yesterday's close, which is exactly what is known at the open.

`test_signals.py` asserts the truncation directly: a frame built for 09:30 must not contain
the day's true high, low or close whenever the session moved after 09:30.
"""
import functools
import importlib.util
import os
from pathlib import Path

import pandas as pd

from . import db

PRODUCTION_SIGNALS = Path(os.getenv(
    "STRATIFY_SIGNALS_PATH",
    Path(__file__).resolve().parents[2] / "BACKTEST/weekly_options_research/signals.py"))


@functools.lru_cache(maxsize=1)
def _production():
    spec = importlib.util.spec_from_file_location("prod_signals", PRODUCTION_SIGNALS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def gate_names():
    return sorted(_production().GATES)


def bias_names():
    return sorted(_production().BIASES)


def _daily(entry_minute):
    """Tier-scoped wrapper. See _daily_cached for why the tier has to be in the key."""
    return _daily_cached(entry_minute, db.current_user.get())


@functools.lru_cache(maxsize=32)
def _daily_cached(entry_minute, ch_user):
    """Daily spot OHLC in two forms: the full session, and the session truncated at
    `entry_minute`. Both come from spot_1min, which covers all 246 days with real
    OHLC — the old close-only spot series could not have computed six of these signals.

    `ch_user` IS PART OF THE KEY AND IS NOT OTHERWISE USED. This query has no date
    predicate: what it returns depends entirely on the row policy of the ClickHouse user
    running it. Measured, the same SQL yields 91,936 rows over 2025-07 → 2026-06 as
    `stratify_free` and 702,371 over 2019-01 → 2026-08 as `stratify_paid`.

    Keyed on entry_minute alone, one process-local cache served both. A free-tier gate then
    evaluated against seven years of paid history it must not see — a boundary breach, and
    an oracle a patient caller can probe — while a paid seven-year backtest evaluated its
    gates against one year whenever a free request warmed the cache first, which is an
    integrity bug in the direction the customer is paying against.
    """
    rows = db.rows(
        f"""
        SELECT toDate(timestamp) AS date,
               argMin(open, timestamp)  AS full_open,  max(high) AS full_high,
               min(low) AS full_low,    argMax(close, timestamp) AS full_close,
               argMinIf(open, timestamp, mod_ <= %(m)s)  AS part_open,
               maxIf(high, mod_ <= %(m)s)                AS part_high,
               minIf(low,  mod_ <= %(m)s)                AS part_low,
               argMaxIf(close, timestamp, mod_ <= %(m)s) AS part_close,
               countIf(mod_ <= %(m)s)                    AS part_bars
        FROM ( SELECT timestamp, open, high, low, close,
                      toHour(timestamp) * 60 + toMinute(timestamp) AS mod_
               FROM {db.DATABASE}.spot_1min )
        GROUP BY date ORDER BY date
        """,
        {"m": entry_minute})
    return pd.DataFrame(rows)


def frame_at(entry_date, entry_minute):
    return _frame_at_cached(entry_date, entry_minute, db.current_user.get())


@functools.lru_cache(maxsize=4096)
def _frame_at_cached(entry_date, entry_minute, ch_user):
    """Spot history as it stood at `entry_time` on `entry_date`. Nothing later, and nothing
    from later in the entry day itself."""
    d = _daily(entry_minute)
    hist = d[d["date"] < entry_date][["date", "full_open", "full_high", "full_low", "full_close"]]
    hist = hist.rename(columns={"full_open": "open", "full_high": "high",
                                "full_low": "low", "full_close": "close"})
    today = d[d["date"] == entry_date]
    if len(today) and int(today["part_bars"].iloc[0]) > 0:
        row = today.iloc[0]
        hist = pd.concat([hist, pd.DataFrame([{
            "date": entry_date, "open": row["part_open"], "high": row["part_high"],
            "low": row["part_low"], "close": row["part_close"]}])], ignore_index=True)
    return hist


def evaluate(gate_name, bias_name, entry_date, entry_minute):
    """-> (allowed: bool, bias: 'bullish'|'bearish'|'neutral')

    The default combination -- no gate, no bias -- short-circuits before touching pandas.
    It was building a DataFrame per cycle to answer "yes, always", which made the signal
    layer one of the largest CPU items in a backtest that used no signals at all.
    """
    if gate_name == "always" and bias_name == "neutral":
        return True, "neutral"
    prod = _production()
    if gate_name not in prod.GATES:
        raise KeyError(f"unknown gate {gate_name!r}; available: {', '.join(gate_names())}")
    if bias_name != "neutral" and bias_name not in prod.BIASES:
        raise KeyError(f"unknown bias {bias_name!r}; available: {', '.join(bias_names())}")
    frame = frame_at(entry_date, entry_minute)
    if frame.empty:
        return False, "neutral"
    allowed = bool(prod.GATES[gate_name](frame, entry_date))
    bias = ("neutral" if bias_name == "neutral"
            else prod.BIASES[bias_name](frame, entry_date))
    return allowed, bias


def overlay_allows(overlay, entry_date, entry_minute):
    """-> True unless the spec's vol overlay blocks this cycle.

    Production's `overlay_allows_entry` on the SAME truncated frame the gates and biases
    read, so the 20-day realised vol ends at the entry minute -- the last return in the
    window is against the spot at entry, not the day's close (the research applies the
    filter with `entry_time` for exactly this reason). An unknown vol (not enough history)
    lets the trade through, as production does.
    """
    if not overlay:
        return True
    frame = frame_at(entry_date, entry_minute)
    if frame.empty:
        return True
    allowed, _rvol, _cut = _production().overlay_allows_entry(frame, entry_date, overlay)
    return bool(allowed)
