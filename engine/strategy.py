"""The open strategy protocol: an arbitrary position, and arbitrary rules over it.

WHY THIS EXISTS. v1's spec was a configurator: five named structures, a handful of numeric
parameters each, and a closed list of clock times. A capability probe of thirty ordinary
trader requests ran seven of them. Everything interesting -- "sell the 50-rupee strike",
"if this leg doubles, roll it further out", "stop the book at -15% for the year", a ratio
spread, a calendar, a jade lizard -- was refused, because each new idea needed a new enum
value somewhere in the engine.

THE SHAPE. A strategy is three things, and nothing else:

    legs    what to open, as an explicit list. Any number, any side, any quantity, any
            strike rule, any expiry. This alone covers every named structure plus every
            structure nobody has named.
    rules   when to do something about it. A condition tree over the position's live
            state -- its P&L, any single leg's mark, the spot, the clock, the running
            peak -- and an action: close, close some legs, open some legs, or roll.
    entry   when to open, including a condition that can refuse the cycle.

DECLARATIVE, NEVER CODE. Every condition is a JSON tree drawn from a closed operator set
and every action is a tagged object. Nothing here is evaluated as an expression, so a
strategy is data that can be stored, diffed, shared and rate-limited, and a hostile spec
is a validation error rather than an execution.

STRICT AND LOUD. An unknown key is an error, not something quietly ignored. A spec that
silently drops a rule the author believed was applied is worse than one that fails --
that is the same rule v1 was built on and it is the one thing worth keeping from it.

WHAT IS DELIBERATELY *NOT* RESTRICTED. Entry and exit may be any minute of the session,
not a nine-value grid. Legs may reference any expiry the chain carries. Quantities may be
unequal, which is what makes a ratio spread expressible. None of these are gated by tier:
the only thing a paid tier buys is a longer window of history.
"""
import datetime as dt
import re

SIDES = ("sell", "buy")
TYPES = ("CE", "PE")
CADENCES = ("weekly", "daily", "monthly")
EXPIRY_REFS = ("near", "next", "far")          # 0th, 1st, 2nd expiry at or after entry

# Comparators. Closed set, all numeric, all total -- no three-valued logic anywhere in
# rule evaluation, because a rule that is neither true nor false is a rule nobody can
# reason about at 3pm with money on the line.
COMPARATORS = ("gt", "gte", "lt", "lte", "eq", "between")

# Every quantity a rule may test. The names are the trader's, not the engine's.
FIELDS = {
    # --- the position, in points per lot ------------------------------------
    "pnl_pts":            "running mark-to-market P&L of the whole position, in points",
    "pnl_rupees":         "the same, in rupees at this lot size and lot count",
    "pnl_pct_of_credit":  "P&L as a fraction of the credit collected at entry "
                          "(-1.0 = the position has given back everything it took in)",
    "pnl_pct_of_max":     "P&L as a fraction of the most this position could ever make",
    "combined_premium":   "what it would cost to close the position right now, in points",
    "credit_kept_pct":    "fraction of the entry credit still unspent (1.0 at entry, "
                          "0.0 at break-even)",
    # --- a single leg -------------------------------------------------------
    "leg_mark":           "the current price of one leg, in points",
    "leg_mark_mult":      "that leg's price divided by what it opened at",
    "leg_mark_delta":     "that leg's price minus what it opened at, in points",
    "leg_pnl_pts":        "one leg's own contribution to P&L, in points",
    # --- the underlying -----------------------------------------------------
    "spot":               "the index, in points",
    "spot_move_pct":      "the index's move from where the trade opened, in per cent",
    "spot_move_pts":      "the same, in index points",
    "spot_beyond_strike": "how far the index is past a given leg's strike, in points "
                          "(negative while the strike is still out of the money)",
    # --- the market, BEFORE the trade exists --------------------------------
    # The reason for taking a trade, which until now could not be stated at all. Every
    # one of these is knowable at the moment it is read: `vix` is the live print, and
    # everything named prev_/gap_/realised_ is settled before the session opens. None of
    # them can see the day's own close, which is the leak that has bitten this project
    # once already.
    "vix":                "India VIX right now, at the minute being evaluated",
    "vix_prev_close":     "India VIX at the previous session's close",
    "vix_change_pct":     "how far VIX has moved today, in per cent of yesterday's close",
    "prev_day_move_pct":  "yesterday's close-to-close move in the index, in per cent",
    "gap_pct":            "this morning's open against yesterday's close, in per cent",
    "realised_vol_20d":   "annualised volatility of the index over the 20 sessions "
                          "ENDING YESTERDAY, in per cent",
    "day_of_week":        "1 = Monday through 5 = Friday",
    # --- the clock ----------------------------------------------------------
    "time":               "wall clock, HH:MM",
    "minutes_held":       "minutes of WALL CLOCK since the position opened, so an "
                          "overnight position counts the 17.5 hours the market was shut. "
                          "For 'a few hours into the session' use `time` instead",
    "dte":                "days to expiry of the nearest leg",
    # --- path-dependent -----------------------------------------------------
    "drawdown_from_peak": "points given back from the best mark-to-market so far",
    "runup_from_trough":  "points recovered from the worst mark-to-market so far",
    "adjustments_done":   "how many rules have fired on this position already",
}
LEG_FIELDS = {"leg_mark", "leg_mark_mult", "leg_mark_delta", "leg_pnl_pts",
              "spot_beyond_strike"}
TIME_FIELDS = {"time"}
# Fields describing the market rather than the position. They are the only ones that mean
# anything BEFORE a trade exists, which is what an entry gate is: everything else in
# FIELDS is either zero or undefined at that moment.
MARKET_FIELDS = {"vix", "vix_prev_close", "vix_change_pct", "prev_day_move_pct",
                 "gap_pct", "realised_vol_20d", "day_of_week"}

# ---------------------------------------------------------------- indicators
#
# Technical indicators on the INDEX, as entry conditions. "Only sell when RSI is under 30",
# "only when the close is above the 50-day average", "only while the 9 is over the 21".
# These are parametric, so they are a family matched by pattern rather than entries in
# FIELDS: `rsi_14`, `close_vs_sma_20_pct`, `close_vs_ema_50_pct`, `ema_9_vs_21_pct`.
#
# EVERY ONE IS COMPUTED ON YESTERDAY'S CLOSE AND EARLIER. Not today's -- today's close is
# the future at 09:15, and reading it is the exact leak that made 29 of 51 live strategies
# fantasy the last time. So rsi_14 on a Tuesday is the RSI of closes up to and including
# Monday, which is what a trader looking at a chart before the open actually sees.
#
# The window is capped at 250 sessions (about a year): a 500-day average needs history
# the free tier does not serve, and would be undefined for every day in the window.
import re as _re

INDICATOR_MIN, INDICATOR_MAX = 2, 250
_IND_PATTERNS = (
    (_re.compile(r"^rsi_(\d+)$"), "rsi"),
    (_re.compile(r"^close_vs_sma_(\d+)_pct$"), "close_vs_sma"),
    (_re.compile(r"^close_vs_ema_(\d+)_pct$"), "close_vs_ema"),
    (_re.compile(r"^ema_(\d+)_vs_(\d+)_pct$"), "ema_cross"),
    (_re.compile(r"^sma_(\d+)_vs_(\d+)_pct$"), "sma_cross"),
)

INDICATOR_DOC = {
    "rsi_N":              "Wilder RSI of the index over N sessions ending YESTERDAY, "
                          "0-100. rsi_14 is the usual one",
    "close_vs_sma_N_pct": "yesterday's close against the N-session simple moving "
                          "average, in per cent (positive = above the average)",
    "close_vs_ema_N_pct": "the same against the N-session exponential average",
    "ema_F_vs_S_pct":     "the F-session EMA against the S-session EMA, in per cent "
                          "(positive = the fast average is above the slow one). "
                          "ema_9_vs_21_pct > 0 is 'the 9 is over the 21'",
    "sma_F_vs_S_pct":     "the same with simple averages",
}


def indicator_field(name):
    """-> {"kind", "n", "m"} for an indicator field name, else None.

    Raises StrategyError for a name that LOOKS like an indicator but is out of range,
    because "unknown field rsi_500" would send the author looking for a typo."""
    for pat, kind in _IND_PATTERNS:
        m = pat.match(name)
        if not m:
            continue
        n = int(m.group(1))
        mm = int(m.group(2)) if m.lastindex and m.lastindex >= 2 else None
        for v in (n, mm):
            if v is not None and not (INDICATOR_MIN <= v <= INDICATOR_MAX):
                raise StrategyError(
                    f"{name}: the window must be between {INDICATOR_MIN} and "
                    f"{INDICATOR_MAX} sessions; {v} is outside that")
        if mm is not None and n >= mm:
            raise StrategyError(
                f"{name}: the fast window ({n}) must be shorter than the slow one ({mm})")
        return {"kind": kind, "n": n, "m": mm, "name": name}
    return None


def is_market_field(name):
    """A field that means something BEFORE a trade exists -- the only kind an entry gate
    may use."""
    return name in MARKET_FIELDS or indicator_field(name) is not None

ACTIONS = ("close", "close_legs", "open", "roll", "close_and_open")

# Strike selectors. Every one resolves to a listed strike at the moment it is applied.
STRIKE_KEYS = ("pct_offset", "points_offset", "atm", "strike", "premium_near",
               "delta_near", "from_leg")

MAX_LEGS = 12                 # a position, not a portfolio
MAX_RULES = 24
MAX_ADJUSTMENTS_CAP = 50      # a rule loop must terminate; this is the backstop
MIN_TRADING_DAYS = 20


class StrategyError(ValueError):
    """The strategy cannot be run as written. Always says why, and what is accepted."""


# ------------------------------------------------------------------------ helpers

def _only(d, allowed, where):
    stray = set(d) - set(allowed)
    if stray:
        raise StrategyError(
            f"{where}: unknown field(s) {', '.join(sorted(stray))}. "
            f"Accepted: {', '.join(sorted(allowed))}")


def _num(v, where, lo=None, hi=None):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise StrategyError(f"{where} must be a number, got {v!r}")
    f = float(v)
    if f != f or f in (float("inf"), float("-inf")):
        raise StrategyError(f"{where} must be finite, got {v!r}")
    if lo is not None and f < lo:
        raise StrategyError(f"{where} must be at least {lo}, got {f}")
    if hi is not None and f > hi:
        raise StrategyError(f"{where} must be at most {hi}, got {f}")
    return f


def _minute(label, where):
    """'09:20' -> 560. Any minute of the session, not a nine-value grid.

    v1 restricted entries to nine clock times because contract_day precomputes a print for
    each of them. That is a COST optimisation, and it had hardened into a capability
    limit: a strategy that enters at 09:20 is not exotic, it is Tuesday. The general
    engine reads the minute bar itself.
    """
    if not isinstance(label, str) or not re.fullmatch(r"\d{1,2}:\d{2}", label):
        raise StrategyError(f"{where} must be a clock time like '09:20', got {label!r}")
    h, m = (int(x) for x in label.split(":"))
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise StrategyError(f"{where} is not a valid time: {label!r}")
    t = h * 60 + m
    if not (SESSION_OPEN <= t <= SESSION_CLOSE):
        raise StrategyError(
            f"{where} {label} is outside the trading session "
            f"({fmt_minute(SESSION_OPEN)}–{fmt_minute(SESSION_CLOSE)})")
    return t


SESSION_OPEN = 9 * 60 + 15        # 09:15
SESSION_CLOSE = 15 * 60 + 29      # 15:29, the last tradeable minute
SETTLEMENT_MINUTE = SESSION_CLOSE


def fmt_minute(t):
    return f"{t // 60:02d}:{t % 60:02d}"


def _as_date(value, label):
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return dt.date.fromisoformat(value)
    raise StrategyError(f"{label} must be a YYYY-MM-DD date, got {value!r}")


# ------------------------------------------------------------------------- strike

def parse_strike(raw, where):
    """One strike selector. Resolved against the live chain, not at parse time.

    Every form answers the same question -- WHICH LISTED STRIKE -- from a different piece
    of information the trader actually has: a distance from spot, a premium they want to
    collect, a delta they want to be short, or another leg they want to sit relative to.
    """
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return {"kind": "strike", "value": float(raw)}
    if raw == "atm":
        return {"kind": "atm"}
    if not isinstance(raw, dict):
        raise StrategyError(
            f"{where}: a strike must be 'atm', a number, or an object using one of "
            f"{', '.join(STRIKE_KEYS)}")
    _only(raw, set(STRIKE_KEYS) | {"ref"}, where)
    keys = [k for k in STRIKE_KEYS if k in raw]
    if len(keys) != 1:
        raise StrategyError(
            f"{where}: give exactly one of {', '.join(STRIKE_KEYS)}; got "
            f"{', '.join(keys) or 'none'}")
    k = keys[0]
    # 'ref' says what "from spot" means once a position is already open: the spot now, or
    # the spot when the trade opened. A roll that re-centres wants NOW; a rule that widens
    # relative to the original strikes wants ENTRY.
    ref = raw.get("ref", "now")
    if ref not in ("now", "entry"):
        raise StrategyError(f"{where}: ref must be 'now' or 'entry'")
    if k == "atm":
        if raw["atm"] is not True:
            raise StrategyError(f"{where}: atm must be true")
        return {"kind": "atm", "ref": ref}
    if k == "from_leg":
        v = raw["from_leg"]
        if not isinstance(v, dict):
            raise StrategyError(f"{where}: from_leg must be an object")
        _only(v, {"leg", "pct", "points"}, f"{where}.from_leg")
        if "leg" not in v:
            raise StrategyError(f"{where}.from_leg needs a leg index")
        if ("pct" in v) == ("points" in v):
            raise StrategyError(f"{where}.from_leg needs exactly one of pct or points")
        return {"kind": "from_leg", "leg": int(v["leg"]),
                "pct": _num(v["pct"], f"{where}.from_leg.pct") if "pct" in v else None,
                "points": (_num(v["points"], f"{where}.from_leg.points")
                           if "points" in v else None)}
    val = _num(raw[k], f"{where}.{k}")
    if k == "pct_offset":
        _num(val, f"{where}.pct_offset", lo=-50.0, hi=50.0)
    if k == "delta_near":
        _num(abs(val), f"{where}.delta_near", lo=0.0, hi=1.0)
    if k == "premium_near":
        _num(val, f"{where}.premium_near", lo=0.0)
    return {"kind": k, "value": val, "ref": ref}


def parse_leg(raw, where, index=None):
    if not isinstance(raw, dict):
        raise StrategyError(f"{where} must be an object")
    _only(raw, {"side", "type", "qty", "strike", "expiry", "label"}, where)
    side = raw.get("side")
    if side not in SIDES:
        raise StrategyError(f"{where}.side must be 'sell' or 'buy', got {side!r}")
    otype = raw.get("type")
    if otype not in TYPES:
        raise StrategyError(f"{where}.type must be 'CE' or 'PE', got {otype!r}")
    qty = raw.get("qty", 1)
    if not isinstance(qty, int) or isinstance(qty, bool) or qty < 1 or qty > 100:
        raise StrategyError(f"{where}.qty must be a whole number from 1 to 100")
    if "strike" not in raw:
        raise StrategyError(f"{where} needs a strike")
    expiry = raw.get("expiry", "near")
    if expiry not in EXPIRY_REFS:
        raise StrategyError(
            f"{where}.expiry must be one of {', '.join(EXPIRY_REFS)} — 'near' is the "
            f"nearest expiry at entry, 'next' the one after it (which is how a calendar "
            f"spread is written)")
    # A label is free text a caller chose, and it is echoed back in refusals, rule
    # descriptions and the rendered report. Every render escapes it, so this is not an XSS
    # guard -- it is a bound. Unconstrained, it accepted any JSON value of any type and any
    # length up to the whole request body, which is a silly thing to carry through the
    # engine and store per trade.
    label = raw.get("label")
    if label is not None and (not isinstance(label, str) or len(label) > 60):
        raise StrategyError(f"{where}.label must be text of at most 60 characters")
    return {"side": side, "type": otype, "qty": qty, "expiry": expiry,
            "strike": parse_strike(raw["strike"], f"{where}.strike"),
            "label": label or (f"L{index}" if index is not None else None)}


# ---------------------------------------------------------------------- condition

def parse_condition(raw, where, n_legs):
    """A condition tree. Leaves compare one named quantity against a number.

    The grammar is tiny on purpose. Anything a trader can say about a live position is
    some combination of: how it is doing, what one leg is doing, where the index is, what
    time it is, and how far it has come back from its best. Five things, and/or/not over
    them, is enough for every request in the capability probe -- and it stays small enough
    to evaluate on every minute of every leg without profiling surprises.
    """
    if not isinstance(raw, dict):
        raise StrategyError(f"{where} must be an object")
    if len(raw) != 1:
        raise StrategyError(
            f"{where}: a condition is exactly one of all / any / not / a field test; got "
            f"{len(raw)} keys ({', '.join(sorted(raw))}). Combine them with "
            f'{{"all": [...]}}')
    (key, val), = raw.items()
    if key in ("all", "any"):
        if not isinstance(val, list) or not val:
            raise StrategyError(f"{where}.{key} must be a non-empty list of conditions")
        return {"op": key,
                "of": [parse_condition(v, f"{where}.{key}[{i}]", n_legs)
                       for i, v in enumerate(val)]}
    if key == "not":
        return {"op": "not", "of": [parse_condition(val, f"{where}.not", n_legs)]}
    if key not in FIELDS and indicator_field(key) is None:
        raise StrategyError(
            f"{where}: unknown field {key!r}. Available: {', '.join(sorted(FIELDS))}, "
            f"and index indicators: {', '.join(INDICATOR_DOC)}")
    if not isinstance(val, dict):
        raise StrategyError(
            f'{where}.{key} must be a comparison object like {{"lte": -80}}')
    allowed = set(COMPARATORS) | ({"leg"} if key in LEG_FIELDS else set())
    _only(val, allowed, f"{where}.{key}")
    cmps = [c for c in COMPARATORS if c in val]
    if len(cmps) != 1:
        raise StrategyError(
            f"{where}.{key}: give exactly one comparator of "
            f"{', '.join(COMPARATORS)}; got {', '.join(cmps) or 'none'}")
    cmp_ = cmps[0]
    if cmp_ == "between":
        pair = val["between"]
        if not (isinstance(pair, list) and len(pair) == 2):
            raise StrategyError(f'{where}.{key}.between must be [low, high]')
        # A CLOCK FIELD IS A CLOCK FIELD IN EVERY COMPARATOR. `time` took "09:15" under
        # gt/gte/lt/lte/eq and a bare number under `between`, so the one way to say "only
        # during the first half hour" -- the reason a time band exists at all -- was the
        # one way that failed. _cond_words already rendered a time band, which is the
        # tell: the reader anticipated it and the parser never allowed it.
        if key in TIME_FIELDS:
            bound = [_minute(pair[0], f"{where}.{key}.between[0]"),
                     _minute(pair[1], f"{where}.{key}.between[1]")]
        else:
            bound = [_num(pair[0], f"{where}.{key}.between[0]"),
                     _num(pair[1], f"{where}.{key}.between[1]")]
        if bound[0] > bound[1]:
            raise StrategyError(f"{where}.{key}.between: low is above high")
    elif key in TIME_FIELDS:
        bound = _minute(val[cmp_], f"{where}.{key}.{cmp_}")
    else:
        bound = _num(val[cmp_], f"{where}.{key}.{cmp_}")
    if key == "day_of_week":
        lo, hi = (bound, bound) if cmp_ != "between" else (bound[0], bound[1])
        if not (1 <= lo <= 7 and 1 <= hi <= 7):
            raise StrategyError(
                f"{where}.day_of_week runs 1 (Monday) to 7 (Sunday); the market is open "
                f"1-5. Got {val[cmp_]!r}")
    leg = None
    if key in LEG_FIELDS:
        if "leg" not in val:
            raise StrategyError(
                f"{where}.{key} is about ONE leg, so it needs \"leg\": <index>. "
                f"Legs are numbered from 0 in the order you listed them")
        leg = val["leg"]
        if not isinstance(leg, int) or isinstance(leg, bool) or not 0 <= leg < n_legs:
            raise StrategyError(
                f"{where}.{key}.leg must be a leg index from 0 to {n_legs - 1}")
    return {"op": "cmp", "field": key, "cmp": cmp_, "bound": bound, "leg": leg}


# ------------------------------------------------------------------------- action

def parse_action(raw, where, n_legs):
    """What to do when a rule fires.

    `close` ends the trade. Everything else MUTATES the position and the walk continues,
    which is the whole point: a strategy that can only end is a stop-loss, not trade
    management.
    """
    if raw == "close":
        return {"do": "close"}
    if not isinstance(raw, dict) or len(raw) != 1:
        raise StrategyError(
            f'{where}: an action is "close" or exactly one of '
            f'{", ".join(k for k in ACTIONS if k != "close")}')
    (key, val), = raw.items()
    if key == "close_legs":
        idx = _leg_list(val, f"{where}.close_legs", n_legs)
        return {"do": "close_legs", "legs": idx}
    if key == "open":
        if not isinstance(val, list) or not val:
            raise StrategyError(f"{where}.open must be a non-empty list of legs")
        return {"do": "open",
                "legs": [parse_leg(l, f"{where}.open[{i}]") for i, l in enumerate(val)]}
    if key == "roll":
        if not isinstance(val, dict):
            raise StrategyError(f"{where}.roll must be an object")
        _only(val, {"legs", "to", "qty"}, f"{where}.roll")
        if "legs" not in val or "to" not in val:
            raise StrategyError(
                f'{where}.roll needs "legs" (which to close) and "to" (the strike to '
                f'reopen at)')
        return {"do": "roll", "legs": _leg_list(val["legs"], f"{where}.roll.legs", n_legs),
                "to": parse_strike(val["to"], f"{where}.roll.to"),
                "qty": val.get("qty")}
    if key == "close_and_open":
        if not isinstance(val, dict):
            raise StrategyError(f"{where}.close_and_open must be an object")
        _only(val, {"close", "open"}, f"{where}.close_and_open")
        return {"do": "close_and_open",
                "legs": _leg_list(val.get("close", []), f"{where}.close_and_open.close",
                                  n_legs, allow_empty=True),
                "open": [parse_leg(l, f"{where}.close_and_open.open[{i}]")
                         for i, l in enumerate(val.get("open") or [])]}
    raise StrategyError(f"{where}: unknown action {key!r}. Available: "
                        f"{', '.join(ACTIONS)}")


def _leg_list(val, where, n_legs, allow_empty=False):
    if val == "all":
        return "all"
    if not isinstance(val, list) or (not allow_empty and not val):
        raise StrategyError(f'{where} must be "all" or a list of leg indices')
    out = []
    for v in val:
        if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v < n_legs:
            raise StrategyError(f"{where}: {v!r} is not a leg index from 0 to {n_legs - 1}")
        out.append(v)
    return out


# ------------------------------------------------------------------------ the spec

class Strategy:
    """A parsed strategy. Immutable in practice; the simulator never writes to it."""

    __slots__ = ("name", "symbol", "date_from", "date_to", "cadence", "entry_minute",
                 "entry_dte", "max_dte", "entry_when", "legs", "rules", "exit_minute",
                 "exit_when", "max_adjustments", "portfolio", "resolution", "raw")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    @property
    def n_legs(self):
        return len(self.legs)

    @property
    def structure(self):
        """A NAME for the shape, derived from the legs rather than declared.

        Everything downstream -- the summary, the honesty panel, the report headline --
        wants to call the position something. Naming the classic shapes when the legs
        happen to form one keeps a plain condor reading as "iron_condor" instead of
        "custom", and anything genuinely new gets an honest label rather than the nearest
        preset it does not actually match.
        """
        return shape_name(self.legs)

    @property
    def entry_time(self):
        return fmt_minute(self.entry_minute)

    @property
    def exit_time(self):
        return None if self.exit_minute is None else fmt_minute(self.exit_minute)

    @property
    def params(self):
        """Legacy read-through. `exits.py` and the older report prose expect a params
        dict; a leg-list strategy has no such thing, so it presents an empty one rather
        than half-inventing keys that were never set."""
        return {}

    @property
    def is_intraday(self):
        return self.exit_minute is not None

    # Legacy read-throughs for consumers written against the preset spec. The open
    # protocol has no separate gate/bias axis -- an entry condition IS the gate, and a
    # directional read is expressed by which legs you list -- so these report the neutral
    # value rather than inventing a name for something that was never set.
    @property
    def gate(self):
        return "always"

    @property
    def bias(self):
        return "neutral"

    @property
    def entry_dte_value(self):
        return self.entry_dte

    @property
    def needs_vix(self):
        """Does any condition in this strategy mention VIX?

        The minute series is a real query per chunk, so it is fetched only when something
        reads it. A strategy that never mentions volatility must not get slower because
        the field now exists.
        """
        live = {"vix", "vix_change_pct"}

        def walk(c):
            if c is None:
                return False
            if c["op"] == "cmp":
                return c["field"] in live
            return any(walk(x) for x in c["of"])

        return walk(self.entry_when) or walk(self.exit_when) \
            or any(walk(r["when"]) for r in self.rules)

    @property
    def indicators(self):
        """The indicator fields this strategy actually references, as parsed specs.

        Computed only for these, so a strategy that never mentions an average pays
        nothing for the family existing."""
        found = {}

        def walk(c):
            if c is None:
                return
            if c["op"] == "cmp":
                spec = indicator_field(c["field"])
                if spec:
                    found[c["field"]] = spec
                return
            for x in c["of"]:
                walk(x)

        walk(self.entry_when)
        walk(self.exit_when)
        for r in self.rules:
            walk(r["when"])
        return found

    @property
    def is_credit(self):
        return sum((-1 if l["side"] == "buy" else 1) * l["qty"] for l in self.legs) > 0

    def describe(self):
        """One line per element, for a report or a confirmation prompt."""
        out = [f"{len(self.legs)} legs, entered "
               + ("every session" if self.cadence == "daily"
                  else f"every {self.cadence} expiry {self.entry_dte}d out")
               + f" at {fmt_minute(self.entry_minute)}"]
        for i, l in enumerate(self.legs):
            out.append(f"  leg {i}: {l['side']} {l['qty']}x {l['type']} "
                       f"@ {_strike_words(l['strike'])} ({l['expiry']} expiry)")
        for i, r in enumerate(self.rules):
            out.append(f"  rule {i}: {_cond_words(r['when'])} → {_action_words(r['then'])}"
                       + (f" (up to {r['max_times']}x)" if r["max_times"] != 1 else ""))
        if self.exit_minute is not None:
            out.append(f"  hard exit at {fmt_minute(self.exit_minute)}")
        return out


def _strike_words(s):
    k = s["kind"]
    if k == "atm":
        return "the money"
    if k == "strike":
        return f"{s['value']:.0f}"
    if k == "pct_offset":
        return f"{s['value']}% from spot"
    if k == "points_offset":
        return f"{s['value']:.0f} points from spot"
    if k == "premium_near":
        return f"the strike nearest {s['value']:.0f} points of premium"
    if k == "delta_near":
        return f"the {abs(s['value']):.2f}-delta strike"
    if k == "from_leg":
        return (f"{s['pct']}% beyond leg {s['leg']}" if s["pct"] is not None
                else f"{s['points']:.0f} points beyond leg {s['leg']}")
    return k


def _cond_words(c):
    if c["op"] in ("all", "any"):
        joiner = " and " if c["op"] == "all" else " or "
        return "(" + joiner.join(_cond_words(x) for x in c["of"]) + ")"
    if c["op"] == "not":
        return "not " + _cond_words(c["of"][0])
    leg = f" of leg {c['leg']}" if c["leg"] is not None else ""
    b = c["bound"]
    word = {"gt": ">", "gte": "≥", "lt": "<", "lte": "≤", "eq": "=",
            "between": "in"}[c["cmp"]]
    if c["field"] in TIME_FIELDS:
        b = fmt_minute(int(b)) if c["cmp"] != "between" else \
            f"{fmt_minute(int(b[0]))}–{fmt_minute(int(b[1]))}"
    return f"{c['field']}{leg} {word} {b}"


def _action_words(a):
    d = a["do"]
    if d == "close":
        return "close the position"
    if d == "close_legs":
        return f"close leg(s) {a['legs']}"
    if d == "open":
        return f"open {len(a['legs'])} new leg(s)"
    if d == "roll":
        return f"roll leg(s) {a['legs']} to {_strike_words(a['to'])}"
    return f"close {a['legs']} and open {len(a['open'])}"


def shape_name(legs):
    """The classic shapes, recognised from the leg list. Order-independent."""
    sig = sorted((l["side"], l["type"], l["qty"], l["expiry"],
                  l["strike"]["kind"]) for l in legs)
    n = len(legs)
    sides = [l["side"] for l in legs]
    types = [l["type"] for l in legs]
    qtys = {l["qty"] for l in legs}
    expiries = {l["expiry"] for l in legs}
    offs = [l["strike"].get("value") for l in legs
            if l["strike"]["kind"] == "pct_offset"]
    if len(expiries) > 1:
        if n == 2:
            same_strike = len({str(l["strike"]) for l in legs}) == 1
            return "calendar" if same_strike else "diagonal"
        return f"multi_expiry_{n}_leg"
    if qtys != {1}:
        return "ratio"
    if n == 1:
        return "long_option" if sides[0] == "buy" else "naked_short"
    if n == 2 and set(sides) == {"sell"} and set(types) == {"CE", "PE"}:
        atm = all(l["strike"]["kind"] == "atm" for l in legs)
        return "short_straddle" if atm else "short_strangle"
    if n == 2 and set(sides) == {"buy"} and set(types) == {"CE", "PE"}:
        return "long_straddle" if all(l["strike"]["kind"] == "atm" for l in legs) \
            else "long_strangle"
    if n == 2 and len(set(types)) == 1 and set(sides) == {"sell", "buy"}:
        return "vertical_spread"
    if n == 4 and sorted(sides) == ["buy", "buy", "sell", "sell"] \
            and sorted(types) == ["CE", "CE", "PE", "PE"]:
        shorts = [l for l in legs if l["side"] == "sell"]
        if all(l["strike"]["kind"] == "atm" for l in shorts):
            return "iron_fly"
        if len(offs) == 4 and len({abs(o) for o in offs}) == 2:
            return "iron_condor"
        return "broken_wing_condor"
    if n == 3 and sorted(sides) == ["buy", "sell", "sell"]:
        return "jade_lizard" if len(set(types)) == 2 else "ratio_spread"
    return f"custom_{n}_leg"


TOP_LEVEL = {"name", "symbol", "period", "entry", "legs", "rules", "exit",
             "max_adjustments", "portfolio", "resolution"}


def parse(raw, window=None):
    """dict -> Strategy, or StrategyError explaining exactly what is wrong.

    `window` is (from, to) the caller's tier is allowed to see. It is the ONLY thing a
    tier changes: every feature in this module is available to every user.
    """
    if not isinstance(raw, dict):
        raise StrategyError("a strategy must be an object")
    _only(raw, TOP_LEVEL, "strategy")

    symbol = raw.get("symbol", "NIFTY")
    if symbol != "NIFTY":
        raise StrategyError(f"only NIFTY is served today; {symbol!r} is not available")

    legs_raw = raw.get("legs")
    if not isinstance(legs_raw, list) or not legs_raw:
        raise StrategyError(
            'a strategy needs "legs": a list of what to open. e.g. '
            '[{"side":"sell","type":"CE","strike":{"pct_offset":1.0}}]')
    if len(legs_raw) > MAX_LEGS:
        raise StrategyError(f"at most {MAX_LEGS} legs; got {len(legs_raw)}")
    legs = [parse_leg(l, f"legs[{i}]", i) for i, l in enumerate(legs_raw)]

    entry = raw.get("entry") or {}
    if not isinstance(entry, dict):
        raise StrategyError("entry must be an object")
    _only(entry, {"cadence", "time", "dte", "max_dte", "when"}, "entry")
    cadence = entry.get("cadence", "weekly")
    if cadence not in CADENCES:
        raise StrategyError(f"entry.cadence must be one of {', '.join(CADENCES)}")
    entry_minute = _minute(entry.get("time", "09:30"), "entry.time")
    entry_dte = entry.get("dte")
    max_dte = entry.get("max_dte")
    if cadence in ("weekly", "monthly"):
        # A monthly cycle is a month long, so entering four days out would spend most of
        # it flat. The default tracks the cadence rather than being one number for both.
        default_dte = 4 if cadence == "weekly" else 21
        entry_dte = (default_dte if entry_dte is None
                     else int(_num(entry_dte, "entry.dte", 0, 60)))
        if max_dte is not None:
            raise StrategyError(
                f"entry.max_dte is for cadence 'daily'. On a {cadence} cadence entry.dte "
                f"already picks the one day per expiry")
    else:
        if entry_dte is not None:
            raise StrategyError(
                "entry.dte picks ONE day per expiry, which contradicts cadence 'daily'. "
                "Use entry.max_dte to say how close to expiry a session must be")
        max_dte = None if max_dte is None else int(_num(max_dte, "entry.max_dte", 0, 60))
    entry_when = (parse_condition(entry["when"], "entry.when", len(legs))
                  if entry.get("when") is not None else None)

    rules_raw = raw.get("rules") or []
    if not isinstance(rules_raw, list):
        raise StrategyError("rules must be a list")
    if len(rules_raw) > MAX_RULES:
        raise StrategyError(f"at most {MAX_RULES} rules; got {len(rules_raw)}")
    rules = []
    for i, r in enumerate(rules_raw):
        if not isinstance(r, dict):
            raise StrategyError(f"rules[{i}] must be an object")
        _only(r, {"when", "then", "max_times", "label"}, f"rules[{i}]")
        if "when" not in r or "then" not in r:
            raise StrategyError(f'rules[{i}] needs "when" and "then"')
        mt = r.get("max_times", 1)
        if not isinstance(mt, int) or isinstance(mt, bool) or not 1 <= mt <= 100:
            raise StrategyError(f"rules[{i}].max_times must be a whole number 1–100")
        rules.append({
            "when": parse_condition(r["when"], f"rules[{i}].when", len(legs)),
            "then": parse_action(r["then"], f"rules[{i}].then", len(legs)),
            "max_times": mt, "label": r.get("label") or f"rule {i}"})

    exit_raw = raw.get("exit") or {}
    if not isinstance(exit_raw, dict):
        raise StrategyError("exit must be an object")
    _only(exit_raw, {"time", "when"}, "exit")
    exit_minute = (_minute(exit_raw["time"], "exit.time")
                   if exit_raw.get("time") is not None else None)
    if exit_minute is not None and exit_minute <= entry_minute and cadence == "daily":
        raise StrategyError(
            f"exit.time {fmt_minute(exit_minute)} is not after entry.time "
            f"{fmt_minute(entry_minute)}")
    exit_when = (parse_condition(exit_raw["when"], "exit.when", len(legs))
                 if exit_raw.get("when") is not None else None)

    max_adj = raw.get("max_adjustments", 4)
    if not isinstance(max_adj, int) or isinstance(max_adj, bool) \
            or not 0 <= max_adj <= MAX_ADJUSTMENTS_CAP:
        raise StrategyError(
            f"max_adjustments must be a whole number 0–{MAX_ADJUSTMENTS_CAP}. It bounds "
            f"how many times the rules may change the position in one trade, so the walk "
            f"is guaranteed to terminate")

    portfolio = parse_portfolio(raw.get("portfolio"))

    resolution = raw.get("resolution", 1)
    if resolution not in (1, 5, 15):
        raise StrategyError(
            "resolution must be 1, 5 or 15 minutes. 1 is the default and the honest one; "
            "the coarser settings exist to make very long searches affordable and will "
            "miss a trigger that fires and reverses inside the bar")

    period = raw.get("period") or {}
    if not isinstance(period, dict):
        raise StrategyError("period must be an object with from/to")
    _only(period, {"from", "to"}, "period")
    win_from, win_to = window if window else (dt.date(2019, 1, 1), dt.date(2026, 6, 30))
    date_from = _as_date(period.get("from", win_from), "period.from")
    date_to = _as_date(period.get("to", win_to), "period.to")
    if date_from > date_to:
        raise StrategyError("period.from is after period.to")
    if date_from < win_from or date_to > win_to:
        raise StrategyError(
            f"your tier serves {win_from} to {win_to}; {date_from} to {date_to} falls "
            f"outside it. The window is the ONLY thing a tier changes — every strategy "
            f"feature is available on every tier")
    if (date_to - date_from).days < MIN_TRADING_DAYS:
        raise StrategyError(
            f"period spans {(date_to - date_from).days} days; at least "
            f"{MIN_TRADING_DAYS} are required")

    strat = Strategy(
        name=raw.get("name") or "untitled", symbol=symbol,
        date_from=date_from, date_to=date_to, cadence=cadence,
        entry_minute=entry_minute, entry_dte=entry_dte, max_dte=max_dte,
        entry_when=entry_when, legs=legs, rules=rules, exit_minute=exit_minute,
        exit_when=exit_when, max_adjustments=max_adj, portfolio=portfolio,
        resolution=resolution, raw=raw)
    _cross_checks(strat)
    return strat


PORTFOLIO_KEYS = {"stop_after_losses", "stop_after_drawdown_pct", "skip_after_loss",
                  "max_trades", "stop_after_profit_pct", "resume_after_days"}


def parse_portfolio(raw):
    """Rules over the SEQUENCE of trades rather than inside one.

    "Stand down after three losers", "stop the book once it is 15% down" are the rules
    real people actually run, and no per-trade condition can express them because they
    depend on trades that have already closed.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise StrategyError("portfolio must be an object")
    _only(raw, PORTFOLIO_KEYS, "portfolio")
    out = {}
    for k in ("stop_after_losses", "max_trades", "resume_after_days"):
        if k in raw and raw[k] is not None:
            v = raw[k]
            if not isinstance(v, int) or isinstance(v, bool) or v < 1:
                raise StrategyError(f"portfolio.{k} must be a whole number of 1 or more")
            out[k] = v
    for k in ("stop_after_drawdown_pct", "stop_after_profit_pct"):
        if k in raw and raw[k] is not None:
            out[k] = _num(raw[k], f"portfolio.{k}", lo=0.0, hi=100.0)
    if raw.get("skip_after_loss"):
        if raw["skip_after_loss"] is not True:
            raise StrategyError("portfolio.skip_after_loss must be true if present")
        out["skip_after_loss"] = True
    return out


def _cross_checks(s):
    """Things that are only wrong in combination."""
    if s.max_adjustments == 0:
        for r in s.rules:
            if r["then"]["do"] != "close":
                raise StrategyError(
                    f"{r['label']} would change the position, but max_adjustments is 0. "
                    f"Raise it, or make the rule close instead")
    for r in s.rules:
        a = r["then"]
        if a["do"] == "roll" and a["legs"] != "all":
            for i in a["legs"]:
                if s.legs[i]["strike"]["kind"] == "premium_near" and \
                        a["to"]["kind"] == "from_leg" and a["to"]["leg"] == i:
                    raise StrategyError(
                        f"{r['label']} rolls leg {i} to a strike defined relative to leg "
                        f"{i}, which it is closing. Reference a leg that stays open")
    # A rule that can never fire is almost always a typo, and silently running it as
    # written is how someone concludes their stop "did not work".
    seen = set()
    for r in s.rules:
        key = _cond_words(r["when"])
        if key in seen:
            raise StrategyError(
                f"{r['label']} has the same condition as an earlier rule ({key}). Only "
                f"the first would ever fire — merge them or make them distinguishable")
        seen.add(key)
