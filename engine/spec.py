"""Strategy specification: the public contract, and everything we refuse.

Shape adopted from the 39 live paper-trading strategies (decision A1), so the MCP, the
research pipeline and paper trading all speak one language:

    {"structure": "short_strangle", "symbol": "NIFTY",
     "params": {"pct_offset": 1.5, "sl_mult": 2.0, "entry_dte": 4},
     "entry_time": "09:30", "period": {"from": "2025-07-01", "to": "2026-06-30"}}

Validation is deliberately strict and closed: an unknown key is an error, not something
quietly ignored. A spec that silently drops a parameter the user believed was applied is
worse than one that fails.
"""
import datetime as dt
import re
from dataclasses import dataclass, field

from . import signals

# debit_spread is deliberately absent (decision A4): a live Fyers SPAN check confirmed a
# margin-sizing bug in structures.py's implementation, which produced implausible
# 200-900 % CAGR. It ships when that is fixed and re-verified, not before.
STRUCTURE_PARAMS = {
    "short_strangle": {"required": {"pct_offset"}, "optional": {"sl_mult", "entry_dte"}},
    "credit_spread":  {"required": {"pct_offset", "pct_width"},
                       "optional": {"sl_mult", "tp_pct", "entry_dte", "direction"}},
    "iron_condor":    {"required": {"pct_offset", "pct_width"}, "optional": {"entry_dte"}},
    "iron_fly":       {"required": {"pct_width"}, "optional": {"entry_dte"}},
    "long_option":    {"required": {"pct_offset", "direction"},
                       "optional": {"sl_pct", "tp_pct", "entry_dte"}},
}

# Anti-oracle floors (decision D1), enforced before the router. A backtest over one
# contract on one day returns that contract's price difference; repeated across the chain
# it reconstructs the data one aggregate at a time. These floors are the control.
MIN_DISTINCT_CONTRACTS = 20
MIN_TRADING_DAYS = 20

# The served window is a TIER property, not a constant. The free tier sees one year; paid
# tiers see the full history. Both are served by the same pipeline over the same tables --
# the boundary is a date range, enforced here and (for defence in depth) by a ClickHouse
# row policy on the free user, so a bug here cannot widen what a free key can read.
import os

TIER_WINDOWS = {
    "free": (dt.date(2025, 7, 1), dt.date(2026, 6, 30)),
    "plus": (dt.date(2021, 1, 1), dt.date(2026, 6, 30)),
    "pro":  (dt.date(2019, 1, 1), dt.date(2026, 6, 30)),
    "bench": (dt.date(2019, 1, 1), dt.date(2026, 6, 30)),   # capacity testing only
}
DEFAULT_TIER = os.getenv("STRATIFY_DEFAULT_TIER", "free")
DATA_FROM, DATA_TO = TIER_WINDOWS[DEFAULT_TIER]


def window_for(tier):
    return TIER_WINDOWS.get(tier, TIER_WINDOWS["free"])

ENTRY_TIMES = ("09:15", "09:30", "11:00", "12:00", "12:30", "13:00", "14:00", "15:00", "EOD")
# The same grid serves as exits. It is not an arbitrary restriction: contract_day holds a
# precomputed last-real-print for each of these clock times, so an exit on the grid costs
# one lookup in a 232 K-row table. An arbitrary minute would mean scanning options_1min
# for every cycle, which is the cost that exit detection was moved into SQL to avoid.
EXIT_TIMES = ENTRY_TIMES
EOD_MINUTE = 929          # 15:29, the last tradeable minute. NOT 15:00.
SESSION_OPEN_MINUTE = 555  # 09:15

# WEEKLY: one entry per expiry, on the trading day matching entry_dte. This is the shape
# the 39 live strategies trade, and it was the only shape v1 offered.
# DAILY: one entry per trading DAY, on whichever expiry is nearest that day. Combined with
# exit_time this is a true intraday round trip -- enter 11:00, exit 14:00, every session --
# which is roughly 246 trades a year instead of 58. It costs about four times the CPU of a
# weekly run for the same period, because it opens four times as many positions.
CADENCES = ("weekly", "daily")


def minute_of(label):
    """'14:00' -> 840, 'EOD' -> 929."""
    if label == "EOD":
        return EOD_MINUTE
    h, m = label.split(":")
    return int(h) * 60 + int(m)


class SpecError(ValueError):
    """The spec cannot be run as written. Always says why."""


@dataclass(frozen=True)
class StrategySpec:
    structure: str
    symbol: str = "NIFTY"
    params: dict = field(default_factory=dict)
    entry_time: str = "EOD"
    date_from: dt.date = DATA_FROM
    date_to: dt.date = DATA_TO
    gate: str = "always"
    bias: str = "neutral"
    cadence: str = "weekly"
    # None means "hold until a stop, a target, or expiry settlement" -- the v1 behaviour.
    # A clock time means the position is squared off that same session, and expiry
    # settlement never enters the result at all.
    exit_time: str = None
    # DAILY ONLY: skip sessions where the nearest expiry is further out than this. The way
    # to say "only trade 0-2 DTE" without dropping to one entry a week.
    max_dte: int = None

    @property
    def entry_minute(self):
        return minute_of(self.entry_time)

    @property
    def exit_minute(self):
        return None if self.exit_time is None else minute_of(self.exit_time)

    @property
    def is_intraday(self):
        """True when every position opens and closes inside one session."""
        return self.exit_time is not None

    @property
    def entry_dte(self):
        return int(self.params.get("entry_dte", 4))

    @property
    def is_credit(self):
        return self.structure != "long_option"

    @property
    def n_legs(self):
        return {"short_strangle": 2, "credit_spread": 2, "iron_condor": 4,
                "iron_fly": 4, "long_option": 1}[self.structure]


def _as_date(value, label):
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return dt.date.fromisoformat(value)
    raise SpecError(f"{label} must be a YYYY-MM-DD date, got {value!r}")


def parse(raw, tier=None):
    """dict -> StrategySpec, or SpecError explaining exactly what is wrong."""
    win_from, win_to = window_for(tier) if tier else (DATA_FROM, DATA_TO)
    if not isinstance(raw, dict):
        raise SpecError("spec must be an object")

    unknown = set(raw) - {"structure", "symbol", "params", "entry_time", "period",
                          "gate", "bias", "cadence", "exit_time", "max_dte"}
    if unknown:
        raise SpecError(f"unknown top-level field(s): {', '.join(sorted(unknown))}")

    structure = raw.get("structure")
    if structure not in STRUCTURE_PARAMS:
        extra = ""
        if structure == "debit_spread":
            extra = (" — debit_spread is withheld from v1: a live SPAN check found a "
                     "margin-sizing bug in it that produced implausible returns")
        raise SpecError(
            f"unknown structure {structure!r}. Available: "
            f"{', '.join(sorted(STRUCTURE_PARAMS))}{extra}")

    symbol = raw.get("symbol", "NIFTY")
    if symbol != "NIFTY":
        raise SpecError(f"the free tier serves NIFTY only; {symbol!r} is not available")

    params = raw.get("params") or {}
    if not isinstance(params, dict):
        raise SpecError("params must be an object")
    allowed = STRUCTURE_PARAMS[structure]
    missing = allowed["required"] - set(params)
    if missing:
        raise SpecError(f"{structure} requires {', '.join(sorted(missing))}")
    stray = set(params) - allowed["required"] - allowed["optional"]
    if stray:
        raise SpecError(
            f"{structure} does not take {', '.join(sorted(stray))}. Accepted: "
            f"{', '.join(sorted(allowed['required'] | allowed['optional']))}")

    entry_time = raw.get("entry_time", "EOD")
    if entry_time not in ENTRY_TIMES:
        raise SpecError(f"entry_time must be one of {', '.join(ENTRY_TIMES)}")

    cadence = raw.get("cadence", "weekly")
    if cadence not in CADENCES:
        raise SpecError(f"cadence must be one of {', '.join(CADENCES)}")

    exit_time = raw.get("exit_time")
    if exit_time is not None:
        if exit_time not in EXIT_TIMES:
            raise SpecError(
                f"exit_time must be one of {', '.join(EXIT_TIMES)}. These are the clock "
                f"times with a precomputed print; an arbitrary minute is not offered "
                f"because it would cost a full scan per cycle")
        if minute_of(exit_time) <= minute_of(entry_time):
            raise SpecError(
                f"exit_time {exit_time} is not after entry_time {entry_time}; a position "
                f"cannot close before it opens. EOD is 15:29")

    max_dte = raw.get("max_dte")
    if max_dte is not None:
        if cadence != "daily":
            raise SpecError(
                "max_dte applies to cadence 'daily' only. On a weekly cadence the entry "
                "day is chosen by entry_dte, which already fixes the days to expiry")
        try:
            max_dte = int(max_dte)
        except (TypeError, ValueError):
            raise SpecError("max_dte must be a whole number of days") from None
        if not 0 <= max_dte <= 45:
            raise SpecError("max_dte must be between 0 and 45")

    if cadence == "daily" and "entry_dte" in params:
        raise SpecError(
            "cadence 'daily' enters every session on whichever expiry is nearest, so "
            "entry_dte -- which picks ONE day per expiry -- would contradict it. Use "
            "max_dte to restrict how far from expiry a session may be")

    period = raw.get("period") or {}
    date_from = _as_date(period.get("from", win_from), "period.from")
    date_to = _as_date(period.get("to", win_to), "period.to")
    if date_from > date_to:
        raise SpecError("period.from is after period.to")
    if date_from < win_from or date_to > win_to:
        raise SpecError(
            f"your tier serves {win_from} to {win_to}; {date_from} to {date_to} falls "
            f"outside it")
    if (date_to - date_from).days < MIN_TRADING_DAYS:
        raise SpecError(
            f"period spans {(date_to - date_from).days} days; a backtest must cover at "
            f"least {MIN_TRADING_DAYS} to be run. Very narrow windows return little more "
            f"than the prices themselves")

    spec = StrategySpec(structure=structure, symbol=symbol, params=params,
                        entry_time=entry_time, date_from=date_from, date_to=date_to,
                        gate=raw.get("gate", "always"), bias=raw.get("bias", "neutral"),
                        cadence=cadence, exit_time=exit_time, max_dte=max_dte)
    _validate_values(spec)
    return spec


def _is_finite(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return f == f and f not in (float("inf"), float("-inf"))


def _validate_values(spec):
    p = spec.params
    # NON-FINITE NUMBERS, refused before anything else touches them.
    #
    # Every numeric bound below is written as `float(x) <= 0` or `0 <= x <= 20`, and EVERY
    # such comparison is False for nan -- so nan passed all of them. It then reached the
    # stop-loss level as nan, and `price <= nan` is False at every minute, so the stop
    # simply never fired. The result came back looking normal: 50 trades, all settled at
    # expiry, +39,385 rupees, with `sl_mult: nan` echoed in the spec as though it had been
    # applied. The same strategy with a real stop LOSES 5,884. A silently inverted answer
    # is worse than an error, and JSON has no nan literal -- anything sending one is
    # already malformed.
    for key, value in p.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not _is_finite(value):
                raise SpecError(
                    f"{key} must be a finite number; got {value!r}. A non-finite bound "
                    f"silently disables the rule it belongs to rather than applying it")
    # A NUMERIC PARAMETER THAT IS NOT A NUMBER. Every bound below was written as
    # `float(p[key]) <= 20`, and float("1.0 + iv") raises ValueError, not SpecError -- so
    # a caller who wrote an expression where a number belongs got an unhandled traceback
    # instead of a sentence telling them what to fix. Coerce once, with a message.
    # A REQUIRED parameter present but null is a missing parameter wearing a value's
    # clothes: `float(None)` is a TypeError, which reaches the caller as a traceback.
    for key in STRUCTURE_PARAMS[spec.structure]["required"]:
        if p.get(key) is None:
            raise SpecError(f"{spec.structure} requires {key}; it was given as null")
    for key in ("pct_offset", "pct_width", "sl_mult", "sl_pct", "tp_pct", "entry_dte"):
        if key in p and p[key] is not None:
            p_val = p[key]
            if isinstance(p_val, bool) or not _is_finite(p_val):
                raise SpecError(
                    f"{key} must be a number; got {p_val!r}. Parameters are fixed values, "
                    f"not expressions — there is no way to make one depend on the market "
                    f"at entry")
    for key in ("pct_offset", "pct_width"):
        if key in p and not (0.0 <= float(p[key]) <= 20.0):
            raise SpecError(f"{key} must be between 0 and 20 percent")
    for key in ("sl_mult", "sl_pct", "tp_pct"):
        if key in p and p[key] is not None and float(p[key]) <= 0:
            raise SpecError(f"{key} must be positive when set")
    if "sl_pct" in p and p["sl_pct"] is not None and float(p["sl_pct"]) > 1.0:
        raise SpecError("sl_pct is a fraction of premium paid; it cannot exceed 1.0")
    if "entry_dte" in p and not (0 <= int(p["entry_dte"]) <= 45):
        raise SpecError("entry_dte must be between 0 and 45")
    if spec.gate not in signals.gate_names():
        raise SpecError(f"unknown gate {spec.gate!r}. Available: "
                        f"{', '.join(signals.gate_names())}")
    if spec.bias != "neutral" and spec.bias not in signals.bias_names():
        raise SpecError(f"unknown bias {spec.bias!r}. Available: neutral, "
                        f"{', '.join(signals.bias_names())}")
    if spec.structure in ("credit_spread", "long_option"):
        direction = p.get("direction")
        # A bias picks the side per cycle, so a fixed direction is only needed without one.
        if spec.bias == "neutral" and direction not in ("CE", "PE"):
            raise SpecError(
                f"{spec.structure} needs either direction 'CE'/'PE' or a bias to choose "
                f"the side each cycle. Available biases: {', '.join(signals.bias_names())}")
        if spec.bias != "neutral" and direction is not None:
            raise SpecError(
                f"{spec.structure} was given both a fixed direction and bias "
                f"{spec.bias!r}; they would contradict each other. Set one")
    if spec.bias != "neutral" and spec.structure in ("short_strangle", "iron_condor",
                                                     "iron_fly"):
        raise SpecError(
            f"{spec.structure} is direction-neutral, so bias {spec.bias!r} would have no "
            f"effect. Use a gate to filter entries, or a directional structure")
    if spec.structure == "credit_spread" and float(p["pct_width"]) <= 0:
        raise SpecError("credit_spread needs a positive pct_width")


def check_coverage(n_contracts_scanned, n_days_scanned):
    """Post-run anti-oracle check. Measured on what the query SCANNED (every contract
    returned by a chain lookup for every candidate cycle), not what the strategy ended up
    TRADING (decision D1, open action #8, resolved 2026-08-25) -- a spec that looks broad
    and resolves to almost nothing once a gate/bias is applied is a legitimate selective
    strategy, not an oracle attempt; what actually reconstructs the underlying data one
    price at a time is the scan itself, which happens regardless of whether the gate later
    says no."""
    if n_contracts_scanned < MIN_DISTINCT_CONTRACTS:
        raise SpecError(
            f"this spec's queries touched only {n_contracts_scanned} distinct contracts; "
            f"at least {MIN_DISTINCT_CONTRACTS} are required. Widen the period or the "
            f"strike range")
    if n_days_scanned < MIN_TRADING_DAYS:
        raise SpecError(
            f"this spec's queries covered only {n_days_scanned} candidate days; at least "
            f"{MIN_TRADING_DAYS} are required. Widen the period")
