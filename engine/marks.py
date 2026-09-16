"""Reading the chain and the tape at arbitrary minutes.

v1 could only price at nine clock times because `contract_day` precomputes a print for
each of them, and that cost optimisation had hardened into a capability limit: entering at
09:20 was refused, not because it is exotic but because the fast table had no column for
it. This module keeps the fast path and adds the general one behind it.

    chain_at()   every strike's last real print in the bucket ending at any minute
    paths()      per-minute marks for a chosen set of contracts over a window
    spot_at()    the index at any minute
    settlement() NSE's final settlement: the mean of the index over the last 30 minutes

THE BUCKET, AND WHY IT IS NOT A SINGLE MINUTE. An option that did not trade in a given
minute has no price in that minute -- only a stale one. Taking the last real print in the
15 minutes ending at the requested time is the same rule v1 used at its nine times, so a
09:20 entry is priced exactly the way an 09:30 entry always was. `volume > 0` is
structural, not a filter: a bar with no volume did not happen.
"""
import datetime as dt
import functools

from . import db


def _naive(ts):
    """ClickHouse hands back tz-aware datetimes for a DateTime('Asia/Kolkata') column;
    every timestamp the engine constructs is a naive IST wall clock. Mixing them raises on
    the first comparison, so they are normalised once here at the boundary rather than
    defensively at each of the dozen places they meet."""
    return ts.replace(tzinfo=None) if ts.tzinfo is not None else ts

BUCKET_MINUTES = 15               # matches backtest.ENTRY_BUCKET_MINUTES
SETTLEMENT_WINDOW_MINUTES = 30
SESSION_CLOSE_MINUTE = 15 * 60 + 29

# The nine minutes contract_day already holds. Hitting one of these turns a scan of
# options_1min into a lookup in a 232 K-row table -- worth keeping, worth never being a
# limit on what can be asked for.
FAST_COLUMN = {555: "ent_0915", 570: "ent_0930", 660: "ent_1100", 720: "ent_1200",
               750: "ent_1230", 780: "ent_1300", 840: "ent_1400", 900: "ent_1500",
               929: "ent_1529"}


def chain_at(pairs, minute, symbol="NIFTY"):
    """pairs: [(expiry_date, trade_date)] -> {(expiry, date): {"CE": {k: px}, "PE": {...}}}

    One query for every cycle at once. Which query depends only on whether the minute is
    one contract_day already knows.
    """
    if not pairs:
        return {}
    pairs = sorted(set(pairs))
    col = FAST_COLUMN.get(minute)
    if col:
        rows = db.rows(
            f"""SELECT toYYYYMMDD(expiry_date) AS ek, toYYYYMMDD(trade_date) AS dk,
                       option_type, strike_price, {col} AS px
                FROM {db.DATABASE}.contract_day
                WHERE (expiry_date, trade_date) IN %(pairs)s AND {col} > 0""",
            {"pairs": pairs})
    else:
        rows = db.rows(
            f"""SELECT toYYYYMMDD(expiry_date) AS ek, toYYYYMMDD(toDate(timestamp)) AS dk,
                       option_type, strike_price,
                       argMax(close, timestamp) AS px
                FROM {db.DATABASE}.options_1min
                WHERE (expiry_date, toDate(timestamp)) IN %(pairs)s
                  AND (toHour(timestamp) * 60 + toMinute(timestamp))
                        BETWEEN %(lo)s AND %(hi)s
                  AND volume > 0
                GROUP BY expiry_date, dk, option_type, strike_price
                HAVING px > 0""",
            {"pairs": pairs, "lo": minute - BUCKET_MINUTES + 1, "hi": minute})
    by_key = {(int(e.strftime("%Y%m%d")), int(d.strftime("%Y%m%d"))): (e, d)
              for e, d in pairs}
    snap = {}
    for r in rows:
        key = by_key.get((r["ek"], r["dk"]))
        if key is None:
            continue
        snap.setdefault(key, {"CE": {}, "PE": {}})[r["option_type"]][
            int(r["strike_price"])] = float(r["px"])
    return snap


# The served result cap is a TIER setting, not a constant: 200k on the paid users and
# 50k on the free one. Sized under the smallest of them and halved again on the way down
# if a batch still overruns, so the engine cannot be broken by someone tightening the
# profile later. Costs extra round trips on the paid tiers and never costs a wrong answer.
ROW_BUDGET = 40_000
MIN_BATCH = 2
SESSION_MINUTES = 375


def paths(keys, t_from, t_to, resolution=1, max_days_open=None):
    """keys: [(expiry_date, option_type, strike)] -> {key: {datetime: mark}}

    Only the contracts a position actually holds, and only the window it is open for.
    That is what keeps a minute-by-minute walk affordable: the expensive table is never
    scanned broadly, it is read along a handful of narrow columns.

    BATCHED AGAINST THE SERVED ROW CAP. A served result is capped at 200,000 rows, and
    fifty four-leg cycles over three days is a quarter of a million -- the query does not
    slow down, it FAILS. The batch size is derived from how long a position can be open
    rather than guessed, because the same guess that works for a 2-DTE condor is an order
    of magnitude wrong for a 45-DTE one.

    `resolution` thins the walk to every Nth minute. It is a search-affordability knob and
    it is honest about what it costs -- a 15-minute walk cannot see a trigger that fires
    and reverses inside the bar -- so it defaults to every minute.
    """
    if not keys:
        return {}
    keys = sorted(set(keys))
    days = max(1, int(max_days_open or 5))
    per_key = max(1, days * SESSION_MINUTES // max(1, int(resolution)))
    batch = max(MIN_BATCH, min(len(keys), ROW_BUDGET // per_key))
    out = {}
    for i in range(0, len(keys), batch):
        _fetch_splitting(keys[i:i + batch], t_from, t_to, resolution, max_days_open, out)
    return out


def _too_many_rows(exc):
    return "TOO_MANY_ROWS_OR_BYTES" in str(exc) or "Limit for result exceeded" in str(exc)


def _fetch_splitting(keys, t_from, t_to, resolution, max_days_open, out):
    """Halve and retry when a batch overruns the cap. A derived batch size can still be
    wrong -- a contract that traded every minute of a long life is many times the typical
    one -- and the failure mode is an exception, not a slow query, so it has to be
    recovered from rather than tuned around."""
    try:
        _paths_batch(keys, t_from, t_to, resolution, max_days_open, out)
    except Exception as exc:
        if not _too_many_rows(exc) or len(keys) <= MIN_BATCH:
            raise
        mid = len(keys) // 2
        _fetch_splitting(keys[:mid], t_from, t_to, resolution, max_days_open, out)
        _fetch_splitting(keys[mid:], t_from, t_to, resolution, max_days_open, out)


def _paths_batch(keys, t_from, t_to, resolution, max_days_open, out):
    # modulo(), not the % operator. clickhouse_connect binds parameters with Python's
    # own `query % params`, so a literal % anywhere in a parameterised query is read as a
    # format spec and the driver raises "not enough arguments for format string" before
    # the SQL is ever sent. Both coarse resolutions were dead on arrival because of it.
    res_clause = ("" if resolution <= 1 else
                  f"AND modulo(toMinute(timestamp), {int(resolution)}) = 0")
    # A contract listed weeks before expiry has most of its bars before any entry. The
    # bound turns that into granule elimination via the minmax index on dte.
    dte_clause = ("" if max_days_open is None else
                  f"AND dateDiff('day', toDate(timestamp), expiry_date) "
                  f"<= {int(max_days_open)}")
    rows = db.rows(
        f"""SELECT expiry_date, option_type, strike_price, timestamp, close
            FROM {db.DATABASE}.options_1min
            WHERE (expiry_date, option_type, strike_price) IN %(keys)s
              AND timestamp >= toDateTime(%(t0)s, 'Asia/Kolkata')
              AND timestamp <= toDateTime(%(t1)s, 'Asia/Kolkata')
              AND volume > 0
              {dte_clause} {res_clause}
            ORDER BY timestamp""",
        {"keys": keys, "t0": t_from.strftime("%Y-%m-%d %H:%M:%S"),
         "t1": t_to.strftime("%Y-%m-%d %H:%M:%S")})
    for r in rows:
        out.setdefault((r["expiry_date"], r["option_type"], int(r["strike_price"])),
                       {})[_naive(r["timestamp"])] = float(r["close"])


def spot_series(t_from, t_to, resolution=1):
    """{datetime: index} over a window, sliced under the served row cap.

    A minute of index is one row and a year is 92,000 of them, so a chunk spanning two
    and a half years is past the 200,000-row ceiling -- the query does not slow down, it
    fails. Sliced by calendar year, which is well inside the cap at every resolution.
    """
    out = {}
    step = dt.timedelta(days=90)      # ~34k index minutes, inside the free cap
    a = t_from
    while a <= t_to:
        b = min(t_to, a + step)
        try:
            _spot_slice(a, b, resolution, out)
        except Exception as exc:
            if not _too_many_rows(exc):
                raise
            mid = a + (b - a) / 2
            _spot_slice(a, mid, resolution, out)
            _spot_slice(mid, b, resolution, out)
        a = b + dt.timedelta(minutes=1)
    return out


def _spot_slice(t_from, t_to, resolution, out):
    res_clause = ("" if resolution <= 1 else
                  f"AND modulo(toMinute(timestamp), {int(resolution)}) = 0")
    rows = db.rows(
        f"""SELECT timestamp, close FROM {db.DATABASE}.spot_1min
            WHERE timestamp >= toDateTime(%(t0)s, 'Asia/Kolkata')
              AND timestamp <= toDateTime(%(t1)s, 'Asia/Kolkata')
              {res_clause}
            ORDER BY timestamp""",
        {"t0": t_from.strftime("%Y-%m-%d %H:%M:%S"),
         "t1": t_to.strftime("%Y-%m-%d %H:%M:%S")})
    for r in rows:
        out[_naive(r["timestamp"])] = float(r["close"])


def spot_at(days, minute):
    """{date: index at or before `minute`}. Used for entry, where one number per day is
    all that is needed and pulling the whole tape would be wasteful."""
    if not days:
        return {}
    return _spot_at_cached(tuple(sorted(days)), minute, db.DATABASE, db.current_user.get())


@functools.lru_cache(maxsize=256)
def _spot_at_cached(days, minute, database, ch_user):
    rows = db.rows(
        f"""SELECT toDate(timestamp) AS d, argMax(close, timestamp) AS spot
            FROM {database}.spot_1min
            WHERE toDate(timestamp) IN %(days)s
              AND (toHour(timestamp) * 60 + toMinute(timestamp)) <= %(m)s
            GROUP BY d""",
        {"days": list(days), "m": minute})
    return {r["d"]: float(r["spot"]) for r in rows}


def settlement(expiries):
    """NSE final settlement price: the mean of the index over the last 30 minutes. Not the
    15:29 close, and not the option's last print."""
    if not expiries:
        return {}
    return _settlement_cached(tuple(sorted(expiries)), db.DATABASE,
                              db.current_user.get())


@functools.lru_cache(maxsize=256)
def _settlement_cached(expiries, database, ch_user):
    rows = db.rows(
        f"""SELECT toDate(timestamp) AS d, avg(close) AS settle
            FROM {database}.spot_1min
            WHERE toDate(timestamp) IN %(days)s
              AND (toHour(timestamp) * 60 + toMinute(timestamp)) > %(m)s - %(w)s
            GROUP BY d""",
        {"days": list(expiries), "m": SESSION_CLOSE_MINUTE,
         "w": SETTLEMENT_WINDOW_MINUTES})
    return {r["d"]: float(r["settle"]) for r in rows}


def expiries_between(date_from, date_to, symbol="NIFTY"):
    """Every expiry with data, and the trading days that precede each. The cycle model is
    built from this rather than from a calendar: a holiday that moved an expiry is in the
    data and is not in anyone's calendar file."""
    return _expiries_cached(date_from, date_to, db.DATABASE, db.current_user.get())


@functools.lru_cache(maxsize=32)
def _expiries_cached(date_from, date_to, database, ch_user):
    rows = db.rows(
        f"""SELECT expiry_date, groupUniqArray(trade_date) AS days
            FROM {database}.contract_day
            WHERE trade_date BETWEEN %(a)s AND %(b)s
            GROUP BY expiry_date ORDER BY expiry_date""",
        {"a": date_from, "b": date_to})
    return [(r["expiry_date"], sorted(r["days"])) for r in rows]


def market_days(d_from, d_to):
    """{date: the day's market state} -- one small query for the whole run.

    EVERY COLUMN HERE IS KNOWABLE AT 09:15 OF ITS OWN DAY. The table also stores that
    day's high, low and close so it can be rebuilt and audited; those are deliberately not
    read here and are not rule fields. Reading them is precisely the leak that made 29 of
    51 live strategies fantasy the last time, so the boundary is drawn at the query rather
    than left to whoever writes the next condition.

    A few hundred rows on the free window, under two thousand on the longest, so it is
    fetched once for the whole backtest rather than per chunk.
    """
    rows = db.rows(
        f"""SELECT d, dow, prev_close, gap_pct, prev_day_move_pct, realised_vol_20d,
                   vix_prev_close
            FROM {db.DATABASE}.market_day
            WHERE d >= %(a)s AND d <= %(b)s""",
        {"a": d_from.isoformat(), "b": d_to.isoformat()})
    return {r["d"]: {"dow": int(r["dow"]), "prev_close": float(r["prev_close"]),
                     "gap_pct": float(r["gap_pct"]),
                     "prev_day_move_pct": float(r["prev_day_move_pct"]),
                     "realised_vol_20d": float(r["realised_vol_20d"]),
                     "vix_prev_close": float(r["vix_prev_close"])} for r in rows}


def vix_series(t_from, t_to, resolution=1):
    """{datetime: India VIX} over a window, sliced under the served row cap.

    The LIVE print at the minute being evaluated, exactly like spot. That is what a trader
    is looking at when they decide, and unlike a daily close it carries no look-ahead of
    any kind -- so a rule may test it during the trade as freely as at entry.
    """
    out = {}
    step = dt.timedelta(days=90)
    a = t_from
    while a <= t_to:
        b = min(t_to, a + step)
        try:
            _vix_slice(a, b, resolution, out)
        except Exception as exc:
            if not _too_many_rows(exc):
                raise
            mid = a + (b - a) / 2
            _vix_slice(a, mid, resolution, out)
            _vix_slice(mid, b, resolution, out)
        a = b + dt.timedelta(minutes=1)
    return out


def _vix_slice(t_from, t_to, resolution, out):
    res_clause = ("" if resolution <= 1 else
                  f"AND modulo(toMinute(timestamp), {int(resolution)}) = 0")
    rows = db.rows(
        f"""SELECT timestamp, close FROM {db.DATABASE}.vix_1min
            WHERE timestamp >= toDateTime(%(t0)s, 'Asia/Kolkata')
              AND timestamp <= toDateTime(%(t1)s, 'Asia/Kolkata')
              {res_clause}
            ORDER BY timestamp""",
        {"t0": t_from.strftime("%Y-%m-%d %H:%M:%S"),
         "t1": t_to.strftime("%Y-%m-%d %H:%M:%S")})
    for r in rows:
        out[_naive(r["timestamp"])] = float(r["close"])


def entry_days(cadence, dte, max_dte, date_from, date_to):
    """The (expiry, trade_date) pairs a cadence enters on -- the SAME query v1 uses.

    Shared deliberately. The weekly rule is "the trading day CLOSEST to entry_dte", not
    "exactly entry_dte days out": holidays move expiries, and an exact match silently
    drops four cycles in five. Reimplementing that rule in the new engine produced ten
    trades where the old one produced fifty-one, which is the kind of divergence that
    makes two engines untrustworthy even when both are individually defensible.
    """
    return _entry_days_cached(cadence, dte, max_dte, date_from, date_to,
                              db.DATABASE, db.current_user.get())


@functools.lru_cache(maxsize=256)
def _entry_days_cached(cadence, dte, max_dte, date_from, date_to, database, ch_user):
    if cadence == "daily":
        bound = 45 if max_dte is None else int(max_dte)
        rows = db.rows(
            f"""SELECT expiry_date, trade_date, dte FROM (
                  SELECT expiry_date, trade_date, dte,
                         row_number() OVER (PARTITION BY trade_date
                                            ORDER BY dte, expiry_date) AS rn
                  FROM (SELECT DISTINCT expiry_date, trade_date, dte
                        FROM {database}.contract_day
                        WHERE trade_date >= %(d0)s AND trade_date <= %(d1)s
                          AND expiry_date <= %(d1)s AND dte <= %(mdte)s))
                WHERE rn = 1 ORDER BY trade_date""",
            {"d0": date_from, "d1": date_to, "mdte": bound})
        return list(rows)
    # MONTHLY is the weekly rule restricted to the last expiry of each calendar month --
    # taken from what actually listed, not from a rule about which Thursday, because the
    # exchange has moved the expiry weekday more than once inside this history. The flag
    # is read from market_day rather than contract_spec so the tier's own row policy
    # applies to it like everything else.
    monthly_clause = ("""AND expiry_date IN (SELECT d FROM {db}.market_day
                                             WHERE is_monthly_expiry = 1)"""
                      .format(db=database) if cadence == "monthly" else "")
    rows = db.rows(
        f"""SELECT expiry_date, trade_date, dte FROM (
              SELECT expiry_date, trade_date, dte,
                     row_number() OVER (PARTITION BY expiry_date
                                        ORDER BY abs(toInt32(dte) - %(dte)s),
                                                 trade_date) AS rn
              FROM (SELECT DISTINCT expiry_date, trade_date, dte
                    FROM {database}.contract_day
                    WHERE trade_date >= %(d0)s AND trade_date <= %(d1)s
                      AND expiry_date <= %(d1)s AND dte <= 45
                      {monthly_clause}))
            WHERE rn = 1 ORDER BY expiry_date""",
        {"dte": int(dte if dte is not None else 4), "d0": date_from, "d1": date_to})
    return [r for r in rows if r["dte"] <= 45]


def trading_days(date_from, date_to):
    return _trading_days_cached(date_from, date_to, db.DATABASE, db.current_user.get())


@functools.lru_cache(maxsize=32)
def _trading_days_cached(date_from, date_to, database, ch_user):
    rows = db.rows(
        f"""SELECT DISTINCT toDate(timestamp) AS d FROM {database}.spot_1min
            WHERE toDate(timestamp) BETWEEN %(a)s AND %(b)s ORDER BY d""",
        {"a": date_from, "b": date_to})
    return [r["d"] for r in rows]


# ---------------------------------------------------------------- indicators

# How far back to read closes so the longest window is warm on the first day of the run.
# A 250-session EMA needs roughly 3x its window to converge from a cold start; 1,200
# calendar days covers that with the holidays in.
_INDICATOR_WARMUP_DAYS = 1200


def indicator_days(d_from, d_to, specs):
    """{date: {indicator_name: value}} for every session in [d_from, d_to].

    EVERY VALUE IS AS OF 09:15 OF ITS DAY, computed on the closes of the sessions BEFORE
    it. market_day.prev_close is exactly that series -- yesterday's close, stored on
    today's row -- so an indicator built on prev_close cannot see the current day even by
    accident. That is the whole look-ahead defence and it is structural, not a check.

    One query for the run, extended backwards so the first day's window is full. The
    arithmetic is a few thousand floats per indicator: measured in microseconds, so a
    strategy gated on three averages costs the same as one gated on none.
    """
    if not specs:
        return {}
    import datetime as _dt
    start = d_from - _dt.timedelta(days=_INDICATOR_WARMUP_DAYS)
    rows = db.rows(
        f"""SELECT d, prev_close FROM {db.DATABASE}.market_day
            WHERE d >= %(a)s AND d <= %(b)s ORDER BY d""",
        {"a": start.isoformat(), "b": d_to.isoformat()})
    dates = [r["d"] for r in rows]
    closes = [float(r["prev_close"]) for r in rows]
    out = {d: {} for d in dates if d_from <= d <= d_to}
    for name, spec in specs.items():
        series = _indicator_series(spec, closes)
        for d, v in zip(dates, series):
            if d_from <= d <= d_to:
                out[d][name] = v
    return out


def _sma(xs, n):
    out, s = [None] * len(xs), 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n:
            s -= xs[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def _ema(xs, n):
    """Seeded with the first n-session SMA, then the standard 2/(n+1) recursion --
    the same definition every charting package uses, so a value here matches the one on
    the screen the author was looking at."""
    out, k, val = [None] * len(xs), 2.0 / (n + 1), None
    for i, x in enumerate(xs):
        if i == n - 1:
            val = sum(xs[:n]) / n
        elif i >= n:
            val = x * k + val * (1 - k)
        out[i] = val
    return out


def _rsi(xs, n):
    """Wilder's RSI: first average gain/loss is a plain mean over n changes, then the
    (n-1)/n smoothing. Returns None until there are n changes to average."""
    out = [None] * len(xs)
    if len(xs) <= n:
        return out
    gains = [max(xs[i] - xs[i - 1], 0.0) for i in range(1, len(xs))]
    losses = [max(xs[i - 1] - xs[i], 0.0) for i in range(1, len(xs))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    out[n] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    for i in range(n + 1, len(xs)):
        ag = (ag * (n - 1) + gains[i - 1]) / n
        al = (al * (n - 1) + losses[i - 1]) / n
        out[i] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    return out


def _pct(a, b):
    return None if a is None or b is None or not b else (a / b - 1.0) * 100.0


def _indicator_series(spec, closes):
    kind, n, m = spec["kind"], spec["n"], spec["m"]
    if kind == "rsi":
        return _rsi(closes, n)
    if kind == "close_vs_sma":
        return [_pct(c, s) for c, s in zip(closes, _sma(closes, n))]
    if kind == "close_vs_ema":
        return [_pct(c, e) for c, e in zip(closes, _ema(closes, n))]
    if kind == "ema_cross":
        return [_pct(f, s) for f, s in zip(_ema(closes, n), _ema(closes, m))]
    if kind == "sma_cross":
        return [_pct(f, s) for f, s in zip(_sma(closes, n), _sma(closes, m))]
    raise ValueError(f"unknown indicator kind {kind!r}")
