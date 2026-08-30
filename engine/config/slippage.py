"""Slippage: an explicit assumption, not a measurement. Read this before trusting it.

WHAT SLIPPAGE MEANS HERE: the cost of crossing the bid-ask spread. Filling at the bid when
selling, or the ask when buying, instead of the mid, costs exactly half the quoted spread
per leg. Same convention as paper_trading/fill_engine.leg_slippage_pts(). It does NOT
include execution-timing or queue-position cost, which this backtest cannot model at all
because it prices off completed bars.

WHY IT IS NOT MEASURED. The served data is OHLCV. It has no quotes, so the spread has to be
inferred, and both standard inferences fail on this data -- checked, not assumed:

  * Roll (1984), from the serial covariance of price changes: needs NEGATIVE serial
    covariance to produce a spread at all. Measured on Feb-2026 weeklies it is POSITIVE for
    every near-the-money bucket (+0.54, +0.37, +0.32), because option prices trend with the
    underlying and that swamps the bid-ask bounce. The estimator returns nothing usable.
  * Corwin-Schultz (2012), from consecutive high-low ranges: returns 0.008 to 0.028 points
    for out-of-the-money buckets. The tick size is 0.05, so a spread below one tick is
    impossible. Values below the tick floor are proof the estimator is not working here,
    not a finding about tight spreads.

So slippage is a DECLARED ASSUMPTION with a documented basis, floored at half a tick, and
the report shows it as an input rather than a result. Users may override it, and should be
shown how sensitive their result is to it.

THE PATH TO MEASURING IT: paper_trading records real entry_slippage_pts / exit_slippage_pts
against live quotes for every fill. That is the ground truth, and it is the differentiator
no competitor has. It is not usable yet -- as of 2026-08-22 the live book holds 1 trade,
the daemon having gone live on 2026-08-20. `measured_half_spread_pts()` reads it when the
sample is large enough and otherwise says so, rather than quietly falling back.
"""
import os
import sqlite3
from pathlib import Path

TICK_SIZE = 0.05
MIN_HALF_SPREAD_PTS = TICK_SIZE / 2.0     # cannot cross less than half a tick
MIN_TRADES_FOR_MEASURED = 30              # same floor as the honesty panel (decision C1)

PAPER_TRADING_DB = Path(os.getenv(
    "STRATIFY_PAPER_TRADING_DB",
    Path(__file__).resolve().parents[3] / "paper_trading/state/paper_trades.sqlite"))

# Assumed half-spread in index points, by how far out of the money the strike is.
# Basis: the option is quoted in 0.05 ticks; near-the-money weeklies trade essentially
# every minute (99.9 % of minutes, stratify.liquidity_profile) and quote in a handful of
# ticks, while far strikes trade in under 60 % of minutes and quote wider relative to a
# price of one or two points. These are round numbers chosen to be defensible and
# slightly pessimistic -- they are not fitted to anything.
_ASSUMED_HALF_SPREAD_PTS = (
    (250,  0.05),   # <= 250 points OTM, or in the money
    (750,  0.10),
    (1250, 0.15),
    (2000, 0.25),
    (None, 0.40),   # beyond 2000 points OTM
)

ASSUMPTION_NOTE = (
    "Slippage is modelled, not measured: the served data carries no quotes, and both Roll "
    "and Corwin-Schultz estimators fail on it (positive serial covariance near the money; "
    "sub-tick values out of the money). Treat it as an input you can change.")


def assumed_half_spread_pts(otm_points):
    """Half-spread per leg, in index points, for a strike this far out of the money.
    Negative `otm_points` means in the money."""
    for edge, value in _ASSUMED_HALF_SPREAD_PTS:
        if edge is None or otm_points <= edge:
            return max(MIN_HALF_SPREAD_PTS, value)
    return MIN_HALF_SPREAD_PTS


def leg_slippage_pts(otm_points, override_pts=None):
    if override_pts is not None:
        return max(MIN_HALF_SPREAD_PTS, float(override_pts))
    return assumed_half_spread_pts(otm_points)


def round_trip_slippage_pts(legs, override_pts=None):
    """Total slippage in points for entering and exiting every leg -- both sides cross."""
    return 2.0 * sum(leg_slippage_pts(l["otm_points"], override_pts) for l in legs)


def measured_half_spread_pts(symbol="NIFTY", structure=None):
    """Real slippage from paper trading, or None with a reason if there is not enough of it.

    Returns (value_or_None, note). Never silently substitutes the assumption.
    """
    if not PAPER_TRADING_DB.exists():
        return None, f"no paper-trading book at {PAPER_TRADING_DB}"
    sql = ("SELECT count(*), avg(entry_slippage_pts), avg(exit_slippage_pts) FROM trades "
           "WHERE entry_slippage_pts IS NOT NULL AND symbol = ?")
    args = [symbol]
    if structure:
        sql += " AND structure = ?"
        args.append(structure)
    con = sqlite3.connect(f"file:{PAPER_TRADING_DB}?mode=ro", uri=True)
    try:
        n, entry, exit_ = con.execute(sql, args).fetchone()
    finally:
        con.close()
    if not n or n < MIN_TRADES_FOR_MEASURED:
        return None, (f"only {n or 0} live fills recorded for {symbol}"
                      f"{'/' + structure if structure else ''}; "
                      f"need {MIN_TRADES_FOR_MEASURED} before reporting measured slippage")
    return (entry + exit_) / 4.0, f"measured from {n} live fills"
