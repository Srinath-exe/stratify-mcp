"""Margin model.

Two regimes, and they are not equally trustworthy:

DEFINED-RISK combos (iron condor, iron fly, credit spread, debit spread) -- margin is the
  GREATER of (a) the maximum possible loss on the combo and (b) a live-measured floor
  (MIN_MARGIN_PCT_OF_NOTIONAL below), CAPPED at the combo's own width. (a) alone is NOT
  exact in practice: a live Fyers SPAN check on a SENSEX ATM-long/OTM-short call debit
  spread (the exact shape this structure trades) found real broker margin of
  Rs63,700-66,000 against a max theoretical loss (the debit paid) of only Rs3,700-4,200 --
  SPAN's exposure-margin component charges roughly 16-17x the textbook worst case for a
  position this tight, something no amount of payoff algebra alone predicts.
  weekly_options_research/structures.py hit this the same way (see its 2026-08-19 comment
  on debit_spread()) and applies the identical floor to every defined-risk structure it has.
  Skipping the floor entirely is what produced this engine's own 200-900% CAGR bug on
  debit_spread (DECISIONS.md open action #3).

  The width cap means this does NOT fully close that 16-17x gap for a combo whose own width
  is tight relative to spot (that SENSEX example's width was 1000 pts; the floor alone would
  ask for ~4235 -- capped back down to 1000, still short of what SPAN actually charged). The
  alternative -- letting reported margin exceed a hedged position's own worst-case loss --
  would make return-on-margin structurally nonsensical for every consumer of this number,
  which is a worse failure mode than an understated-but-internally-consistent one. Not
  closing the whole gap is a known, flagged limitation, not an oversight.

  long_option (a single bought leg, no second leg to define a width against) does not
  belong in this floor at all -- margin is exactly the premium paid, unmodified. There is no
  SPAN exposure-margin quirk to correct for on a fully-paid-for long option; the floor logic
  above is specific to multi-leg combos.

NAKED shorts (short strangle, short straddle) -- SPAN margin depends on the exchange's
  volatility scan range for a specific day, and there is no way to retrieve that after the
  fact. So we use a ratio calibrated from Fyers' live SPAN calculator across a grid of
  strike offsets (cache/margin_calibration.json, calibrated 2026-08-19) and apply it
  historically. That is materially better grounded than the 11 %-of-notional rule of thumb
  in weekly_options_research/common.py -- Fyers' own number for a 1-step-OTM NIFTY strangle
  is 13.19 %, not 11 % -- but it is a TODAY-calibrated ratio applied to the PAST.
  It is an approximation and every response that uses it must say so.

Margin widens in a volatility shock exactly when a naked short is losing, so a
today-calibrated ratio understates the capital a real seller would have needed on the
worst days of the window. Return-on-margin from a naked structure is therefore optimistic
by an unknown amount. The defined-risk floor above narrows, but does not close, the
equivalent gap for hedged combos.
"""
import bisect
import functools
import json
import os
from pathlib import Path

CALIBRATION_PATH = Path(os.getenv(
    "STRATIFY_MARGIN_CALIBRATION",
    Path(__file__).resolve().parents[3] /
    "BACKTEST/weekly_options_research/cache/margin_calibration.json"))

NAKED_IS_APPROXIMATE = (
    "Naked-short margin uses a Fyers SPAN ratio calibrated on {calibrated_at} and applied "
    "historically. Real SPAN margin varies with each day's volatility scan range and "
    "widens in a shock, so return-on-margin for naked structures is optimistic.")

# Live-measured floor on defined-risk margin (points-of-notional, per symbol) -- see this
# module's docstring for the Fyers SPAN check that motivated it. Same values as
# weekly_options_research/structures.py's MIN_MARGIN_PCT_OF_NOTIONAL; kept as a separate
# constant here (not imported) so this engine's own test suite pins its own behavior rather
# than silently inheriting a future change to the research copy.
MIN_MARGIN_PCT_OF_NOTIONAL = {"NIFTY": 0.039, "SENSEX": 0.055, "BANKNIFTY": 0.039}
_DEFAULT_MIN_MARGIN_PCT = 0.035

DEFINED_RISK_EXACT = (
    "Margin for this structure is its maximum loss, which is what SPAN recognises for a "
    "fully-paid-for long option. Exact, not approximated.")

DEFINED_RISK_NOTE = (
    "Margin for this structure is the greater of its maximum theoretical loss and a "
    "{floor_pct:.1f}% -of-notional floor measured against live Fyers SPAN margin, capped at "
    "the combo's own width. SPAN's exposure-margin component can charge well above the "
    "textbook max loss for a tight defined-risk combo (measured 16-17x on a SENSEX debit "
    "spread); the floor corrects for that but the width cap means a combo with a tight "
    "width may still be understated relative to real SPAN.")


def _min_margin_pct(symbol):
    return MIN_MARGIN_PCT_OF_NOTIONAL.get(symbol, _DEFAULT_MIN_MARGIN_PCT)


@functools.lru_cache(maxsize=1)
def _calibration():
    with open(CALIBRATION_PATH) as fh:
        raw = json.load(fh)
    meta = raw.pop("_meta", {})
    table = {}
    for symbol, offsets in raw.items():
        pairs = sorted((int(k), v["margin_pct_of_notional"]) for k, v in offsets.items())
        table[symbol] = ([p[0] for p in pairs], [p[1] for p in pairs])
    return table, meta


def calibrated_at():
    return _calibration()[1].get("calibrated_at")


def naked_margin_pct(strike_offset_steps, symbol="NIFTY"):
    """SPAN+exposure as a fraction of notional, for a naked short at this many strike
    steps from ATM. Linearly interpolated between calibrated grid points; clamped at the
    ends rather than extrapolated, because extrapolating a margin curve invents capital
    requirements that were never measured."""
    table, _ = _calibration()
    if symbol not in table:
        raise KeyError(f"no margin calibration for {symbol}")
    xs, ys = table[symbol]
    x = abs(int(strike_offset_steps))
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    i = bisect.bisect_left(xs, x)
    if xs[i] == x:
        return ys[i]
    x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


def margin_points(structure, legs, spot, strike_offset_steps=None, symbol="NIFTY"):
    """Margin blocked, in index points, so it is lot-size invariant.

    `legs` is a list of dicts: {"action": "SELL"|"BUY", "option_type": "CE"|"PE",
    "strike": int, "premium_pts": float}.
    """
    if is_defined_risk(legs):
        raw = max_loss_points(legs)
        width = _width_pts(legs)
        if width is None:
            # A single leg per side (long_option): there is no spread width to speak of,
            # so no floor either -- margin stays exactly the premium paid. Applying a
            # notional-based floor here would be wrong twice over: a bought option's risk
            # really is capped at what was paid (no exposure-margin quirk applies, there is
            # nothing SPAN could charge more for), and test_long_option_margin_is_the_premium
            # pins this exactly.
            return raw, DEFINED_RISK_EXACT
        floor_pct = _min_margin_pct(symbol)
        # The floor corrects for real SPAN charging more than the textbook max loss (see
        # docstring) -- but it must never imply the position could lose MORE than its own
        # structural ceiling (the width), which is a mathematical fact independent of what
        # any broker charges. So the floor is capped at width, not applied unconditionally:
        # it can raise margin above the theoretical max loss, never above the hard cap.
        # This means the floor only partially closes the measured 16-17x gap for a combo
        # whose width itself is tight relative to spot -- the alternative (letting margin
        # exceed width) would make return-on-margin numbers structurally nonsensical, which
        # is worse than an understated-but-sane one. Flagged, not silently accepted.
        pts = min(width, max(raw, floor_pct * spot))
        return pts, DEFINED_RISK_NOTE.format(floor_pct=floor_pct * 100)
    if strike_offset_steps is None:
        raise ValueError("naked structures need strike_offset_steps for the SPAN ratio")
    pct = naked_margin_pct(strike_offset_steps, symbol)
    note = NAKED_IS_APPROXIMATE.format(calibrated_at=calibrated_at())
    return spot * pct * naked_units(legs), note


def naked_units(legs):
    """How many calibrated units of naked risk this position carries.

    THE CALIBRATION'S UNIT IS ONE STRANGLE. Every row of margin_calibration.json prices
    one short call plus one short put against one lot of notional (24078.3 x 65), so
    `spot * pct` is the margin for a 1x1 strangle and nothing else. It was returned
    verbatim for every naked position regardless of size, which meant a hundred short
    calls blocked exactly as much capital as one -- a 0.02-delta 100-lot short reported
    +13,435 points of profit against 2,375 points of margin, a return on margin that is
    pure fiction. Quantity-blind margin is the same bug that once let a ratio spread be
    margined as a capped spread; this is the third place it was hiding.

    MAX OF THE TWO SIDES, not the sum. A short call and a short put cannot both go
    maximally wrong, which is exactly the offset the calibrated strangle already prices
    in; summing them would double-charge a plain strangle and contradict the measurement.

    NET OF COVERING LONGS. A long of the same type covers one short. What this does NOT
    then do is add the covered remainder's own spread margin back on top, so a 3x1 ratio
    is charged for its two uncovered shorts and nothing for the spread underneath them.
    That understates, in the same direction and for the same reason as the naked
    approximation this whole branch is built on, and is flagged rather than hidden.
    """
    units = 0
    for side in ("CE", "PE"):
        shorts = sum(l.get("qty", 1) for l in legs
                     if l["option_type"] == side and l["action"] == "SELL")
        longs = sum(l.get("qty", 1) for l in legs
                    if l["option_type"] == side and l["action"] == "BUY")
        units = max(units, shorts - longs)
    # is_defined_risk() has already returned False to get here, so some side is uncovered.
    return max(1, units)


def _width_pts(legs):
    """Same computation as engine/backtest.py's _width_pts, duplicated rather than
    imported: backtest.py imports this module, so importing back would cycle. None for a
    single leg per side (nothing to measure a spread width against)."""
    ce = [l["strike"] for l in legs if l["option_type"] == "CE"]
    pe = [l["strike"] for l in legs if l["option_type"] == "PE"]
    widths = []
    if len(ce) > 1:
        widths.append(max(ce) - min(ce))
    if len(pe) > 1:
        widths.append(max(pe) - min(pe))
    return max(widths) if widths else None


def is_defined_risk(legs):
    """A structure is defined-risk if every short leg is covered by a long leg of the same
    option type -- that is what caps the loss.

    COUNTED IN QUANTITY, not in legs. `qty` defaults to 1, so every existing caller is
    unaffected; without it a ratio spread (sell 2 calls, buy 1) counts one short and one
    long and is declared defined-risk, which is exactly backwards -- the whole point of a
    ratio is the uncovered short, and margining it as a spread would report a capped loss
    on a position with none.
    """
    for side in ("CE", "PE"):
        shorts = sum(l.get("qty", 1) for l in legs
                     if l["option_type"] == side and l["action"] == "SELL")
        longs = sum(l.get("qty", 1) for l in legs
                    if l["option_type"] == side and l["action"] == "BUY")
        if shorts > longs:
            return False
    return True


def max_loss_points(legs):
    """Worst outcome across every strike boundary. Evaluating only at the listed strikes
    is sufficient: the payoff is piecewise linear and every kink sits on a strike."""
    net_credit = sum((1 if l["action"] == "SELL" else -1) * l.get("qty", 1)
                     * l["premium_pts"] for l in legs)
    boundaries = sorted({l["strike"] for l in legs})
    worst = 0.0
    for s in boundaries:
        payoff = net_credit
        for l in legs:
            intrinsic = (max(0.0, s - l["strike"]) if l["option_type"] == "CE"
                         else max(0.0, l["strike"] - s))
            payoff += (-1 if l["action"] == "SELL" else 1) * l.get("qty", 1) * intrinsic
        worst = min(worst, payoff)
    return abs(worst)
