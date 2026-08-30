"""Tradeability, from the measured liquidity profile.

"There is a bar" and "you could have traded" are different statements. 58 % of all
contract-minutes in the window have zero volume -- normal for an option chain, and the
reason decision B4 requires volume at entry. This module turns liquidity_profile
into a yes/no on whether a strike was realistically tradeable, plus the evidence behind it.

The profile is measured, and the structure it shows is the real one: out-of-the-money
weeklies trade in ~99.9 % of minutes out to 750 points and degrade to ~59 % at 2000 points,
while IN-the-money options are illiquid everywhere -- 7.8 % of minutes at 1000 points ITM.
Distance is therefore signed relative to the option's own direction; using an unsigned
distance conflates a liquid OTM strike with an illiquid ITM one at the same offset.
"""
import functools

from .. import db

# A strike that traded in fewer than this share of minutes is reported as thin. Not a hard
# rejection -- it is disclosure. The hard gate is volume > 0 in the entry bar itself.
THIN_PCT_MINUTES_TRADED = 60.0


def dte_bucket(dte):
    if dte == 0:
        return "0"
    if dte == 1:
        return "1"
    if dte <= 3:
        return "2-3"
    if dte <= 7:
        return "4-7"
    if dte <= 15:
        return "8-15"
    return "16-45"


def otm_bucket(otm_points):
    """Deliberately NOT clamped to the observed range.

    Clamping looked harmless and was not: a strike 5,000 points out of the money mapped to
    the +2000 bucket and inherited its liquidity, so the engine reported a well-traded
    strike where it had no evidence at all. Returning the true bucket lets the lookup miss,
    and a miss is reported honestly as "never observed" rather than borrowed from the
    nearest cell that happens to exist.
    """
    return int(otm_points // 250) * 250


@functools.lru_cache(maxsize=1)
def _profile():
    return {(r["dte_bucket"], r["otm_bucket"]): r for r in db.rows(
        f"SELECT dte_bucket, otm_bucket, n_contract_days, pct_minutes_traded, "
        f"median_volume_lots, median_price_pts, median_day_volume_lots "
        f"FROM {db.DATABASE}.liquidity_profile")}


def profile(dte, otm_points):
    """Measured liquidity for this (dte, distance) cell, or None if never observed."""
    return _profile().get((dte_bucket(dte), otm_bucket(otm_points)))


def assess(dte, otm_points):
    """(is_thin, note). `is_thin` is disclosure, not rejection."""
    p = profile(dte, otm_points)
    if p is None:
        return True, (f"no contract in the window sat {otm_points:+.0f} points from spot "
                      f"at {dte} DTE; there is no evidence this strike was tradeable")
    pct = p["pct_minutes_traded"]
    where = f"{otm_bucket(otm_points):+d} pts OTM, {dte_bucket(dte)} DTE"
    if pct < THIN_PCT_MINUTES_TRADED:
        return True, (f"thin: strikes at {where} traded in only {pct:.0f} % of minutes "
                      f"(median {p['median_day_volume_lots']:.0f} lots/day). Fills at the "
                      f"modelled price are optimistic")
    return False, (f"strikes at {where} traded in {pct:.0f} % of minutes "
                   f"(median {p['median_day_volume_lots']:.0f} lots/day)")
