"""The backtest engine: spec in, trades and evidence out.

SHAPE. A cycle is one weekly expiry. On the trading day whose DTE matches `entry_dte`,
the structure is opened at `entry_time` and held until a stop/target fires or the contract
settles at expiry. That is the shape the 39 live strategies actually trade -- an intraday
entry time inside a weekly cycle -- not a same-day round trip.

WHY IT IS FAST WITHOUT contract_day. Strike selection is the only step that has to look
across the chain, and it looks at ONE minute per cycle. Once the legs are chosen, the path
query touches only those contracts: ~58 cycles x 4 legs x a few days x 375 minutes. The
expensive layer is never scanned for the path, so a full-year backtest reads well under a
million rows.

CONVENTIONS, stated once because getting them wrong silently is the failure mode here:

  net_value(t) = sum over legs of (+1 if the leg was SOLD else -1) * price(t)
  entry_credit = net_value(entry)            positive for credit structures, negative for debit
  exit_value   = -net_value(exit)            positive = credit received on closing
  pnl_pts      = entry_credit + exit_value = net_value(entry) - net_value(exit)

This matches paper_trading/engine.py's sign convention exactly, so a stop-loss rule written
for the live system means the same thing here.

ENTRY PRICING. The last real print in the 15-minute bucket ending at `entry_time`, and the
strike must have traded in that bucket (decision B4). No modelled fills.

EXPIRY SETTLEMENT. Intrinsic value against the mean of the underlying over the final 30
minutes -- that is NSE's own final settlement convention, and it is neither the 15:29 close
nor the option's last print.
"""
import datetime as dt
from dataclasses import dataclass, field

import functools

from . import db, exits, signals, spec as spec_mod
from .config import charges as charges_mod, contracts, liquidity, margin as margin_mod, slippage

ENTRY_BUCKET_MINUTES = 15
# Credit below this share of the spread width is reported. See the note where it is used.
NEGLIGIBLE_CREDIT_RATIO = 0.02
SETTLEMENT_WINDOW_MINUTES = 30   # NSE final settlement = last 30 minutes of the underlying


@dataclass
class Trade:
    expiry: dt.date
    entry_date: dt.date
    entry_ts: dt.datetime
    exit_ts: dt.datetime
    exit_reason: str
    spot_entry: float
    legs: list
    entry_credit_pts: float
    exit_value_pts: float
    pnl_pts: float
    slippage_pts: float
    margin_pts: float
    lot_size: int
    charges_rupees: float = 0.0
    pnl_rupees: float = 0.0
    dte: int = 0
    # Whether this trade's margin is exact (a hedged combo's maximum loss) or a
    # today-calibrated SPAN ratio applied historically. Reported, not discarded.
    margin_basis: str = ""
    # {(option_type, strike): price} at the close, when one exists as a set of per-leg
    # prices. Absent for an SL/TP exit, where SQL returns the combined value at the firing
    # minute and never the individual legs.
    exit_prices: dict = None


@dataclass
class BacktestResult:
    spec: object
    trades: list
    warnings: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    n_contracts: int = 0
    n_trading_days: int = 0


# ------------------------------------------------------------------ cycles

def _cycles(spec):
    """The (expiry, entry_date) pairs this strategy opens on.

    WEEKLY -- one per expiry: the trading day closest to entry_dte.
    DAILY  -- one per trading SESSION, on whichever expiry is nearest that day. This is
    what makes an intraday strategy expressible. A weekly cadence can only ever produce
    ~58 entries a year because there are 58 expiries; a daily one produces ~246, which is
    the difference between "one trade a week" and "every day", and it was the limitation
    that made an 11:00-in / 14:00-out strategy unaskable rather than unprofitable.
    """
    if spec.cadence == "daily":
        return _daily_cycles_cached(spec.max_dte, spec.date_from, spec.date_to,
                                    db.DATABASE, db.current_user.get())
    return _cycles_cached(spec.entry_dte, spec.date_from, spec.date_to,
                          db.DATABASE, db.current_user.get())


@functools.lru_cache(maxsize=256)
def _daily_cycles_cached(max_dte, date_from, date_to, database, ch_user):
    """Nearest expiry per trading day. Ties broken by the earlier expiry_date, which is
    only reachable if two contracts share a DTE -- it cannot happen for NIFTY weeklies,
    but an explicit tie-break beats a nondeterministic one.

    expiry_date is bounded by the window for the same reason the weekly query bounds it:
    a cycle whose expiry settles after the served range has no settlement to read. That
    costs at most the final week of the period.
    """
    bound = 45 if max_dte is None else int(max_dte)
    rows = db.rows(
        f"""
        SELECT expiry_date, trade_date, dte FROM (
          SELECT expiry_date, trade_date, dte,
                 row_number() OVER (PARTITION BY trade_date
                                    ORDER BY dte, expiry_date) AS rn
          FROM ( SELECT DISTINCT expiry_date, trade_date, dte FROM {db.DATABASE}.contract_day
                 WHERE trade_date >= %(d0)s AND trade_date <= %(d1)s
                   AND expiry_date <= %(d1)s AND dte <= %(mdte)s ) )
        WHERE rn = 1 ORDER BY trade_date
        """,
        {"d0": date_from, "d1": date_to, "mdte": bound})
    return list(rows)


# EVERY CACHE BELOW TAKES ch_user AND NONE OF THEM READ IT. It is in the key because these
# tables are row-policy-filtered per tier: the same arguments return different rows to
# stratify_free and stratify_paid, so a key that omits the identity lets one tier serve the
# other out of a shared process-local cache.
#
# Process-local memo on top of the ClickHouse cache. The database cache removes the work;
# this removes the round trip as well, which is most of what is left once the work is
# small. Keyed on the values the query actually depends on -- not on the spec, because two
# different strategies with the same entry timing share this answer exactly.
@functools.lru_cache(maxsize=256)
def _cycles_cached(entry_dte, date_from, date_to, database, ch_user):
    class _S:
        pass
    spec = _S()
    spec.entry_dte, spec.date_from, spec.date_to = entry_dte, date_from, date_to
    rows = db.rows(
        f"""
        SELECT expiry_date, trade_date, dte FROM (
          SELECT expiry_date, trade_date, dte,
                 row_number() OVER (PARTITION BY expiry_date
                                    ORDER BY abs(toInt32(dte) - %(dte)s), trade_date) AS rn
          FROM ( SELECT DISTINCT expiry_date, trade_date, dte FROM {db.DATABASE}.contract_day
                 WHERE trade_date >= %(d0)s AND trade_date <= %(d1)s
                   AND expiry_date <= %(d1)s AND dte <= 45 ) )
        WHERE rn = 1 ORDER BY expiry_date
        """,
        {"dte": spec.entry_dte, "d0": spec.date_from, "d1": spec.date_to})
    return [r for r in rows if r["dte"] <= 45]


# ------------------------------------------------------------------ entry snapshot

ENTRY_COLUMN = {"09:15": "ent_0915", "09:30": "ent_0930", "11:00": "ent_1100",
                "12:00": "ent_1200", "12:30": "ent_1230", "13:00": "ent_1300",
                "14:00": "ent_1400", "15:00": "ent_1500", "EOD": "ent_1529"}


def _entry_snapshot(spec, cycles):
    return _chain_snapshot(cycles, ENTRY_COLUMN[spec.entry_time])


def _exit_snapshot(spec, cycles):
    """The same lookup, at exit_time. A timed exit is priced exactly the way an entry is --
    last real print in the 15-minute bucket ending on the clock time -- so a strategy that
    exits at 14:00 is charged the same kind of fill it was given at 11:00. Anything else
    would let a modelled exit flatter a real entry."""
    return _chain_snapshot(cycles, ENTRY_COLUMN[spec.exit_time])


def _chain_snapshot(cycles, column):
    """Last real print per strike in the 15-minute bucket ending at entry_time, for every
    cycle at once. `require_volume` is structural: only bars that actually traded count.

    Served from contract_day, where that bucket is precomputed per standard entry time.
    Computing it from options_1min meant scanning every bar of every expiry in play --
    41.6 M rows, and the dominant cost once exit detection moved into SQL. A 232 K-row
    table answers the identical question; `ent_HHMM = 0` is precisely "did not trade in
    the bucket", so the volume requirement survives the move rather than being dropped.
    """
    if not cycles:
        return {}
    pairs = [(c["expiry_date"], c["trade_date"]) for c in cycles]
    # BOTH keys are selected, and both as integers. The earlier version selected only the
    # expiry, on the reasoning that there is exactly one entry date per expiry -- true of a
    # weekly cadence and false the moment a daily one enters five sessions against the same
    # contract. Reading the trade_date back rather than inferring it is what makes the two
    # cadences share this query. Integers rather than Dates because clickhouse_connect
    # builds a Python date object per Date column, and at thousands of rows that
    # conversion was the largest CPU item left in the request.
    rows = db.rows(
        f"""
        SELECT toYYYYMMDD(expiry_date) AS expiry_key, toYYYYMMDD(trade_date) AS day_key,
               option_type, strike_price, {column} AS px
        FROM {db.DATABASE}.contract_day
        WHERE (expiry_date, trade_date) IN %(pairs)s AND {column} > 0
        """,
        {"pairs": pairs})
    by_key = {}
    for e, d in pairs:
        by_key[(int(e.strftime("%Y%m%d")), int(d.strftime("%Y%m%d")))] = (e, d)
    snap = {}
    for r in rows:
        key = by_key.get((r["expiry_key"], r["day_key"]))
        if key is None:
            continue
        snap.setdefault(key, {}) \
            .setdefault(r["option_type"], {})[int(r["strike_price"])] = float(r["px"])
    return snap


def _spot_at(spec, cycles):
    if not cycles:
        return {}
    return _spot_at_cached(spec.entry_minute,
                           tuple(sorted({c["trade_date"] for c in cycles})),
                           db.DATABASE, db.current_user.get())


@functools.lru_cache(maxsize=256)
def _spot_at_cached(entry_minute, days, database, ch_user):
    rows = db.rows(
        f"""
        SELECT toDate(timestamp) AS d, argMax(close, timestamp) AS spot
        FROM {db.DATABASE}.spot_1min
        WHERE toDate(timestamp) IN %(days)s
          AND (toHour(timestamp) * 60 + toMinute(timestamp)) <= %(m)s
        GROUP BY d
        """,
        {"days": list(days), "m": entry_minute})
    return {r["d"]: float(r["spot"]) for r in rows}


# ------------------------------------------------------------------ leg construction

def _nearest_listed(strikes, target, prefer_higher):
    """Nearest listed strike, ties broken toward `prefer_higher` -- the further-OTM side.
    See config/contracts.select_strike for why the tie-break has to be explicit."""
    if not strikes:
        return None
    best = None
    for k in strikes:
        key = (abs(k - target), -k if prefer_higher else k)
        if best is None or key < best[0]:
            best = (key, k)
    return best[1]


# How far the chosen ATM may sit from spot before the chain is judged unable to carry an
# ATM-relative structure. MEASURED, not picked: across 245 daily cycles the nearest strike
# with a real print in both CE and PE lands exactly on ATM 93.5 % of the time, with a thin
# tail at 1-5 steps and then outliers at 8, 13, 22, 27, 29 and 33 steps -- up to 1,650
# index points from spot. Two steps keeps 96 % of cycles and removes the entire tail.
#
# This matters beyond strike selection: `offset_steps` is measured FROM atm and feeds the
# naked-short SPAN ratio, which is clamped at the ends of its calibration grid. A drifted
# atm therefore reports the SMALLEST calibrated margin for a strangle that is nowhere near
# where the engine thinks it is -- a wrong margin, in the flattering direction.
MAX_ATM_DRIFT_STEPS = 2


def _build_legs(spec, chain, spot, expiry, direction=None, reasons=None):
    """-> list of legs, or None if the chain cannot support this structure that day.

    `reasons` is a counter the caller passes in so a skipped cycle can be DISCLOSED with
    the reason it was skipped. Three different failures used to collapse into one note
    saying the chain lacked a real print, which was true of only one of them."""
    reasons = {} if reasons is None else reasons
    p = spec.params
    direction = direction or p.get("direction")
    ce, pe = chain.get("CE", {}), chain.get("PE", {})
    if not ce or not pe:
        return None
    step = contracts.strike_step(expiry, spec.symbol)
    atm = _nearest_listed(sorted(set(ce) & set(pe)), spot, prefer_higher=True)
    if atm is None:
        return None
    if abs(atm - spot) > MAX_ATM_DRIFT_STEPS * step:
        reasons["atm_drift"] = reasons.get("atm_drift", 0) + 1
        # The minute is too thin to locate ATM. Every structure here is defined relative to
        # it, so building one anyway does not produce a worse fill -- it produces a
        # DIFFERENT strategy wearing this one's name. Observed: an "iron fly" whose ATM sat
        # 380 points below spot, which made it a deep-ITM CE credit spread and returned
        # 100.6 % on margin in a single trade.
        return None

    def leg(otype, strike, action):
        book = ce if otype == "CE" else pe
        if strike is None or strike not in book:
            return None
        otm = (strike - spot) if otype == "CE" else (spot - strike)
        return {"option_type": otype, "action": action, "strike": int(strike),
                "premium_pts": float(book[strike]), "otm_points": otm,
                "offset_steps": abs(strike - atm) / step}

    s = spec.structure
    if s == "short_strangle":
        off = float(p["pct_offset"]) / 100.0
        legs = [leg("CE", _nearest_listed(sorted(ce), spot * (1 + off), True), "SELL"),
                leg("PE", _nearest_listed(sorted(pe), spot * (1 - off), False), "SELL")]
    elif s == "credit_spread":
        off, wid = float(p["pct_offset"]) / 100.0, float(p["pct_width"]) / 100.0
        if direction == "CE":
            short_k = _nearest_listed(sorted(ce), spot * (1 + off), True)
            long_k = _nearest_listed(sorted(ce), spot * (1 + off + wid), True)
            legs = [leg("CE", short_k, "SELL"), leg("CE", long_k, "BUY")]
        else:
            short_k = _nearest_listed(sorted(pe), spot * (1 - off), False)
            long_k = _nearest_listed(sorted(pe), spot * (1 - off - wid), False)
            legs = [leg("PE", short_k, "SELL"), leg("PE", long_k, "BUY")]
        if legs[0] and legs[1] and legs[0]["strike"] == legs[1]["strike"]:
            return None
    elif s == "iron_condor":
        off, wid = float(p["pct_offset"]) / 100.0, float(p["pct_width"]) / 100.0
        legs = [leg("CE", _nearest_listed(sorted(ce), spot * (1 + off), True), "SELL"),
                leg("CE", _nearest_listed(sorted(ce), spot * (1 + off + wid), True), "BUY"),
                leg("PE", _nearest_listed(sorted(pe), spot * (1 - off), False), "SELL"),
                leg("PE", _nearest_listed(sorted(pe), spot * (1 - off - wid), False), "BUY")]
    elif s == "iron_fly":
        wid = float(p["pct_width"]) / 100.0
        legs = [leg("CE", atm, "SELL"),
                leg("CE", _nearest_listed(sorted(ce), spot * (1 + wid), True), "BUY"),
                leg("PE", atm, "SELL"),
                leg("PE", _nearest_listed(sorted(pe), spot * (1 - wid), False), "BUY")]
    elif s == "long_option":
        off = float(p["pct_offset"]) / 100.0
        if direction == "CE":
            legs = [leg("CE", _nearest_listed(sorted(ce), spot * (1 + off), True), "BUY")]
        else:
            legs = [leg("PE", _nearest_listed(sorted(pe), spot * (1 - off), False), "BUY")]
    else:
        raise spec_mod.SpecError(f"structure {s} has no implementation")

    if any(l is None for l in legs):
        return None
    if len({(l["option_type"], l["strike"], l["action"]) for l in legs}) != len(legs):
        return None
    if _has_offsetting_pair(legs):
        reasons["offsetting_legs"] = reasons.get("offsetting_legs", 0) + 1
        return None
    if spec.is_credit and margin_mod.is_defined_risk(legs) \
            and margin_mod.max_loss_points(legs) <= 0:
        # Max loss of zero means the payoff is non-negative at every strike boundary: the
        # position cannot lose. That is an arbitrage, so it is a pricing artefact -- an
        # iron fly one strike wide whose ATM credit exceeds the wing width, from stale or
        # tick-floor prints. Left in, margin is 0, and metrics.py then drops the trade from
        # the return-on-margin mean without telling the reader the average changed shape.
        reasons["cannot_lose"] = reasons.get("cannot_lose", 0) + 1
        return None
    if spec.is_credit and _net_value(
            legs, {(l["option_type"], l["strike"]): l["premium_pts"] for l in legs}) < 0:
        # A credit structure that pays a net debit. Reachable where both legs sit at the
        # tick floor on expiry day and the FURTHER-out leg prints a tick higher than the
        # nearer one -- an inversion that is real in stale data and untradeable in life.
        # Left in, its max loss exceeds the spread width, which breaks the only guarantee a
        # defined-risk structure makes and puts margin above width.
        reasons["inverted_credit"] = reasons.get("inverted_credit", 0) + 1
        return None
    return legs


def _has_offsetting_pair(legs):
    """A SELL and a BUY of the same option type on the SAME strike. The pair cancels: it
    contributes no premium, no risk and no margin, and what is left is a different
    structure from the one that was asked for.

    The dedup above does NOT catch this. Its key includes `action`, so (CE, 25150, SELL)
    and (CE, 25150, BUY) are distinct entries and both survive -- it rejects a repeated
    leg, not a self-cancelling one. Two ways this arises when the entry minute is thin:
    both wing targets round to the same listed strike, or the far wing rounds onto the ATM
    strike itself.

    Observed consequences before this guard: an iron condor whose credit, max loss and
    margin were all exactly zero -- which metrics.py then dropped from mean return-on-
    margin without saying so -- and an iron fly with a flat PE side that reported 100.6 %
    return on margin.
    """
    for side in ("CE", "PE"):
        sells = {l["strike"] for l in legs
                 if l["option_type"] == side and l["action"] == "SELL"}
        buys = {l["strike"] for l in legs
                if l["option_type"] == side and l["action"] == "BUY"}
        if sells & buys:
            return True
    return False


def _sign(leg):
    return 1.0 if leg["action"] == "SELL" else -1.0


def _net_value(legs, prices):
    return sum(_sign(l) * prices[(l["option_type"], l["strike"])] for l in legs)


def _width_pts(legs):
    ce = [l["strike"] for l in legs if l["option_type"] == "CE"]
    pe = [l["strike"] for l in legs if l["option_type"] == "PE"]
    widths = []
    if len(ce) > 1:
        widths.append(max(ce) - min(ce))
    if len(pe) > 1:
        widths.append(max(pe) - min(pe))
    return max(widths) if widths else None


# ------------------------------------------------------------------ path and exit

def _path(cycles_legs, date_to, max_dte):
    """Per-minute close for every selected contract, from entry to expiry.

    Only the chosen legs are read, which is what keeps this cheap -- and only the part of
    their life the position was actually open for. A contract listed two months before
    expiry has most of its bars before any entry; without the DTE bound this query reads
    them all for nothing.
    """
    keys = sorted({(l["expiry"], l["option_type"], l["strike"]) for l in cycles_legs})
    if not keys:
        return {}
    rows = db.rows(
        f"""
        SELECT expiry_date, option_type, strike_price, timestamp, close, volume
        FROM {db.DATABASE}.options_1min
        WHERE (expiry_date, option_type, strike_price) IN %(keys)s
          AND toDate(timestamp) <= %(d1)s
          AND dateDiff('day', toDate(timestamp), expiry_date) <= %(mdte)s
        ORDER BY timestamp
        """,
        {"keys": keys, "d1": date_to, "mdte": max_dte})
    out = {}
    for r in rows:
        out.setdefault((r["expiry_date"], r["option_type"], int(r["strike_price"])), {})[
            r["timestamp"]] = (float(r["close"]), int(r["volume"]))
    return out


def _settlement_spot(expiries):
    """NSE final settlement price: mean of the underlying over the last 30 minutes."""
    if not expiries:
        return {}
    return _settlement_cached(tuple(sorted(expiries)), db.DATABASE, db.current_user.get())


@functools.lru_cache(maxsize=256)
def _settlement_cached(expiries, database, ch_user):
    rows = db.rows(
        f"""
        SELECT toDate(timestamp) AS d, avg(close) AS settle
        FROM {db.DATABASE}.spot_1min
        WHERE toDate(timestamp) IN %(days)s
          AND (toHour(timestamp) * 60 + toMinute(timestamp))
              > %(m)s - %(w)s
        GROUP BY d
        """,
        {"days": list(expiries), "m": spec_mod.EOD_MINUTE, "w": SETTLEMENT_WINDOW_MINUTES})
    return {r["d"]: float(r["settle"]) for r in rows}


def _intrinsic(legs, settle):
    prices = {}
    for l in legs:
        k = l["strike"]
        prices[(l["option_type"], k)] = (max(0.0, settle - k) if l["option_type"] == "CE"
                                         else max(0.0, k - settle))
    return prices


def _settles_at(spec, cycle):
    """True when the timed exit lands on or after the settlement bell of the contract's own
    expiry day. 'EOD' on a 0-DTE position is not a 15:29 print -- the contract settles, and
    NSE settles it against the mean of the underlying over the final 30 minutes. Pricing it
    off the option's last tick instead would quietly change what the strategy is."""
    if spec.exit_time is None:
        return False
    return (cycle["trade_date"] == cycle["expiry_date"]
            and spec.exit_minute >= spec_mod.EOD_MINUTE)


def _exit_trigger(spec, entry_credit, width_pts, exit_value):
    """Direct port of paper_trading/engine.check_exit_trigger, same sign convention."""
    p = spec.params
    credit = entry_credit
    s = spec.structure
    if s == "short_strangle":
        sl = p.get("sl_mult")
        if sl is not None and exit_value <= -credit * (1 + float(sl)):
            return "SL"
    elif s == "credit_spread":
        sl, tp = p.get("sl_mult"), p.get("tp_pct")
        if sl is not None and exit_value <= -credit * (1 + float(sl)):
            return "SL"
        if tp is not None and exit_value >= -credit * (1 - float(tp)):
            return "TP"
    elif s == "long_option":
        debit = -credit
        sl, tp = p.get("sl_pct"), p.get("tp_pct")
        if sl is not None and exit_value <= debit * (1 - float(sl)):
            return "SL"
        if tp is not None and exit_value >= debit * (1 + float(tp)):
            return "TP"
    # iron_condor / iron_fly hold to expiry, matching structures.py
    return None


# ------------------------------------------------------------------ run

def run(raw_spec, lots=1):
    spec = raw_spec if isinstance(raw_spec, spec_mod.StrategySpec) else spec_mod.parse(raw_spec)
    cycles = _cycles(spec)
    snapshots = _entry_snapshot(spec, cycles)
    spots = _spot_at(spec, cycles)

    opened, warnings, notes = [], [], []
    skipped_no_chain = skipped_gate = skipped_bias = skipped_no_holding = 0
    skip_reasons = {}
    for c in cycles:
        key = (c["expiry_date"], c["trade_date"])
        chain, spot = snapshots.get(key), spots.get(c["trade_date"])
        if not chain or spot is None:
            skipped_no_chain += 1
            continue
        if (c["trade_date"] == c["expiry_date"]
                and spec.entry_minute >= spec_mod.EOD_MINUTE):
            # Entering at 15:29 on the contract's OWN expiry day. It settles at 15:29 too,
            # so the position has no holding period at all: it opens and settles in the
            # same instant, and its "P&L" is the gap between the last print and the
            # settlement price. That is a measurement of the settlement convention, not a
            # trade anyone could take, and it is free money in whichever direction the gap
            # happens to fall.
            skipped_no_holding += 1
            continue
        allowed, bias = signals.evaluate(spec.gate, spec.bias, c["trade_date"],
                                         spec.entry_minute)
        if not allowed:
            skipped_gate += 1
            continue
        direction = spec.params.get("direction")
        if spec.bias != "neutral":
            if bias == "neutral":
                skipped_bias += 1
                continue
            direction = "CE" if bias == "bullish" else "PE"
        legs = _build_legs(spec, chain, spot, c["expiry_date"], direction, skip_reasons)
        if legs is None:
            skipped_no_chain += 1
            continue
        for l in legs:
            l["expiry"] = c["expiry_date"]
        opened.append({"cycle": c, "spot": spot, "legs": legs})

    drifted = skip_reasons.get("atm_drift", 0)
    offsetting = skip_reasons.get("offsetting_legs", 0)
    thin = (skipped_no_chain - drifted - offsetting
            - skip_reasons.get("inverted_credit", 0)
            - skip_reasons.get("cannot_lose", 0))
    if thin > 0:
        notes.append(f"{thin} of {len(cycles)} cycles skipped: the chain at "
                     f"{spec.entry_time} did not carry every leg with a real print")
    if drifted:
        notes.append(
            f"{drifted} of {len(cycles)} cycles skipped: at {spec.entry_time} the nearest "
            f"strike trading in both CE and PE was more than "
            f"{MAX_ATM_DRIFT_STEPS} strikes from spot, so ATM could not be located and "
            f"every strike in this structure is defined relative to it")
    if skipped_no_holding:
        notes.append(
            f"{skipped_no_holding} of {len(cycles)} cycles skipped: entry_time "
            f"{spec.entry_time} falls on the contract's own expiry day at or after the "
            f"15:29 settlement, so the position would open and settle in the same minute "
            f"with no holding period")
    inverted = skip_reasons.get("inverted_credit", 0)
    if inverted:
        notes.append(
            f"{inverted} of {len(cycles)} cycles skipped: the further-out leg priced above "
            f"the nearer one, so this credit structure would have paid a net debit. That "
            f"inversion appears where both legs sit at the exchange tick floor and is not "
            f"tradeable")
    riskless = skip_reasons.get("cannot_lose", 0)
    if riskless:
        notes.append(
            f"{riskless} of {len(cycles)} cycles skipped: the structure priced with a "
            f"maximum loss of zero, meaning it could not lose at any strike. That is an "
            f"arbitrage rather than a strategy, and it comes from tick-floor or stale "
            f"prints rather than from a tradeable edge")
    if offsetting:
        notes.append(
            f"{offsetting} of {len(cycles)} cycles skipped: two legs rounded onto the same "
            f"strike on the same side, which cancels the pair and leaves a different "
            f"structure from the one specified")
    if skipped_gate:
        notes.append(f"{skipped_gate} of {len(cycles)} cycles skipped by gate "
                     f"{spec.gate!r}")
    if skipped_bias:
        notes.append(f"{skipped_bias} of {len(cycles)} cycles skipped: bias "
                     f"{spec.bias!r} read neutral, so there was no side to take")

    all_legs = [l for o in opened for l in o["legs"]]
    settle = _settlement_spot({o["cycle"]["expiry_date"] for o in opened})

    # Exit detection happens in SQL (see exits.py). Walking the path in Python spent more
    # than a quarter of every backtest deserialising timestamps it only ever compared,
    # and the cost scaled with cycles x legs x holding period -- which is exactly the
    # dimension the paid tier multiplies.
    for o in opened:
        entry_prices = {(l["option_type"], l["strike"]): l["premium_pts"] for l in o["legs"]}
        o["entry_credit"] = _net_value(o["legs"], entry_prices)
        o["width"] = _width_pts(o["legs"])
        o["sl"], o["tp"] = exits.levels_for(spec, o["entry_credit"], o["width"])
        # IST wall-clock string, compared against a DateTime('Asia/Kolkata') column.
        # Never an epoch -- see exits.py.
        o["entry_at"] = dt.datetime.combine(
            o["cycle"]["trade_date"],
            dt.time(spec.entry_minute // 60, spec.entry_minute % 60))
        o["exit_at"] = None
        if spec.exit_time is not None:
            o["exit_at"] = dt.datetime.combine(
                o["cycle"]["trade_date"],
                dt.time(spec.exit_minute // 60, spec.exit_minute % 60))
    fired = exits.resolve(
        [{"expiry": o["cycle"]["expiry_date"],
          "entry_ts": o["entry_at"].strftime("%Y-%m-%d %H:%M:%S"),
          "legs": o["legs"], "sl": o["sl"], "tp": o["tp"],
          "hard_exit": (o["exit_at"].strftime("%Y-%m-%d %H:%M:%S")
                        if o["exit_at"] else None)} for o in opened],
        database=db.DATABASE,
        max_dte=max((o["cycle"]["dte"] for o in opened), default=None))

    # A timed exit needs a price at the exit clock time, and only for the cycles that
    # survived that far -- one that stopped out at 12:10 is already closed. The lookup is
    # the same 232 K-row table the entry came from, so this adds one query, not a scan.
    exit_snap = {}
    if spec.exit_time is not None:
        survivors = [o["cycle"] for i, o in enumerate(opened) if i not in fired]
        exit_snap = _exit_snapshot(spec, survivors) if survivors else {}

    trades = []
    thin_seen = set()
    no_exit_print = 0
    for i, o in enumerate(opened):
        t = _settle_cycle(spec, o, fired.get(i), settle, lots, thin_seen, exit_snap)
        if t is None:
            if spec.exit_time is not None and i not in fired:
                no_exit_print += 1
            continue
        trades.append(t)
    if no_exit_print:
        notes.append(
            f"{no_exit_print} of {len(opened)} positions dropped: not every leg had a real "
            f"print in the bucket ending {spec.exit_time}, so there was no honest price to "
            f"close at. They are removed rather than closed at a modelled price")

    # NEGLIGIBLE CREDIT. Found by the invariant sweep, not by anyone looking: a
    # 0.25 %-wide credit spread on expiry day routinely enters for almost nothing, because
    # both legs print at the exchange tick floor and the difference between them is zero.
    # The position is real and the data is real -- a trader following the rule would have
    # got that fill -- but it blocks the full width of margin to collect ~nothing, and no
    # existing warning fires because both legs DID trade.
    #
    # Threshold measured rather than guessed: at 2 % of width a normal 1 %-wide condor has
    # 2 of 50 trades below it while a 0.25 %-wide one has 116 of 229. Below 2 % the trade
    # risks 49 units to make 1.
    #
    # Disclosure, not rejection -- the engine's one hard gate is volume at entry, and
    # everything else is reported so the reader can judge it.
    negligible = 0
    for t in trades:
        width = _width_pts(t.legs)
        if width and spec.is_credit and t.entry_credit_pts < NEGLIGIBLE_CREDIT_RATIO * width:
            negligible += 1
    if negligible:
        notes.append(
            f"{negligible} of {len(trades)} trades entered for less than "
            f"{NEGLIGIBLE_CREDIT_RATIO:.0%} of the spread width — the full width of margin "
            f"blocked to collect almost nothing. Both legs traded, so this is real, but it "
            f"is usually a sign that pct_width is too narrow for the days to expiry")

    for note in sorted(thin_seen):
        warnings.append(note)
    # The margin basis is a WARNING when it is an approximation, not a footnote. A naked
    # short's SPAN ratio was calibrated today and applied to the past; it widens in a shock,
    # which is exactly when the position is losing, so return-on-margin is optimistic by an
    # unknown amount. That belongs next to the number, not in a document nobody opened.
    for basis in sorted({t.margin_basis for t in trades if t.margin_basis}):
        if "calibrated" in basis:
            warnings.append(basis)
    warnings.extend(charges_mod.DISCLOSED_GAPS)
    warnings.append(slippage.ASSUMPTION_NOTE)

    n_contracts = len({(l["expiry"], l["option_type"], l["strike"]) for l in all_legs})
    n_days = len({t.entry_date for t in trades})

    # Anti-oracle floors (decision D1) are enforced on what the query SCANNED, not what the
    # strategy ended up TRADING (DECISIONS.md open action #8, resolved by Srinath
    # 2026-08-25: measure scanned). A selective gate/bias reads neutral most weeks and
    # trades on very few days -- that is a legitimate strategy, and gating its floors on
    # n_contracts/n_days above produces a false positive on exactly that shape. What
    # actually leaks the underlying data one price at a time is the DB round trip itself:
    # `snapshots` already holds the full chain returned for EVERY candidate cycle, whether
    # or not the gate/bias went on to skip it, so the scanned set is read from there rather
    # than from `opened`/`trades`. n_contracts/n_days above stay trade-based -- they are the
    # honesty-panel numbers shown to the user ("your strategy touched N contracts"), which
    # should describe what it did, not what the engine looked at along the way.
    n_contracts_scanned = len({(expiry, otype, strike)
                               for (expiry, _trade_date), by_type in snapshots.items()
                               for otype, strikes in by_type.items()
                               for strike in strikes})
    n_days_scanned = len({c["trade_date"] for c in cycles})
    spec_mod.check_coverage(n_contracts_scanned, n_days_scanned)

    return BacktestResult(spec=spec, trades=trades, warnings=warnings, notes=notes,
                          n_contracts=n_contracts, n_trading_days=n_days)


def _settle_cycle(spec, opened, fired, settle, lots, thin_seen, exit_snap=None):
    """Turn one cycle plus its (possibly absent) exit into a Trade. All the per-minute work
    already happened in SQL; what is left is arithmetic on a handful of numbers.

    Three ways a position can end, in priority order:
      SL/TP    a rule fired at a real print, before any timed exit (bounded in SQL)
      TIME     squared off at exit_time on the entry day, at that bucket's last real print
      EXPIRY   settled against NSE's final settlement price
    """
    c, legs, spot = opened["cycle"], opened["legs"], opened["spot"]
    expiry = c["expiry_date"]
    entry_credit = opened["entry_credit"]
    exit_prices = None

    for l in legs:
        thin, note = liquidity.assess(c["dte"], l["otm_points"])
        if thin:
            thin_seen.add(note)

    if fired is not None:
        hit_at, exit_value = fired
        exit_ts = dt.datetime.strptime(hit_at, "%Y-%m-%d %H:%M:%S")
        exit_reason = "SL" if exit_value <= opened["sl"] else "TP"
    elif opened["exit_at"] is not None and not _settles_at(spec, c):
        chain = (exit_snap or {}).get((expiry, c["trade_date"]))
        if not chain:
            return None
        try:
            exit_prices = {(l["option_type"], l["strike"]):
                           chain[l["option_type"]][l["strike"]] for l in legs}
        except KeyError:
            # A leg that did not trade in the exit bucket. Closing it at the last price it
            # DID trade at would be a fill nobody could have got; dropping the position is
            # the honest treatment, and the caller counts and discloses how many.
            return None
        exit_value = -_net_value(legs, exit_prices)
        exit_ts = opened["exit_at"]
        exit_reason = "TIME"
    else:
        s = settle.get(expiry)
        if s is None:
            return None
        exit_prices = _intrinsic(legs, s)
        exit_value = -_net_value(legs, exit_prices)
        exit_ts = dt.datetime.combine(expiry, dt.time(15, 29))
        exit_reason = "EXPIRY"

    pnl_pts = entry_credit + exit_value
    # Two crossings, not three: one entering, one exiting. A settlement is an official
    # print rather than a market order, so it crosses nothing -- same treatment as
    # structures.py. (The superseded Python walk charged three; see _run_cycle_python.)
    per_leg = sum(slippage.leg_slippage_pts(l["otm_points"]) for l in legs)
    slip = per_leg if exit_reason == "EXPIRY" else 2.0 * per_leg
    pnl_pts -= slip

    lot = contracts.lot_size(expiry, spec.symbol)
    if spec.structure == "long_option":
        margin_pts = -entry_credit
        margin_basis = "Premium paid in full; no SPAN margin is blocked."
    else:
        offset_steps = max(l["offset_steps"] for l in legs if l["action"] == "SELL")
        # The NOTE was previously discarded here. It is the difference between "margin is
        # this position's maximum loss, exactly" and "margin is a ratio calibrated today
        # and applied to the past, which understates what a seller needed on the worst
        # days" -- and return-on-margin is reported to two decimal places either way.
        # Shipping the number without its basis is the part that misleads.
        margin_pts, margin_basis = margin_mod.margin_points(
            spec.structure, legs, spot, strike_offset_steps=offset_steps, symbol=spec.symbol)

    ch = charges_mod.round_trip(abs(entry_credit), abs(exit_value), lot, lots, spec.symbol,
                                n_legs=len(legs), entry_is_sell=spec.is_credit)
    entry_ts = opened["entry_at"]
    return Trade(
        expiry=expiry, entry_date=c["trade_date"], entry_ts=entry_ts, exit_ts=exit_ts,
        exit_reason=exit_reason, spot_entry=spot, legs=legs,
        entry_credit_pts=entry_credit, exit_value_pts=exit_value, pnl_pts=pnl_pts,
        slippage_pts=slip, margin_pts=margin_pts, lot_size=lot,
        charges_rupees=ch.total, pnl_rupees=pnl_pts * lot * lots - ch.total,
        dte=int(c["dte"]), exit_prices=exit_prices, margin_basis=margin_basis)


def _run_cycle_python(spec, opened, path, settle, lots, thin_seen):
    """The original per-minute walk in Python. Superseded by exits.resolve(); kept solely
    so a test can assert the SQL implementation agrees with it."""
    c, legs, spot = opened["cycle"], opened["legs"], opened["spot"]
    expiry = c["expiry_date"]
    entry_prices = {(l["option_type"], l["strike"]): l["premium_pts"] for l in legs}
    entry_credit = _net_value(legs, entry_prices)
    width = _width_pts(legs)

    for l in legs:
        thin, note = liquidity.assess(c["dte"], l["otm_points"])
        if thin:
            thin_seen.add(note)

    series = [path.get((expiry, l["option_type"], l["strike"]), {}) for l in legs]
    entry_ts = None
    for ts in sorted(series[0]):
        if (ts.hour * 60 + ts.minute) >= spec.entry_minute and ts.date() == c["trade_date"]:
            entry_ts = ts
            break
    if entry_ts is None:
        return None

    # Walk minutes where EVERY leg has a real print; a stop cannot fire on a price that
    # did not exist.
    common = sorted(set.intersection(*[set(s) for s in series]) if series else [])
    exit_ts, exit_reason, exit_value = None, None, None
    for ts in common:
        if ts <= entry_ts or ts.date() > expiry:
            continue
        if ts.date() == expiry and (ts.hour * 60 + ts.minute) >= spec_mod.EOD_MINUTE:
            break
        prices = {(l["option_type"], l["strike"]): s[ts][0] for l, s in zip(legs, series)}
        value = -_net_value(legs, prices)
        reason = _exit_trigger(spec, entry_credit, width, value)
        if reason:
            exit_ts, exit_reason, exit_value = ts, reason, value
            break

    if exit_ts is None:
        s = settle.get(expiry)
        if s is None:
            return None
        exit_value = -_net_value(legs, _intrinsic(legs, s))
        exit_ts = dt.datetime.combine(expiry, dt.time(15, 29))
        exit_reason = "EXPIRY"

    pnl_pts = entry_credit + exit_value

    # BUG, kept visible: this charged round_trip (2 crossings) PLUS entry (1 more) = 3
    # crossings on any exit that was not a settlement, inflating costs by 50 % on every
    # stopped-out trade. _settle_cycle charges 2 -- one in, one out. Left here unchanged so
    # the equivalence test documents the difference rather than hiding it.
    slip = (0.0 if exit_reason == "EXPIRY"
            else slippage.round_trip_slippage_pts(legs))
    slip += sum(slippage.leg_slippage_pts(l["otm_points"]) for l in legs)
    pnl_pts -= slip

    lot = contracts.lot_size(expiry, spec.symbol)
    if spec.structure == "long_option":
        margin_pts, margin_note = -entry_credit, "Premium paid in full; no SPAN margin."
    else:
        offset_steps = max(l["offset_steps"] for l in legs if l["action"] == "SELL")
        margin_pts, margin_note = margin_mod.margin_points(
            spec.structure, legs, spot, strike_offset_steps=offset_steps,
            symbol=spec.symbol)

    ch = charges_mod.round_trip(
        abs(entry_credit), abs(exit_value), lot, lots, spec.symbol,
        n_legs=len(legs), entry_is_sell=spec.is_credit)

    return Trade(
        expiry=expiry, entry_date=c["trade_date"], entry_ts=entry_ts, exit_ts=exit_ts,
        exit_reason=exit_reason, spot_entry=spot, legs=legs,
        entry_credit_pts=entry_credit, exit_value_pts=exit_value, pnl_pts=pnl_pts,
        slippage_pts=slip, margin_pts=margin_pts, lot_size=lot,
        charges_rupees=ch.total, pnl_rupees=pnl_pts * lot * lots - ch.total,
        dte=int(c["dte"]))
