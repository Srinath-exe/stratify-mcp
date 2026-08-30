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
    return {r["expiry_date"]: r for r in db.rows(
        f"SELECT expiry_date, lot_size, strike_step, n_obs "
        f"FROM {db.DATABASE}.contract_spec WHERE symbol = %(s)s", {"s": symbol})}


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
