"""Exit resolution in SQL.

WHY THIS EXISTS. The first implementation pulled every minute of every leg into Python and
walked them looking for a stop. Profiling a single one-year backtest:

    2.43 s total
    1.36 s in _path
    0.65 s in clickhouse_connect's datetime column reader
    148,494 calls to datetime.fromtimestamp, 0.62 s
    0.53 s inside pytz.tzinfo.fromutc

More than a quarter of the run was spent turning timestamps into timezone-aware Python
objects that were then only ever compared to each other. And it scales with the position's
lifetime x legs x cycles, so the paid tier — 400+ cycles over seven years — would have moved
millions of rows through that path.

This module asks ClickHouse the question instead: "for each cycle, what is the FIRST minute
at which the stop or target condition held?" That returns one row per cycle rather than one
per minute per leg. The 148,494-row transfer becomes ~53 rows.

The legs and per-cycle thresholds are passed in as inline arrays rather than a temporary
table, so the whole thing is one round trip with no server-side state to clean up.

Sentinels instead of NULLs: a missing stop is -1e18 and a missing target is +1e18, so the
comparison is always well-defined and the SQL has no three-valued logic in it. A rule that
cannot fire is simply a threshold that cannot be crossed.

NO EPOCHS. Timestamps cross this boundary as 'YYYY-MM-DD HH:MM:SS' strings compared against
a DateTime('Asia/Kolkata') column, never as Unix seconds. The first version used epochs and
was wrong by exactly 5 hours 30 minutes, because datetime.combine().timestamp() reads a
naive IST wall-clock time as UTC. Every exit still landed on the right minute, so the
arithmetic looked fine -- but the entry cutoff was shifted forward half a day, which
silently swallowed any stop that fired within 5.5 hours of entry. Comparing wall-clock
strings inside the column's own timezone removes the class of bug rather than the instance.
"""
from . import db, spec as spec_mod

NO_STOP = -1e18
NO_TARGET = 1e18


def levels_for(spec, entry_credit, width_pts):
    """Stop and target as absolute exit_value levels, from the same rules as
    backtest._exit_trigger. Expressing them as levels is what lets the comparison happen
    in SQL: the rule becomes a number, and the number is per cycle."""
    p = spec.params
    s = spec.structure
    sl, tp = NO_STOP, NO_TARGET
    if s == "short_strangle":
        if p.get("sl_mult") is not None:
            sl = -entry_credit * (1 + float(p["sl_mult"]))
    elif s == "credit_spread":
        if p.get("sl_mult") is not None:
            sl = -entry_credit * (1 + float(p["sl_mult"]))
        if p.get("tp_pct") is not None:
            tp = -entry_credit * (1 - float(p["tp_pct"]))
    elif s == "long_option":
        debit = -entry_credit
        if p.get("sl_pct") is not None:
            sl = debit * (1 - float(p["sl_pct"]))
        if p.get("tp_pct") is not None:
            tp = debit * (1 + float(p["tp_pct"]))
    # iron_condor and iron_fly hold to expiry, matching structures.py -- no level fires.
    return sl, tp


def _leg_rows(cycles):
    out = []
    for i, c in enumerate(cycles):
        for leg in c["legs"]:
            sign = 1 if leg["action"] == "SELL" else -1
            out.append(f"({i},'{c['expiry']}','{leg['option_type']}',"
                       f"{leg['strike']},{sign})")
    return ",".join(out)


# A cycle with no timed exit is given a bound it can never reach, for the same reason a
# missing stop is a sentinel level rather than a NULL: the comparison stays two-valued and
# the SQL has one code path instead of two.
NO_HARD_EXIT = "2099-01-01 00:00:00"


def _cycle_rows(cycles):
    out = []
    for i, c in enumerate(cycles):
        hard = c.get("hard_exit") or NO_HARD_EXIT
        out.append(f"({i},'{c['expiry']}','{c['entry_ts']}',{len(c['legs'])},"
                   f"{c['sl']:.6f},{c['tp']:.6f},'{hard}')")
    return ",".join(out)


def resolve(cycles, database="stratify", max_dte=None):
    """cycles: [{expiry, entry_ts ('YYYY-MM-DD HH:MM:SS' IST), legs, sl, tp,
       hard_exit (optional 'YYYY-MM-DD HH:MM:SS' IST)}] ->
       {index: (hit_datetime_str, exit_value)} for the cycles whose rule fired.

    Cycles whose rule never fires simply do not appear; the caller settles those at expiry.
    """
    if not cycles:
        return {}
    # PRUNING PREDICATES. Joining the small legs list against options_1min is not enough
    # on its own: ClickHouse builds its hash from the right-hand table, so the join alone
    # made this read all 76.89 M rows and hold 4.5 GB. Restating the leg identities as
    # literal IN lists lets the primary key (expiry_date, strike_price, option_type,
    # timestamp) prune, and the explicit time window lets partition pruning work too.
    # Same answer, a fraction of the work -- the join stays for correctness, the
    # predicates are there for the index.
    # A TUPLE in-list, not three separate ones. Separate lists describe the cross product
    # -- every listed strike against every listed expiry -- which for 53 cycles is ~50
    # strikes x 53 expiries of candidates when only ~106 combinations actually exist.
    # The tuple form matches the primary key's own ordering and prunes to the real set.
    keys = sorted({(str(c["expiry"]), l["strike"], l["option_type"])
                   for c in cycles for l in c["legs"]})
    in_keys = ",".join(f"('{e}',{k},'{t}')" for e, k, t in keys)
    lo = min(c["entry_ts"] for c in cycles)
    # Upper bound for PRUNING, distinct from the per-cycle hard_exit above, which is
    # applied after the join. When every position is squared off on its entry day the
    # contract's remaining life is never read at all, which is most of an intraday run's
    # potential cost -- a 0-DTE contract's afternoon is dead weight if the position closed
    # at 14:00.
    hards = [c.get("hard_exit") for c in cycles]
    if all(hards):
        hi_ts = max(hards)
    else:
        hi_ts = max(str(c["expiry"]) for c in cycles) + " 16:00:00"
    # The global time window spans the whole backtest, so on its own it reads every
    # contract from the first entry to the last expiry rather than from its OWN entry.
    # A position is only ever open inside the last `max_dte` days of its contract's life,
    # and a minmax skip index on dte turns that into real granule elimination.
    dte_clause = (f"AND o.dte <= {int(max_dte)}" if max_dte is not None else "")
    sql = f"""
    WITH legs AS (
      SELECT tupleElement(t,1) AS cid, toDate(tupleElement(t,2)) AS expiry,
             tupleElement(t,3) AS ot, toUInt32(tupleElement(t,4)) AS strike,
             toInt8(tupleElement(t,5)) AS sign
      FROM (SELECT arrayJoin([{_leg_rows(cycles)}]) AS t)),
    cyc AS (
      SELECT tupleElement(t,1) AS cid, toDate(tupleElement(t,2)) AS expiry,
             toDateTime(tupleElement(t,3), 'Asia/Kolkata') AS entry_ts,
             toUInt8(tupleElement(t,4)) AS n_legs,
             toFloat64(tupleElement(t,5)) AS sl, toFloat64(tupleElement(t,6)) AS tp,
             toDateTime(tupleElement(t,7), 'Asia/Kolkata') AS hard_exit
      FROM (SELECT arrayJoin([{_cycle_rows(cycles)}]) AS t)),
    marks AS (
      SELECT l.cid AS cid, o.timestamp AS ts,
             -sum(l.sign * o.close) AS exit_value, count() AS present
      FROM legs AS l
      INNER JOIN {database}.options_1min AS o
              ON o.expiry_date = l.expiry AND o.strike_price = l.strike
             AND o.option_type = l.ot
      WHERE (o.expiry_date, o.strike_price, o.option_type) IN ({in_keys})
        AND o.timestamp >= toDateTime('{lo}', 'Asia/Kolkata')
        AND o.timestamp <= toDateTime('{hi_ts}', 'Asia/Kolkata')
        AND o.timestamp <= toDateTime(l.expiry, 'Asia/Kolkata') + INTERVAL 16 HOUR
        {dte_clause}
      GROUP BY cid, ts)
    -- %i is minutes in ClickHouse's formatDateTime; %M is the month NAME.
    SELECT m.cid AS cid,
           formatDateTime(m.ts, '%Y-%m-%d %H:%i:%S') AS hit_ts,
           m.exit_value AS exit_value
    FROM marks AS m
    INNER JOIN cyc AS c ON c.cid = m.cid
    WHERE m.present = c.n_legs
      AND m.ts > c.entry_ts
      -- A stop that would have fired after the position was already squared off did not
      -- fire. Without this bound a timed exit would still inherit stops from the rest of
      -- the contract's life, which is the same lookahead the entry cutoff prevents.
      AND m.ts <= c.hard_exit
      -- the expiry-day close is a settlement, not a tradeable stop
      AND NOT (toDate(m.ts) = c.expiry
               AND toHour(m.ts) * 60 + toMinute(m.ts) >= {spec_mod.EOD_MINUTE})
      AND (m.exit_value <= c.sl OR m.exit_value >= c.tp)
    ORDER BY cid, hit_ts
    LIMIT 1 BY cid
    """
    return {r["cid"]: (r["hit_ts"], r["exit_value"]) for r in db.rows(sql)}
