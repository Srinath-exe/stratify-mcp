"""The general simulator: walk a position minute by minute and let rules change it.

WHAT IS DIFFERENT FROM backtest.py. That engine opens a fixed leg set, asks SQL for the
first minute a precomputed threshold is crossed, and settles. It is fast precisely because
the position never changes -- the rule collapses to a number, and a number can be pushed
into the database.

A rule that MUTATES the position cannot collapse to a number: what happens after the first
trigger depends on what the trigger did. So this engine walks. The walk is what buys
generality, and the cost of it is paid back by the shape of the queries:

    WAVES, NOT ROUND TRIPS PER CYCLE. Every open position across the whole chunk is
    advanced against ONE bulk path query. Positions that never adjust finish in wave 0.
    Only the ones that adjusted need wave 1, and so on. Query count is therefore
    O(max_adjustments), not O(cycles) -- 50 cycles with a rolling rule is five queries,
    not fifty.

    CHUNKED BY CYCLE so memory is bounded by the chunk rather than by the backtest. Seven
    years of daily entries is 2.6 million marks; a chunk of a hundred cycles is forty
    thousand.

SIGN CONVENTION, identical to backtest.py so a rule written for one means the same in the
other:

    sign(leg)   = +1 if SOLD else -1
    leg P&L     = sign * qty * (price_in - price_out)
    credit      = sum(sign * qty * price_in)          positive when premium was received
    cost_to_close = sum(sign * qty * mark)            over the legs still open

STALE MARKS ARE NOT MARKS. A minute in which a contract did not trade has no price, only
an old one. Every step requires a fresh-enough print for every open leg, carried forward
at most CARRY_MINUTES; beyond that the step is skipped rather than evaluated against a
price that no longer exists. This is the same rule the replay track uses, and it exists
because a stale leg once moved a trade 43 points away from its true outcome.
"""
import datetime as dt
from dataclasses import dataclass, field

from . import greeks, marks, strategy as strat_mod
from .config import charges as charges_mod, contracts, margin as margin_mod, slippage

CYCLE_CHUNK = 120           # positions advanced against one path query
CARRY_MINUTES = 60          # how long a print may stand in for a missing one
MAX_ATM_DRIFT_STEPS = 2     # same floor as backtest.py; see the note there
ENTRY_BUCKET_MINUTES = 15


# ------------------------------------------------------------------------ results

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
    margin_basis: str = ""
    exit_prices: dict = None
    adjustments: list = field(default_factory=list)


@dataclass
class Result:
    spec: object
    trades: list
    warnings: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    n_contracts: int = 0
    n_trading_days: int = 0
    skipped: dict = field(default_factory=dict)


# -------------------------------------------------------------------- the position

class Position:
    __slots__ = ("entry_date", "entry_ts", "entry_spot", "expiries", "legs", "credit",
                 "horizon", "cursor", "peak", "trough", "fired", "n_adj", "closed",
                 "exit_ts", "exit_reason", "log", "next_id", "margin_peak",
                 "margin_basis", "credit_at_entry", "max_profit", "last_seen")

    def __init__(self, entry_date, entry_ts, entry_spot, expiries):
        self.entry_date, self.entry_ts = entry_date, entry_ts
        self.entry_spot, self.expiries = entry_spot, expiries
        self.legs, self.credit = [], 0.0
        self.horizon = self.cursor = entry_ts
        self.peak = self.trough = 0.0
        self.fired, self.n_adj = {}, 0
        self.closed, self.exit_ts, self.exit_reason = False, None, None
        self.log, self.next_id = [], 0
        self.margin_peak, self.margin_basis = 0.0, ""
        self.credit_at_entry, self.max_profit = 0.0, None
        self.last_seen = None

    @property
    def open_legs(self):
        return [l for l in self.legs if l["open"]]

    def leg_for(self, slot):
        """The leg currently filling a spec slot. A rolled leg inherits the slot, so
        `leg 0` keeps meaning "the short call" after it has been rolled twice."""
        for l in self.legs:
            if l["open"] and l["slot"] == slot:
                return l
        return None


def _sign(leg):
    return 1.0 if leg["action"] == "SELL" else -1.0


def _add_leg(pos, otype, strike, action, qty, expiry, price, ts, spot, slot,
             step, atm=None):
    leg = {"id": pos.next_id, "slot": slot, "option_type": otype, "strike": int(strike),
           "action": action, "qty": int(qty), "expiry": expiry,
           "premium_pts": float(price), "otm_points": ((strike - spot) if otype == "CE"
                                                       else (spot - strike)),
           # FROM ATM, not from spot. This number feeds the naked-short SPAN ratio,
           # which is clamped at the ends of its calibration grid -- measuring it
           # from a spot that sits between strikes reports a different margin for
           # the same position depending on where the index happened to be.
           "offset_steps": (abs(strike - (atm if atm is not None else spot)) / step
                            if step else 0.0),
           "open": True, "open_ts": ts, "exit_price": None, "close_ts": None}
    pos.next_id += 1
    pos.legs.append(leg)
    pos.credit += _sign(leg) * leg["qty"] * leg["premium_pts"]
    return leg


def _close_leg(leg, price, ts, reason):
    leg["open"] = False
    leg["exit_price"] = float(price)
    leg["close_ts"] = ts
    leg["close_reason"] = reason


# -------------------------------------------------------------------------- state

def _state(pos, ts, mark_of, spot, market=None, vix=None):
    """Everything a rule can ask about, computed once per step.

    `market` is the day's settled state and `vix` the live print. Both are passed in
    rather than looked up, because they are the same two objects for every position in the
    chunk and fetching them per step would put a query inside the walk.
    """
    realised = sum(_sign(l) * l["qty"] * (l["premium_pts"] - l["exit_price"])
                   for l in pos.legs if not l["open"])
    mtm, cost = 0.0, 0.0
    per_leg = {}
    for l in pos.open_legs:
        m = mark_of[l["id"]]
        p = _sign(l) * l["qty"] * (l["premium_pts"] - m)
        mtm += p
        cost += _sign(l) * l["qty"] * m
        per_leg[l["slot"]] = (l, m, p)
    pnl = realised + mtm
    pos.peak = max(pos.peak, pnl)
    pos.trough = min(pos.trough, pnl)
    return {"ts": ts, "spot": spot, "pnl": pnl, "cost": cost, "per_leg": per_leg,
            "market": market or {}, "vix": vix}


def _field(name, leg_slot, pos, st, lot_size, lots):
    if name in strat_mod.LEG_FIELDS:
        got = st["per_leg"].get(leg_slot)
        if got is None:
            return None                       # that leg is not open: the test is undefined
        leg, mark, lpnl = got
        if name == "leg_mark":
            return mark
        if name == "leg_mark_mult":
            return mark / leg["premium_pts"] if leg["premium_pts"] > 0 else None
        if name == "leg_mark_delta":
            return mark - leg["premium_pts"]
        if name == "leg_pnl_pts":
            return lpnl
        if name == "spot_beyond_strike":
            return (st["spot"] - leg["strike"] if leg["option_type"] == "CE"
                    else leg["strike"] - st["spot"])
    if name == "pnl_pts":
        return st["pnl"]
    if name == "pnl_rupees":
        return st["pnl"] * lot_size * lots
    if name == "pnl_pct_of_credit":
        # Denominator is the credit at ENTRY, not the running total. A roll that collects
        # more premium must not quietly loosen a stop the author wrote against the
        # original credit.
        return st["pnl"] / abs(pos.credit_at_entry) if pos.credit_at_entry else None
    if name == "pnl_pct_of_max":
        mx = pos.max_profit
        return st["pnl"] / mx if mx else None
    if name == "combined_premium":
        return st["cost"]
    if name == "credit_kept_pct":
        c = pos.credit_at_entry
        return (c - st["cost"]) / c if c else None
    if name in strat_mod.MARKET_FIELDS:
        day = st["market"]
        if name == "vix":
            return st["vix"]
        if name == "vix_change_pct":
            # Undefined rather than zero when either half is missing: a gate written
            # against a VIX move must not silently read as "flat" on a day with no print.
            prev = day.get("vix_prev_close")
            if not prev or st["vix"] is None:
                return None
            return (st["vix"] / prev - 1.0) * 100.0
        if name == "day_of_week":
            return float(day["dow"]) if "dow" in day else None
        if name == "vix_prev_close":
            return day.get("vix_prev_close") or None
        got = day.get(name)
        return None if got is None else float(got)
    if name == "spot":
        return st["spot"]
    if name == "spot_move_pct":
        return (st["spot"] / pos.entry_spot - 1.0) * 100.0 if pos.entry_spot else None
    if name == "spot_move_pts":
        return st["spot"] - pos.entry_spot
    if name == "time":
        return st["ts"].hour * 60 + st["ts"].minute
    if name == "minutes_held":
        return (st["ts"] - pos.entry_ts).total_seconds() / 60.0
    if name == "dte":
        opens = pos.open_legs
        return min((l["expiry"] - st["ts"].date()).days for l in opens) if opens else None
    if name == "drawdown_from_peak":
        return pos.peak - st["pnl"]
    if name == "runup_from_trough":
        return st["pnl"] - pos.trough
    if name == "adjustments_done":
        return float(pos.n_adj)
    return None


def _compare(v, cmp_, bound):
    if cmp_ == "gt":
        return v > bound
    if cmp_ == "gte":
        return v >= bound
    if cmp_ == "lt":
        return v < bound
    if cmp_ == "lte":
        return v <= bound
    if cmp_ == "eq":
        return abs(v - bound) < 1e-9
    return bound[0] <= v <= bound[1]


def evaluate(cond, pos, st, lot_size, lots):
    """A condition tree against one instant. A leaf whose quantity is undefined -- a leg
    that is no longer open, a ratio with a zero denominator -- is FALSE, never an error:
    a rule about a leg that has been closed has simply not fired."""
    op = cond["op"]
    if op == "all":
        return all(evaluate(c, pos, st, lot_size, lots) for c in cond["of"])
    if op == "any":
        return any(evaluate(c, pos, st, lot_size, lots) for c in cond["of"])
    if op == "not":
        return not evaluate(cond["of"][0], pos, st, lot_size, lots)
    v = _field(cond["field"], cond["leg"], pos, st, lot_size, lots)
    if v is None:
        return False
    return _compare(v, cond["cmp"], cond["bound"])


# ------------------------------------------------------------------ strike resolving

def resolve_strike(sel, chain, otype, spot_now, spot_entry, expiry, pos, ts, symbol):
    """A selector -> a listed strike that actually printed, or None."""
    book = chain.get(otype) or {}
    if not book:
        return None
    listed = sorted(book)
    step = contracts.strike_step(expiry, symbol)
    ref = spot_entry if sel.get("ref") == "entry" else spot_now
    kind = sel["kind"]

    if kind == "strike":
        return _nearest(listed, sel["value"], otype == "CE")
    if kind == "atm":
        atm = _nearest(listed, ref, otype == "CE")
        return atm if atm is not None and abs(atm - ref) <= MAX_ATM_DRIFT_STEPS * step \
            else None
    if kind == "pct_offset":
        return _nearest(listed, ref * (1 + sel["value"] / 100.0), otype == "CE")
    if kind == "points_offset":
        return _nearest(listed, ref + sel["value"], otype == "CE")
    if kind == "premium_near":
        # The strike whose LAST REAL PRINT is closest to the premium asked for. This is
        # how a large part of the world actually specifies a strike, and it needs no model
        # at all -- the price is the data.
        target = sel["value"]
        return min(listed, key=lambda k: (abs(book[k] - target), k))
    if kind == "delta_near":
        years = max((expiry - ts.date()).days, 0) / 365.0 + (1.0 / (365 * 24))
        target = abs(sel["value"])
        best, best_gap = None, None
        for k in listed:
            d = greeks.delta(otype, ref, k, years, book[k])
            gap = abs(d - target)
            if best_gap is None or gap < best_gap:
                best, best_gap = k, gap
        return best
    if kind == "from_leg":
        anchor = pos.leg_for(sel["leg"]) if pos else None
        if anchor is None:
            return None
        base = anchor["strike"]
        away = 1 if otype == "CE" else -1
        target = (base * (1 + away * sel["pct"] / 100.0) if sel["pct"] is not None
                  else base + away * sel["points"])
        return _nearest(listed, target, otype == "CE")
    return None


def _nearest(listed, target, prefer_higher=True):
    """Nearest listed strike, ties broken FURTHER OUT OF THE MONEY.

    Targets land on multiples of the strike step, so when the exact target is not listed
    the two neighbours are exactly equidistant. Breaking that tie arbitrarily makes the
    same backtest return different strikes on re-run -- it disagreed on 4 of 490 legs
    before v1 pinned it down, and the general engine has to pin it the same way or the two
    disagree about what the same strategy is.
    """
    if not listed:
        return None
    return min(listed, key=lambda k: (abs(k - target), -k if prefer_higher else k))


# -------------------------------------------------------------------------- cycles

def _cycles(strat):
    """Entry opportunities, and which expiry each 'near'/'next'/'far' reference means.

    The entry DAYS come from the same query the v1 engine uses, so the two engines cannot
    disagree about when a strategy trades. What is new here is the expiry REFERENCES: a
    leg may name the nearest expiry, the one after it, or the one after that, which is the
    whole of what a calendar spread needs.
    """
    rows = marks.entry_days(strat.cadence, strat.entry_dte, strat.max_dte,
                            strat.date_from, strat.date_to)
    if not rows:
        return []
    expiry_list = sorted({e for e, _ in
                          marks.expiries_between(strat.date_from, strat.date_to,
                                                 strat.symbol)})
    out = []
    for r in rows:
        d = r["trade_date"]
        refs = _expiry_refs(expiry_list, d)
        # The row's own expiry IS 'near' by construction of the query; trust it over the
        # reconstruction so the two engines pick the same contract on a tie.
        refs["near"] = r["expiry_date"]
        ahead = [e for e in expiry_list if e > r["expiry_date"]]
        refs["next"] = ahead[0] if ahead else None
        refs["far"] = ahead[1] if len(ahead) > 1 else None
        out.append({"date": d, "expiries": refs})
    out.sort(key=lambda c: c["date"])
    return out


def _expiry_refs(expiry_list, day):
    """near / next / far -- the 0th, 1st and 2nd expiry at or after this day. This is the
    whole of what a calendar spread needs: two legs on two references."""
    ahead = [e for e in expiry_list if e >= day]
    return {"near": ahead[0] if len(ahead) > 0 else None,
            "next": ahead[1] if len(ahead) > 1 else None,
            "far": ahead[2] if len(ahead) > 2 else None}


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# --------------------------------------------------------------------------- open

def _open(strat, cycles, lots, skipped, market=None, vix_path=None):
    """Open every cycle in the chunk. Two queries for the whole chunk, not two per cycle."""
    needed = set()
    for c in cycles:
        for leg in strat.legs:
            e = c["expiries"].get(leg["expiry"])
            if e is not None:
                needed.add((e, c["date"]))
    if not needed:
        return []
    chain = marks.chain_at(sorted(needed), strat.entry_minute, strat.symbol)
    spot = marks.spot_at({c["date"] for c in cycles}, strat.entry_minute)

    out = []
    for c in cycles:
        s0 = spot.get(c["date"])
        if s0 is None:
            skipped["no_spot"] = skipped.get("no_spot", 0) + 1
            continue
        ts = dt.datetime.combine(c["date"], dt.time(strat.entry_minute // 60,
                                                    strat.entry_minute % 60))
        pos = Position(c["date"], ts, s0, c["expiries"])
        ok = True
        for i, spec_leg in enumerate(strat.legs):
            e = c["expiries"].get(spec_leg["expiry"])
            book = chain.get((e, c["date"])) if e else None
            if not book:
                skipped["no_chain"] = skipped.get("no_chain", 0) + 1
                ok = False
                break
            atm = _atm_of(book, s0)
            if atm is None or abs(atm - s0) > MAX_ATM_DRIFT_STEPS * contracts.strike_step(
                    e, strat.symbol):
                skipped["atm_drift"] = skipped.get("atm_drift", 0) + 1
                ok = False
                break
            k = resolve_strike(spec_leg["strike"], book, spec_leg["type"], s0, s0, e,
                               pos, ts, strat.symbol)
            px = (book.get(spec_leg["type"]) or {}).get(k) if k is not None else None
            if k is None or px is None or px <= 0:
                skipped["strike_not_traded"] = skipped.get("strike_not_traded", 0) + 1
                ok = False
                break
            _add_leg(pos, spec_leg["type"], k,
                     "SELL" if spec_leg["side"] == "sell" else "BUY",
                     spec_leg["qty"], e, px, ts, s0, i,
                     contracts.strike_step(e, strat.symbol), atm)
        if not ok:
            continue
        bad = _structurally_sound(pos.legs)
        if bad:
            skipped[bad] = skipped.get(bad, 0) + 1
            continue
        pos.credit_at_entry = pos.credit
        pos.max_profit = _max_profit(pos.legs)
        _remargin(pos, s0, strat.symbol)
        # The entry gate sees the position it is about to open: `combined_premium` is the
        # credit on offer, which is what "only take it if I collect 80 points" means.
        if strat.entry_when is not None:
            st = {"ts": ts, "spot": s0, "pnl": 0.0, "cost": pos.credit,
                  "per_leg": {l["slot"]: (l, l["premium_pts"], 0.0) for l in pos.legs},
                  "market": (market or {}).get(pos.entry_date, {}),
                  "vix": (vix_path or {}).get(ts)}
            lot = contracts.lot_size(min(l["expiry"] for l in pos.legs), strat.symbol)
            if not evaluate(strat.entry_when, pos, st, lot, lots):
                skipped["entry_condition"] = skipped.get("entry_condition", 0) + 1
                continue
        pos.horizon = _horizon(strat, pos)
        pos.cursor = ts
        out.append(pos)
    return out


def _horizon(strat, pos):
    """The last instant this position can be held. The EARLIEST expiry among its legs --
    a calendar's near leg settles and the position is marked out there, which is the
    honest simple rule and is stated rather than implied."""
    first_expiry = min(l["expiry"] for l in pos.legs)
    end = dt.datetime.combine(first_expiry,
                              dt.time(strat_mod.SESSION_CLOSE // 60,
                                      strat_mod.SESSION_CLOSE % 60))
    if strat.exit_minute is not None:
        same_day = dt.datetime.combine(pos.entry_date,
                                       dt.time(strat.exit_minute // 60,
                                               strat.exit_minute % 60))
        if same_day > pos.entry_ts:
            end = min(end, same_day)
    return end


def _atm_of(book, spot):
    """The nearest strike printing on BOTH sides. Every offset is measured from here."""
    both = sorted(set(book.get("CE") or {}) & set(book.get("PE") or {}))
    return _nearest(both, spot, True) if both else None


def _chain_is_usable(book, spot, expiry, symbol):
    """Is this minute thick enough to locate the money?

    Every offset-based strike is defined relative to spot, and a minute where the nearest
    strike printing on BOTH sides sits far from spot is a minute where "1% out" means
    something else entirely. v1 measured this drift and capped it at two steps: 93.5% of
    cycles land exactly on ATM, with a tail out to 33 steps -- 1,650 index points -- and
    building a position anyway does not give a worse fill, it gives a different strategy
    wearing this one's name.
    """
    ce, pe = book.get("CE") or {}, book.get("PE") or {}
    both = sorted(set(ce) & set(pe))
    if not both:
        return False
    step = contracts.strike_step(expiry, symbol)
    atm = _nearest(both, spot, True)
    return atm is not None and abs(atm - spot) <= MAX_ATM_DRIFT_STEPS * step


def _structurally_sound(legs):
    """Guards that are not about the strategy but about the DATA that built it.

    Each one is here because it shipped a wrong number first. A self-cancelling pair --
    a SELL and a BUY of the same type at the same strike -- contributes no premium, no
    risk and no margin, and once produced an "iron fly" reporting 100.6% return on margin
    in a single trade. A defined-risk position whose worst case is a profit is an
    arbitrage, which means it is a stale print, not an opportunity.
    """
    # KEYED ON THE EXPIRY TOO. Without it a calendar spread -- sell this week's ATM call,
    # buy next week's at the same strike -- looks like a self-cancelling pair and every
    # cycle is thrown away. They share a strike and cancel nothing: they are different
    # contracts with different lives, which is the entire point of the trade.
    #
    # AND ON THE QUANTITY. The guard used to refuse ANY contract carrying legs on both
    # sides, which is far wider than the bug it was built for. Two strike rules can
    # legitimately resolve onto the same contract on some days and not others -- a 1x2
    # ratio whose premium-selected shorts land on the long's own strike is still a real
    # position, net short one call -- and refusing it threw away 10 of 53 cycles of one
    # such strategy with nothing said beyond a skip counter. What actually faked the
    # margin was a contract netting to ZERO: no premium, no risk, no margin, and once an
    # "iron fly" reporting 100.6% return on margin. So that, and only that, is refused.
    #
    # The legs are NOT merged. Every leg keeps its own slot because rules address legs by
    # the index the author wrote them at, and premium, P&L and margin are all linear in
    # signed quantity, so the netted position is priced and margined correctly with the
    # slots left alone.
    net = {}
    for l in legs:
        k = (l["expiry"], l["option_type"], l["strike"])
        net[k] = net.get(k, 0.0) + _sign(l) * l["qty"]
    if any(abs(v) < 1e-9 for v in net.values()):
        return "offsetting_legs"
    credit = sum(_sign(l) * l["qty"] * l["premium_pts"] for l in legs)
    # The arbitrage check only means anything when every leg shares an expiry: a calendar
    # has real time-value risk that a single-expiry payoff scan cannot see.
    if len({l["expiry"] for l in legs}) == 1 and credit > 0 \
            and margin_mod.is_defined_risk(legs) \
            and margin_mod.max_loss_points(legs) <= 0:
        return "cannot_lose"
    return None


def _max_profit(legs):
    """The most a position can make, by scanning the payoff at every strike boundary and
    both tails. None when the upside is open-ended, so a rule written against it simply
    does not fire rather than firing against an invented ceiling."""
    credit = sum(_sign(l) * l["qty"] * l["premium_pts"] for l in legs)
    ks = sorted({l["strike"] for l in legs})
    if not ks:
        return None
    span = max(ks) - min(ks) + max(ks) * 0.5 + 1000

    def payoff(s):
        v = credit
        for l in legs:
            intr = (max(0.0, s - l["strike"]) if l["option_type"] == "CE"
                    else max(0.0, l["strike"] - s))
            v += -_sign(l) * l["qty"] * intr
        return v

    lo, hi = max(0.0, min(ks) - span), max(ks) + span
    if payoff(hi) > payoff(max(ks)) + 1e-6 or payoff(lo) > payoff(min(ks)) + 1e-6:
        return None
    best = max([payoff(k) for k in ks] + [payoff(lo), payoff(hi)])
    return best if best > 0 else None


def _remargin(pos, spot, symbol):
    """Margin for the position AS IT NOW STANDS, and the peak across its life.

    Peak, not entry. A position that starts naked and is hedged half way through really
    did need the naked margin; one that starts hedged and has its protection rolled away
    really did need more than it started with. Sizing off the entry alone reports capital
    the account never had to have -- in the flattering direction.
    """
    legs = pos.open_legs
    if not legs:
        return
    steps = max((l["offset_steps"] for l in legs if l["action"] == "SELL"), default=0.0)
    pts, basis = margin_mod.margin_points(None, legs, spot, strike_offset_steps=steps,
                                          symbol=symbol)
    if pts > pos.margin_peak:
        pos.margin_peak, pos.margin_basis = pts, basis


# --------------------------------------------------------------------------- walk

def _timeline(pos, path, spot_path):
    """(ts, {leg_id: mark}) for every instant at which EVERY open leg has a fresh print.

    A step where one leg is stale is skipped rather than evaluated: a rule fired against a
    price that no longer exists is worse than a rule that fires a minute later.
    """
    legs = pos.open_legs
    series = []
    for l in legs:
        p = path.get((l["expiry"], l["option_type"], l["strike"]))
        if not p:
            return []
        series.append((l["id"], sorted(p.items())))
    stamps = sorted({t for _, ser in series for t, _ in ser
                     if pos.cursor < t <= pos.horizon})
    if not stamps:
        return []
    idx = {lid: 0 for lid, _ in series}
    last = {lid: None for lid, _ in series}
    carry = dt.timedelta(minutes=CARRY_MINUTES)
    out = []
    for t in stamps:
        ok = True
        marks_now = {}
        for lid, ser in series:
            i = idx[lid]
            while i < len(ser) and ser[i][0] <= t:
                last[lid] = ser[i]
                i += 1
            idx[lid] = i
            if last[lid] is None or t - last[lid][0] > carry:
                ok = False
                break
            marks_now[lid] = last[lid][1]
        if ok:
            out.append((t, marks_now, spot_path.get(t)))
    return out


def _advance(strat, pos, path, spot_path, lot_size, lots, market=None, vix_path=None):
    """Run one position forward until it closes, adjusts, or reaches its horizon.

    Returns the pending action when it stopped to adjust, else None. Stopping at the first
    adjustment is what makes the wave model work: the position's leg set has changed, so
    the paths it needs have changed, and the next wave fetches them for every position at
    once rather than one query per adjustment.
    """
    for ts, mark_of, spot in _timeline(pos, path, spot_path):
        if spot is None:
            continue
        st = _state(pos, ts, mark_of, spot, (market or {}).get(ts.date()),
                    (vix_path or {}).get(ts))
        pos.cursor = ts
        pos.last_seen = (ts, mark_of, spot)
        if strat.exit_when is not None and \
                evaluate(strat.exit_when, pos, st, lot_size, lots):
            _close_all(pos, ts, mark_of, "RULE")
            return None
        for ri, rule in enumerate(strat.rules):
            if pos.fired.get(ri, 0) >= rule["max_times"]:
                continue
            if not evaluate(rule["when"], pos, st, lot_size, lots):
                continue
            pos.fired[ri] = pos.fired.get(ri, 0) + 1
            act = rule["then"]
            if act["do"] == "close":
                _close_all(pos, ts, mark_of, "RULE")
                pos.log.append({"ts": str(ts), "rule": rule["label"], "did": "close",
                                "pnl_pts": round(st["pnl"], 2)})
                return None
            if pos.n_adj >= strat.max_adjustments:
                # The budget is spent. Firing the rule would be a lie about what the
                # strategy did, so the position is closed instead -- and it says so.
                _close_all(pos, ts, mark_of, "ADJ_LIMIT")
                pos.log.append({"ts": str(ts), "rule": rule["label"],
                                "did": "hit max_adjustments, closed"})
                return None
            return {"ts": ts, "marks": mark_of, "spot": spot, "rule": rule,
                    "action": act, "pnl": st["pnl"]}
    return None


def _close_all(pos, ts, mark_of, reason):
    for l in pos.open_legs:
        _close_leg(l, mark_of[l["id"]], ts, reason)
    pos.closed = True
    pos.exit_ts, pos.exit_reason = ts, reason


# ---------------------------------------------------------------------- adjustments

def _apply(strat, pending, lots):
    """Carry out every pending action across the chunk.

    Actions that OPEN legs need the chain at the minute they fired, so those are grouped
    by minute -- cycles that trigger together share one query, and a strategy whose rule
    never opens anything needs no query at all.
    """
    want = {}
    for pos, act in pending:
        a = act["action"]
        opens = (a.get("legs") if a["do"] == "open" else None) or []
        if a["do"] in ("roll", "close_and_open") or (a["do"] == "open"):
            minute = act["ts"].hour * 60 + act["ts"].minute
            for e in pos.expiries.values():
                if e is not None:
                    want.setdefault(minute, set()).add((e, act["ts"].date()))
    chains = {m: marks.chain_at(sorted(pairs), m, strat.symbol)
              for m, pairs in want.items()}

    for pos, act in pending:
        ts, mark_of, spot = act["ts"], act["marks"], act["spot"]
        a = act["action"]
        minute = ts.hour * 60 + ts.minute
        chain_all = chains.get(minute, {})
        note = {"ts": str(ts), "rule": act["rule"]["label"], "pnl_pts": round(act["pnl"], 2)}

        def close_these(which):
            legs = pos.open_legs if which == "all" else \
                [l for l in pos.open_legs if l["slot"] in set(which)]
            for l in legs:
                _close_leg(l, mark_of[l["id"]], ts, "ADJUST")
            return [l["slot"] for l in legs]

        def open_these(specs, anchor_slot=None):
            made = []
            for j, sp in enumerate(specs):
                e = pos.expiries.get(sp["expiry"])
                book = chain_all.get((e, ts.date())) if e else None
                if not book:
                    continue
                k = resolve_strike(sp["strike"], book, sp["type"], spot, pos.entry_spot,
                                   e, pos, ts, strat.symbol)
                px = (book.get(sp["type"]) or {}).get(k) if k is not None else None
                if k is None or px is None or px <= 0:
                    continue
                slot = anchor_slot if anchor_slot is not None else \
                    (strat.n_legs + pos.next_id)
                made.append(_add_leg(pos, sp["type"], k,
                                     "SELL" if sp["side"] == "sell" else "BUY",
                                     sp["qty"], e, px, ts, spot, slot,
                                     contracts.strike_step(e, strat.symbol),
                                     _atm_of(book, spot)))
            return made

        if a["do"] == "close_legs":
            note["did"] = f"closed legs {close_these(a['legs'])}"
        elif a["do"] == "open":
            made = open_these(a["legs"])
            note["did"] = f"opened {len(made)} leg(s)"
        elif a["do"] == "roll":
            closed = [l for l in pos.open_legs
                      if a["legs"] == "all" or l["slot"] in set(a["legs"])]
            note["did"] = f"rolled legs {[l['slot'] for l in closed]}"
            for l in closed:
                _close_leg(l, mark_of[l["id"]], ts, "ROLL")
                e = l["expiry"]
                book = chain_all.get((e, ts.date()))
                if not book:
                    continue
                k = resolve_strike(a["to"], book, l["option_type"], spot, pos.entry_spot,
                                   e, pos, ts, strat.symbol)
                px = (book.get(l["option_type"]) or {}).get(k) if k is not None else None
                if k is None or px is None or px <= 0:
                    continue
                _add_leg(pos, l["option_type"], k, l["action"],
                         a.get("qty") or l["qty"], e, px, ts, spot, l["slot"],
                         contracts.strike_step(e, strat.symbol), _atm_of(book, spot))
        elif a["do"] == "close_and_open":
            gone = close_these(a["legs"]) if a["legs"] else []
            made = open_these(a["open"])
            note["did"] = f"closed {gone}, opened {len(made)}"

        pos.n_adj += 1
        pos.log.append(note)
        _remargin(pos, spot, strat.symbol)
        pos.max_profit = _max_profit(pos.open_legs) if pos.open_legs else None
        if not pos.open_legs:
            # Every leg closed and nothing reopened: the trade is over, whatever the rule
            # thought it was doing.
            pos.closed = True
            pos.exit_ts, pos.exit_reason = ts, "RULE"


# ------------------------------------------------------------------------- settle

def _settle(strat, pos, lots, settle_spot):
    """Turn a finished position into a Trade.

    Legs whose own expiry is the horizon SETTLE, at intrinsic against NSE's final
    settlement price -- the mean of the index over the last thirty minutes, which is
    neither the 15:29 close nor the option's last print. Legs that outlive the horizon (a
    calendar's back month) are marked out at their last real price instead, because they
    have time value that intrinsic would throw away.
    """
    if not pos.closed:
        open_legs = pos.open_legs
        if not open_legs:
            pos.closed, pos.exit_reason = True, "RULE"
            pos.exit_ts = pos.exit_ts or pos.cursor
        else:
            hz_date = pos.horizon.date()
            expiring = [l for l in open_legs if l["expiry"] <= hz_date]
            at_expiry = bool(expiring) and pos.horizon.hour * 60 + pos.horizon.minute \
                >= strat_mod.SESSION_CLOSE
            last = pos.last_seen
            for l in open_legs:
                if at_expiry and l["expiry"] <= hz_date:
                    s = settle_spot.get(l["expiry"])
                    if s is None:
                        return None
                    intr = (max(0.0, s - l["strike"]) if l["option_type"] == "CE"
                            else max(0.0, l["strike"] - s))
                    _close_leg(l, intr, pos.horizon, "EXPIRY")
                else:
                    if last is None or l["id"] not in last[1]:
                        return None
                    _close_leg(l, last[1][l["id"]], last[0], "TIME")
            pos.closed = True
            pos.exit_ts = pos.horizon if at_expiry else (last[0] if last else pos.horizon)
            pos.exit_reason = "EXPIRY" if at_expiry else "TIME"

    if not pos.legs or any(l["exit_price"] is None for l in pos.legs):
        return None

    first_expiry = min(l["expiry"] for l in pos.legs)
    lot = contracts.lot_size(first_expiry, strat.symbol)
    gross = sum(_sign(l) * l["qty"] * (l["premium_pts"] - l["exit_price"])
                for l in pos.legs)
    # One crossing entering, one leaving -- EXCEPT at expiry, where the position is cash
    # settled against an official print and crosses no spread at all. A rolled leg is two
    # openings and is charged for both, which is what actually happened.
    slip = sum(slippage.leg_slippage_pts(l["otm_points"]) * l["qty"]
               * (1.0 if l.get("close_reason") == "EXPIRY" else 2.0)
               for l in pos.legs)
    net = gross - slip
    ch = charges_mod.ChargeBreakdown()
    for l in pos.legs:
        ch = ch + charges_mod.round_trip(
            l["premium_pts"], l["exit_price"], lot, lots * l["qty"], strat.symbol,
            n_legs=1, entry_is_sell=(l["action"] == "SELL"))
    entry_legs = [l for l in pos.legs if l["open_ts"] == pos.entry_ts]
    entry_credit = sum(_sign(l) * l["qty"] * l["premium_pts"] for l in entry_legs)
    return Trade(
        expiry=first_expiry, entry_date=pos.entry_date, entry_ts=pos.entry_ts,
        exit_ts=pos.exit_ts, exit_reason=pos.exit_reason, spot_entry=pos.entry_spot,
        legs=pos.legs, entry_credit_pts=entry_credit,
        exit_value_pts=gross - entry_credit, pnl_pts=net, slippage_pts=slip,
        margin_pts=pos.margin_peak, lot_size=lot, charges_rupees=ch.total,
        pnl_rupees=net * lot * lots - ch.total,
        dte=(first_expiry - pos.entry_date).days, margin_basis=pos.margin_basis,
        exit_prices=None, adjustments=pos.log)


# ---------------------------------------------------------------------- portfolio

DEFAULT_BOOK_CAPITAL = 1_000_000.0


def apply_portfolio(strat, trades):
    """Rules over the SEQUENCE of trades, applied in exit order.

    These cannot live inside a position: "stand down after three losers" depends on trades
    that have already closed. They are a FILTER over the realised book, and because a
    skipped trade changes what comes after it, the pass is sequential by construction.

    The drawdown stop is measured on the ONE-LOT cumulative P&L against
    `portfolio.capital`, because that is the only definition available before position
    sizing -- which is a view applied downstream -- has been chosen. Stated rather than
    implied.
    """
    p = strat.portfolio or {}
    if not p:
        return trades, {}
    cap = DEFAULT_BOOK_CAPITAL
    resume_days = p.get("resume_after_days")
    kept, dropped = [], {}
    run_pnl, peak, losses, stopped, skip_next = 0.0, 0.0, 0, None, False
    stopped_on = None
    for t in sorted(trades, key=lambda x: (x.exit_ts, x.entry_ts)):
        if stopped:
            # A cool-off, if one was asked for. Without it the stop is permanent, which is
            # a legitimate rule but is almost never what "stand down after three losers"
            # means -- over seven years it turns a strategy into its first bad month.
            if resume_days is not None and \
                    (t.entry_ts.date() - stopped_on).days >= resume_days:
                stopped, stopped_on, losses, peak = None, None, 0, run_pnl
            else:
                dropped[stopped] = dropped.get(stopped, 0) + 1
                continue
        if p.get("max_trades") and len(kept) >= p["max_trades"]:
            dropped["max_trades"] = dropped.get("max_trades", 0) + 1
            continue
        if skip_next:
            # ONE cycle is skipped, then trading resumes. Reading the flag off the last
            # KEPT trade instead made the skip permanent -- after the first loss the last
            # kept trade stayed the loser forever, and a 75%-win strategy took nine trades
            # out of fifty-one.
            skip_next = False
            dropped["skip_after_loss"] = dropped.get("skip_after_loss", 0) + 1
            continue
        kept.append(t)
        run_pnl += t.pnl_rupees
        peak = max(peak, run_pnl)
        losses = losses + 1 if t.pnl_rupees < 0 else 0
        if p.get("skip_after_loss") and t.pnl_rupees < 0:
            skip_next = True
        if p.get("stop_after_losses") and losses >= p["stop_after_losses"]:
            stopped, stopped_on = "stop_after_losses", t.exit_ts.date()
        dd = (peak - run_pnl) / cap * 100.0
        if p.get("stop_after_drawdown_pct") and dd >= p["stop_after_drawdown_pct"]:
            stopped, stopped_on = "stop_after_drawdown_pct", t.exit_ts.date()
        up = run_pnl / cap * 100.0
        if p.get("stop_after_profit_pct") and up >= p["stop_after_profit_pct"]:
            stopped, stopped_on = "stop_after_profit_pct", t.exit_ts.date()
    return kept, dropped


# ------------------------------------------------------------------------------ run

def run(strat, lots=1):
    """Execute a parsed Strategy. Returns a Result shaped like backtest.run's, so metrics,
    the honesty panel, the detail builder and the report all consume it unchanged."""
    cycles = _cycles(strat)
    if not cycles:
        return Result(spec=strat, trades=[], warnings=[
            "no entry days matched this cadence and window"], n_contracts=0,
            n_trading_days=0)

    trades, skipped = [], {}
    keys_seen, days_seen = set(), set()
    # ONE query for the whole run: a few hundred rows covering every day it can touch.
    # Fetched unconditionally rather than only when a rule needs it, because it is smaller
    # than the decision about whether to fetch it.
    market = marks.market_days(strat.date_from, strat.date_to)
    for chunk in _chunks(cycles, CYCLE_CHUNK):
        vix_entry = marks.vix_series(
            dt.datetime.combine(min(c["date"] for c in chunk),
                                dt.time(strat_mod.SESSION_OPEN // 60,
                                        strat_mod.SESSION_OPEN % 60)),
            dt.datetime.combine(max(c["date"] for c in chunk),
                                dt.time(strat_mod.SESSION_CLOSE // 60,
                                        strat_mod.SESSION_CLOSE % 60)),
            strat.resolution) if strat.needs_vix else {}
        positions = _open(strat, chunk, lots, skipped, market, vix_entry)
        days_seen.update(c["date"] for c in chunk)
        if not positions:
            continue
        t0 = min(p.entry_ts for p in positions)
        t1 = max(p.horizon for p in positions)
        spot_path = marks.spot_series(t0, t1, strat.resolution)
        # Only paid for when a rule actually asks for it. A strategy that never mentions
        # VIX must not get slower because the field now exists.
        vix_path = (marks.vix_series(t0, t1, strat.resolution) if strat.needs_vix else {})
        for _wave in range(strat.max_adjustments + 1):
            active = [p for p in positions if not p.closed and p.cursor < p.horizon]
            if not active:
                break
            keys = {(l["expiry"], l["option_type"], l["strike"])
                    for p in active for l in p.open_legs}
            keys_seen.update(keys)
            # Bounded by each contract's OWN remaining life, not by the position's. A
            # calendar's back-month leg is seven days from ITS expiry while the position
            # is three days from the front one -- bounding on the position dropped every
            # bar of the back leg and made every calendar unpriceable.
            span = max((max(l["expiry"] for l in p.open_legs) - p.entry_date).days
                       for p in active) + 1
            path = marks.paths(sorted(keys), min(p.cursor for p in active),
                               max(p.horizon for p in active), strat.resolution,
                               max_days_open=span)
            pending = []
            for p in active:
                lot = contracts.lot_size(min(l["expiry"] for l in p.open_legs),
                                         strat.symbol)
                act = _advance(strat, p, path, spot_path, lot, lots, market, vix_path)
                if act:
                    pending.append((p, act))
            if not pending:
                break
            _apply(strat, pending, lots)

        settle_spot = marks.settlement({p.horizon.date() for p in positions})
        for p in positions:
            t = _settle(strat, p, lots, settle_spot)
            if t is None:
                skipped["unpriceable_exit"] = skipped.get("unpriceable_exit", 0) + 1
            else:
                trades.append(t)

    trades.sort(key=lambda t: (t.exit_ts, t.entry_ts))
    trades, dropped = apply_portfolio(strat, trades)
    skipped.update(dropped)

    notes = []
    n_adj = sum(len(t.adjustments) for t in trades)
    if n_adj:
        notes.append(f"{n_adj} rule action(s) fired across {sum(1 for t in trades if t.adjustments)} "
                     f"of {len(trades)} trades")
    if skipped:
        notes.append("cycles not taken: " +
                     ", ".join(f"{k}={v}" for k, v in sorted(skipped.items())))
    if strat.resolution > 1:
        notes.append(f"walked every {strat.resolution} minutes, not every minute: a "
                     f"trigger that fires and reverses inside a bar is not seen")
    return Result(spec=strat, trades=trades, notes=notes,
                  n_contracts=len(keys_seen), n_trading_days=len(days_seen),
                  skipped=skipped)
