"""Per-trade replay tracks: the spot the position was watched on, and what it was worth.

WHAT THE REPLAY IS. Every trade, played back minute by minute from entry to exit, so a
person can watch the position work rather than read its outcome. Three things move together
-- the index, the position's combined premium, and the account's equity -- and the point of
the screen is that the third is a consequence of the first two.

THE DATA BOUNDARY, WHICH IS THE WHOLE DESIGN CONSTRAINT.

Index candles are not the asset and go out in full. Option prices are the asset and never
go out. A replay of an options position that ships no option prices sounds impossible, so
here is exactly how it is done -- every series below is POSITION-LEVEL, a signed sum over
two to four legs, never a leg:

    combined(t) = sum over legs of (+1 if SOLD else -1) * price(t)

With L legs and T minutes that is T equations in L*T unknowns, which is not invertible for
L >= 2 and no amount of it reconstructs a single contract's price. The engine already
computes exactly this quantity -- it is what stops and targets fire against -- so the replay
is reading a number the backtest was going to compute anyway.

    running P&L(t) = entry_credit - combined(t)

Also position-level, being an affine function of one.

STRIKES ARE NOT PRICES. A leg's strike is a rule output: 1% out of the money from a spot the
report already prints. Publishing "SELL 25600 CE" discloses the strategy, which is the thing
the report exists to explain, and discloses nothing about the option table.

THE PAYOFF CURVE IS DRAWN FROM STRIKES AND THE NET CREDIT, NOTHING ELSE. At expiry,

    payoff(S) = entry_credit - sum over legs of sign * intrinsic(S, K)

so the kinks sit at strikes and the levels are set by one position-level scalar. It needs no
per-leg prices at all, which is why the replay's price meter reads zero.

BUCKETING IS A UX DECISION BEFORE IT IS A SIZE ONE. At one bar a second, a two-day position
is twelve minutes of watching at 1-minute bars and two and a half at 5-minute. The default
is 5. It also happens to make the page five times smaller, which is a nice second-order
effect and not the reason.
"""
import datetime as dt

from . import backtest, db, spec as spec_mod

DEFAULT_BUCKET = 1           # minutes per replay bar -- the client aggregates upward
# A three-session 1-minute hold is about 1,110 bars, so the old cap of 900 quietly
# downsampled every normal trade to 2-minute and then let the UI go on calling it
# 1-minute. The cap exists for a runaway hold, not for the ordinary case, so it has to sit
# well above it -- and when it does bite, the effective resolution is reported rather than
# hidden.
MAX_BARS_PER_TRADE = 3000

# How long a leg's last print may stand in for a missing one, IN MINUTES rather than in
# buckets. Near expiry a worthless option simply stops trading, and carrying its last real
# mark forever draws a flat line at a price nobody would pay -- on one condor here that put
# the replay 43 points away from the outcome the report states. An hour is generous for a
# weekly index option and short enough that the line goes honestly quiet instead of quietly
# wrong. Counting buckets instead of minutes made the tolerance depend on the timeframe:
# the same twelve buckets are an hour at 5-minute bars and twelve minutes at 1-minute.
MAX_CARRY_MINUTES = 60

# The served ClickHouse users cap a single result at 50,000 rows -- an abuse control, not a
# tuning knob, and one this module lives inside rather than raises. The batch size has to be
# derived rather than guessed: a contract held two days contributes 375 * 3 / bucket rows,
# so at 5-minute buckets 170 contracts fit in one query and at 1-minute barely 35 do. A
# fixed 60 looked safe and still overran on the first 1-minute capture.
#
# Batching by CONTRACT rather than by row is what keeps each trade's legs whole. Splitting a
# trade across two queries would carry a stale mark forward over the seam and draw a step in
# the premium line that the market never printed.
ROWS_PER_QUERY = 40_000
Q = 20               # 1 / 0.05, the NIFTY tick: every price is an exact multiple
SESSION_MINUTES = 375


def _sign(leg):
    return 1.0 if leg["action"] == "SELL" else -1.0


def _naive(ts):
    """ClickHouse hands back tz-aware timestamps for toStartOfInterval while the engine's
    own Trade timestamps are naive IST. Comparing the two raises rather than silently
    misordering, which is the good failure -- but it has to be normalised somewhere, and
    the boundary of this module is that somewhere."""
    return ts.replace(tzinfo=None) if ts.tzinfo is not None else ts


def _bucket(ts, minutes):
    """Floor a timestamp onto the bucket grid. Buckets are anchored to the clock, not to
    the entry minute, so two positions open ten minutes apart still share an x-axis."""
    m = (ts.minute // minutes) * minutes
    return ts.replace(minute=m, second=0, microsecond=0)


def spot_track(date_from, date_to, bucket=DEFAULT_BUCKET):
    """OHLC index bars, bucketed, for the WHOLE period -- not only the days a position was
    open.

    The replay's index chart is continuous on purpose. Drawing each trade on its own fresh
    axis threw away the thing a reader most wants while watching a premium seller work:
    where this week sits in the year. A position that looks calm inside its own two days is
    a different position when you can see the index has fallen 2,000 points into it.

    A year at 1-minute is about 92,000 rows, over the served users' 50,000-row result cap,
    so the days are read in batches.
    """
    per_day = max(1, SESSION_MINUTES // max(bucket, 1))
    batch = max(1, ROWS_PER_QUERY // per_day)
    days = _trading_days(date_from, date_to)
    out = {}
    for i in range(0, len(days), batch):
        rows = db.rows(
            f"""
            SELECT toStartOfInterval(timestamp, INTERVAL %(b)s minute) AS t,
                   argMin(open, timestamp) AS o, max(high) AS h,
                   min(low) AS l, argMax(close, timestamp) AS c
            FROM {db.DATABASE}.spot_1min
            WHERE toDate(timestamp) IN %(days)s
            GROUP BY t ORDER BY t
            """,
            {"days": days[i:i + batch], "b": bucket})
        for r in rows:
            out[_naive(r["t"])] = (float(r["o"]), float(r["h"]),
                                   float(r["l"]), float(r["c"]))
    return out


def _trading_days(date_from, date_to):
    rows = db.rows(
        f"""SELECT DISTINCT toDate(timestamp) AS d FROM {db.DATABASE}.spot_1min
            WHERE timestamp >= %(a)s AND timestamp < addDays(toDate(%(b)s), 1)
            ORDER BY d""",
        {"a": f"{date_from} 00:00:00", "b": str(date_to)})
    return [r["d"] for r in rows]


def premium_track(trades, bucket=DEFAULT_BUCKET):
    """{trade index: {bucket_ts: combined_premium_points}}.

    One query for every leg of every trade, then summed per position IN PYTHON -- which is
    where the boundary is enforced. The per-leg closes exist for the length of this
    function and are discarded; only the sum is returned, and only the sum is ever
    serialised. Keeping the aggregation here rather than in SQL is deliberate: it is easier
    to audit one return statement than a query someone may later 'optimise' into returning
    rows.
    """
    if not trades:
        return {}
    keys = sorted({(t.expiry, l["option_type"], l["strike"])
                   for t in trades for l in t.legs})
    max_dte = max((t.expiry - t.entry_ts.date()).days for t in trades)

    per_contract = max(1, SESSION_MINUTES * (max_dte + 1) // max(bucket, 1))
    batch = max(1, ROWS_PER_QUERY // per_contract)

    by_leg = {}
    for i in range(0, len(keys), batch):
        rows = db.rows(
            f"""
            SELECT expiry_date, option_type, strike_price,
                   toStartOfInterval(timestamp, INTERVAL %(b)s minute) AS t,
                   argMax(close, timestamp) AS c
            FROM {db.DATABASE}.options_1min
            WHERE (expiry_date, option_type, strike_price) IN %(keys)s
              AND dateDiff('day', toDate(timestamp), expiry_date) <= %(mdte)s
            GROUP BY expiry_date, option_type, strike_price, t
            ORDER BY t
            """,
            {"keys": keys[i:i + batch], "b": bucket, "mdte": max_dte})
        for r in rows:
            by_leg.setdefault(
                (r["expiry_date"], r["option_type"], int(r["strike_price"])), {}
            )[_naive(r["t"])] = float(r["c"])

    out = {}
    for i, t in enumerate(trades):
        out[i] = _sum_legs(t.legs, by_leg, t, bucket)
    return out


def _sum_legs(chosen, by_leg, t, bucket):
    """The signed sum, and the ONLY place per-leg closes are touched.

    They live in `by_leg` for the length of this call and are never returned. Everything
    downstream sees one number per bucket.
    """
    legs = [(by_leg.get((t.expiry, l["option_type"], l["strike"]), {}), _sign(l))
            for l in chosen]
    max_carry = max(1, MAX_CARRY_MINUTES // max(bucket, 1))
    lo, hi = _bucket(t.entry_ts, bucket), _bucket(t.exit_ts, bucket)
    stamps = sorted({ts for series, _ in legs for ts in series
                     if lo <= ts <= hi})
    series = {}
    last, staleness = {}, {}
    for ts in stamps:
        # A leg with no print in this bucket holds its previous mark for up to
        # an hour. Skipping immediately would make the combined line jump
        # every time one leg is briefly illiquid, which reads as a move in the position
        # that never happened; carrying indefinitely is the opposite lie.
        total, complete = 0.0, True
        for k, (prices, sign) in enumerate(legs):
            p = prices.get(ts)
            if p is None:
                staleness[k] = staleness.get(k, 0) + 1
                p = last.get(k) if staleness[k] <= max_carry else None
            else:
                staleness[k] = 0
                last[k] = p
            if p is None:
                complete = False
                break
            total += sign * p
        if complete:
            series[ts] = round(total, 2)
    return series


def _encode(bars):
    """Columnar, integer, delta-coded. The same bars in roughly a quarter of the bytes.

    A year of 1-minute condor replay is 36,000 bars. As a list of objects with seven named
    keys that is 3.3 MB of JSON, most of it the key names repeated 36,000 times and price
    decimals that cannot exist -- NIFTY ticks in 0.05, so every price is an exact multiple
    of a twentieth. Storing prices as integer twentieths, open/high/low as offsets from
    their own bar's close, and the timeline as minute gaps, carries the identical data with
    no loss at all. The decoder is fifteen lines in the browser.

    Shipping 1-minute rather than 5 is what makes the timeframe selector possible: one
    source series, every coarser view aggregated client-side, nothing re-fetched.
    """
    if not bars:
        return {"t0": None, "n": 0, "q": Q, "dt": [], "c": [], "o": [], "h": [], "l": []}
    out = {"t0": _minutes(bars[0]["t"]) * 60, "n": len(bars), "q": Q}
    dt, c, o, h, l, p, x = [], [], [], [], [], [], []
    prev_min = _minutes(bars[0]["t"])
    prev_c = None
    for b in bars:
        m = _minutes(b["t"])
        dt.append(m - prev_min)
        prev_min = m
        ci = int(round(b["c"] * Q))
        c.append(ci if prev_c is None else ci - prev_c)
        prev_c = ci
        o.append(int(round(b["o"] * Q)) - ci)
        h.append(int(round(b["h"] * Q)) - ci)
        l.append(int(round(b["l"] * Q)) - ci)
        p.append(None if b["p"] is None else int(round(b["p"] * Q)))
        x.append(b.get("x"))
    out.update(dt=dt, c=c, o=o, h=h, l=l)
    # A COLUMN OF NOTHING IS STILL A COLUMN. The continuous index series carries no
    # position data -- premium lives on the trades that index into it -- so `p` came out as
    # 92,000 literal nulls, half a megabyte of payload saying nothing, on a series where
    # the field has no meaning in the first place. Emit a column only when it holds
    # something.
    if any(v is not None for v in p):
        out["p"] = p
    marks = {str(i): v for i, v in enumerate(x) if v}
    if marks:
        out["x"] = marks
    return out


def _minutes(stamp):
    """Wall-clock string -> whole minutes since the epoch, the IST reading taken as UTC.

    A real calendar conversion, not an approximation. The first version packed the fields
    arithmetically -- ((year*12 + month)*31 + day)*24*60 -- which is exact within a month
    and silently wrong across a short one: 28 February to 1 March came out as a four-day
    gap. The client reconstructs timestamps by accumulating these, so that error would have
    dragged every bar after a month boundary out of position on the axis.
    """
    return int(dt.datetime(
    int(stamp[0:4]), int(stamp[5:7]), int(stamp[8:10]),
    int(stamp[11:13]), int(stamp[14:16]),
    tzinfo=dt.timezone.utc).timestamp()) // 60


# A year of 1-minute index bars is ~92,000 rows and about 680 KB over the wire gzipped,
# which is a fair price for a page you then work in for minutes. Seven years at the same
# resolution is not: it is a five-megabyte download to look at a chart nobody will zoom
# into that far. So the base resolution is DERIVED from the span rather than fixed, and the
# page is told what it actually got instead of being left to claim 1-minute.
MAX_INDEX_BARS = 120_000


# The ladder is 1, 5, 15 and not "keep doubling" because the base has to DIVIDE the
# timeframes the selector offers. A 25-minute base aggregated into 30-minute candles gives
# buckets holding one bar and buckets holding two -- uneven candles presented as even ones.
BASE_LADDER = (1, 5, 15)


def base_bucket(days, bucket=DEFAULT_BUCKET):
    for b in BASE_LADDER:
        if b < bucket:
            continue
        if days * SESSION_MINUTES / b <= MAX_INDEX_BARS:
            return b
    return BASE_LADDER[-1]


def build(result, bucket=DEFAULT_BUCKET):
    """-> {spot, expiries, trades}. Position-level only. Releases no option price.

    ONE CONTINUOUS INDEX SERIES, and per-trade overlays that index into it. The earlier
    shape gave every trade its own bars, which made the chart restart on every entry --
    each position drawn on a fresh two-day axis with no idea what the year around it was
    doing. Here the index is one series for the whole period and a trade is a span within
    it: `i0`..`i1`, with the position's combined premium aligned bar for bar. That costs
    less, too, since the overlapping index bars are no longer stored once per trade.
    """
    trades = sorted(result.trades, key=lambda t: (t.entry_ts, str(t.expiry)))
    if not trades:
        return {"spot": _encode([]), "expiries": [], "trades": []}
    if any(len(t.legs) < MIN_LEGS for t in trades):
        raise NotReleasable(
            "this strategy holds single-leg positions, whose combined premium is just "
            "that option's price. It cannot be replayed.")

    lo_day = min(t.entry_ts.date() for t in trades)
    hi_day = max(t.exit_ts.date() for t in trades)
    bucket = base_bucket((hi_day - lo_day).days * 250 // 365 + 1, bucket)
    spot = spot_track(lo_day, hi_day, bucket)
    stamps = sorted(spot)
    index_of = {ts: i for i, ts in enumerate(stamps)}
    prem = premium_track(trades, bucket)

    bars = [{"t": ts.strftime("%Y-%m-%d %H:%M"), "o": spot[ts][0], "h": spot[ts][1],
             "l": spot[ts][2], "c": spot[ts][3], "p": None}
            for ts in stamps]

    # 15:30 on each expiry day, which in this data is the last bar of that session. Marked
    # because for a weekly premium seller the expiry bell IS the event: everything before
    # it is time decay and everything at it is settlement.
    last_of_day = {}
    for i, ts in enumerate(stamps):
        last_of_day[ts.date()] = i
    expiries = []
    for d in sorted({t.expiry for t in trades}):
        if d in last_of_day:
            expiries.append({"i": last_of_day[d], "date": str(d)})

    out = []
    for i, t in enumerate(trades):
        lo, hi = _bucket(t.entry_ts, bucket), _bucket(t.exit_ts, bucket)
        span = [ts for ts in stamps if lo <= ts <= hi]
        if not span:
            continue
        series = prem.get(i, {})
        i0, i1 = index_of[span[0]], index_of[span[-1]]
        p = [None if series.get(ts) is None else round(series[ts], 2) for ts in span]

        # THE TERMINAL VALUE IS THE ENGINE'S, NOT THE TAPE'S. An expiring position settles
        # against the index -- intrinsic on the mean of its final thirty minutes -- and a
        # cash-settled leg's last option print is not where it ended. Without this the
        # replay finishes on a different number from the table below it, which is the one
        # inconsistency that would make a reader stop believing the page.
        p[-1] = round(-t.exit_value_pts, 2)

        out.append({
            "n": i + 1,
            "entry": t.entry_ts.strftime("%Y-%m-%d %H:%M"),
            "exit": t.exit_ts.strftime("%Y-%m-%d %H:%M"),
            "expiry": str(t.expiry),
            "dte": t.dte,
            "exit_reason": t.exit_reason,
            "spot_entry": round(t.spot_entry, 2),
            # STRIKES AND SIDES, NEVER PRICES. See the module docstring.
            "legs": [{"action": l["action"], "type": l["option_type"],
                      "strike": l["strike"]} for l in t.legs],
            "entry_credit": round(t.entry_credit_pts, 2),
            "net_points": round(t.pnl_pts, 2),
            "margin_points": round(t.margin_pts, 2),
            "lot_size": t.lot_size,
            "charges_rupees": round(t.charges_rupees, 2),
            "pnl_rupees": round(t.pnl_rupees, 2),
            "i0": i0, "i1": i1,
            "p": _quant(p),
        })

    return {"spot": _encode(bars), "expiries": expiries, "trades": out,
            "bucket_minutes": bucket}


def _quant(vals):
    """Points -> integer twentieths, nulls preserved. Same reason as _encode: every price
    in this data is an exact multiple of a tick, so the decimals are noise in the payload."""
    return [None if v is None else int(round(v * Q)) for v in vals]


MIN_LEGS = 2


class NotReleasable(AssertionError):
    """This position cannot be replayed without disclosing an option price."""


SPOT_COLUMNS = {"t0", "n", "q", "dt", "c", "o", "h", "l", "p", "x"}
TRADE_FIELDS = {"n", "entry", "exit", "expiry", "dte", "exit_reason", "spot_entry",
                "legs", "entry_credit", "net_points", "margin_points", "lot_size",
                "charges_rupees", "pnl_rupees", "i0", "i1", "p"}


def release_audit(track):
    """Assert the invariant this module exists to hold. Called by tests and by capture.

    Cheap, and the failure it guards against is the expensive kind: a field named
    `entry_price` quietly added to a leg would be a data leak shipped inside a UI change.
    An ALLOW-LIST rather than a deny-list, so a new field has to be considered rather than
    merely not-yet-forbidden.
    """
    extra = set(track["spot"]) - SPOT_COLUMNS
    assert not extra, f"index series carries unexpected columns: {sorted(extra)}"

    for tr in track["trades"]:
        unknown = set(tr) - TRADE_FIELDS
        assert not unknown, f"trade carries unexpected fields: {sorted(unknown)}"
        # THE SUM IS ONLY SAFE OVER TWO OR MORE LEGS. With one leg, "combined premium" is
        # that contract's price, minute by minute, published in full -- and `long_option`
        # is a structure the engine supports, so this is a live hole rather than a
        # theoretical one. A single-leg strategy gets no replay; it is not a rendering
        # limitation to work around later.
        if len(tr["legs"]) < MIN_LEGS:
            raise NotReleasable(
                f"trade {tr.get('n')} has {len(tr['legs'])} leg(s): a combined premium "
                f"needs two or more legs to be non-invertible. Single-leg structures "
                f"(long_option) cannot be replayed.")
        for leg in tr["legs"]:
            bad = set(leg) - {"action", "type", "strike"}
            assert not bad, f"leg carries non-strike fields: {sorted(bad)}"
    return {"price_points_released": 0,
            "basis": ("index OHLC (not the asset) and position-level sums over >=2 legs "
                      "(not invertible to any leg)")}
