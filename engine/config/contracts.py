"""Contract specification: lot size and strike step, per expiry.

MEASURED, not assumed. NIFTY's lot size changes inside the served window -- expiries
through 2025-12-30 trade in lots of 75, and from 2026-01-06 in lots of 65. It is derived
per expiry from NSE bhavcopy (which reports volume in lots against our bars in units, so
the ratio is the lot size) and lives in the serving database's contract_spec table.

This is not a detail. BACKTEST/weekly_options_research/build_intraday_grid.py carries
LOT_SIZE = {"NIFTY": 65, ...} with no date dimension; applied across this window that
understates position size by 15.4 % for every trade before 2026-01-06, and every rupee
figure -- P&L, charges, margin, return-on-capital -- scales with it.

There is deliberately no default. An expiry with no measured spec raises rather than
falling back to a guess.
"""
import functools

from .. import db


class UnknownExpiry(KeyError):
    """No measured contract spec for this expiry. Never guess a lot size."""


@functools.lru_cache(maxsize=8)
def _spec_table(symbol):
    """Measured specs for the STANDARD chain, keyed by expiry.

    A symbol's listed options are not all one instrument. NIFTY's weekly and monthly
    chain has traded on a 50-point strike grid throughout — its lot size changes by
    regime (75, then 50, then 25, then 65) and that is real — but the same table also
    holds a handful of LONG-DATED series on 500 and 1000-point grids, four of them
    running out to 2027. Those are a different product, and the lot size measured for
    them is measured from very little: one carried 80 observations against the ~4,000 a
    normal expiry has.

    Two of them landed inside the served window and reported a lot size of 50 sitting
    between the correct 75 and 65 regimes, so the published coverage claimed three lot
    sizes where the market had two, and any cycle that selected one of those expiries
    would have been sized 50 instead of 75 or 65 — every rupee figure wrong by a third.

    Filtering on the dominant strike step keeps the chain this service actually serves
    and prices, without hardcoding a number: it is derived per symbol from the data.
    An expiry excluded here raises UnknownExpiry at selection time, which the engine
    already treats as a cycle it cannot trade — the safe outcome, and a visible one.
    """
    rows = db.rows(
        f"SELECT expiry_date, lot_size, strike_step, n_obs "
        f"FROM {db.database()}.contract_spec WHERE symbol = %(s)s", {"s": symbol})
    if not rows:
        return {}
    steps = {}
    for r in rows:
        steps[r["strike_step"]] = steps.get(r["strike_step"], 0) + 1
    standard = max(steps, key=lambda k: (steps[k], -k))
    return {r["expiry_date"]: r for r in rows if r["strike_step"] == standard}


def standard_strike_step(symbol="NIFTY"):
    """The strike grid this service serves for a symbol, derived from the data."""
    table = _spec_table(symbol)
    return next(iter(table.values()))["strike_step"] if table else None


def spec(expiry_date, symbol="NIFTY"):
    table = _spec_table(symbol)
    try:
        return table[expiry_date]
    except KeyError:
        raise UnknownExpiry(
            f"no measured contract spec for {symbol} expiry {expiry_date}; "
            f"refusing to assume a lot size") from None


def lot_size(expiry_date, symbol="NIFTY"):
    return spec(expiry_date, symbol)["lot_size"]


def strike_step(expiry_date, symbol="NIFTY"):
    return spec(expiry_date, symbol)["strike_step"]


def select_strike(spot, pct_offset, option_type, expiry_date, symbol="NIFTY"):
    """Nearest listed strike to `pct_offset` percent away from spot, in the OTM direction.

    TIE-BREAK: targets land on multiples of the strike step, so when the exact target is
    not listed, target +/- one step are EQUIDISTANT. Picking arbitrarily makes the same
    backtest return different strikes on re-run -- it disagreed on 4 of 490 legs before
    this was pinned down. The rule is to take the strike further OTM: higher for a call,
    lower for a put. Conservative for a seller, and deterministic, which matters more.
    """
    step = strike_step(expiry_date, symbol)
    sign = 1.0 if option_type == "CE" else -1.0
    target = spot * (1.0 + sign * pct_offset / 100.0)
    lower = int(target // step) * step
    upper = lower + step
    d_lower, d_upper = abs(target - lower), abs(target - upper)
    if d_lower < d_upper:
        return lower
    if d_upper < d_lower:
        return upper
    return upper if option_type == "CE" else lower   # tie -> further OTM
