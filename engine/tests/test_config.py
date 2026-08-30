"""Config-layer tests. The important one is test_matches_production_charges: it asserts
the ported cost model is byte-identical to the function paper trading actually uses, so
the port cannot drift away from production without a test going red."""
import datetime as dt
import importlib.util
import sys
import os
from pathlib import Path

import pytest

# The directory holding engine/ -- i.e. this checkout, wherever it is cloned.
# Was `parents[3] / "stratify_mcp"`, which resolved to a SIBLING directory of that
# name and silently tested a different copy of the code from any other layout.
PKG = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PKG))

from engine.config import charges, contracts, liquidity, margin, slippage  # noqa: E402


def _incumbent(rel):
    """Path to a file in the PRIVATE research/live repos that a parity test compares against.

    These tests are the load-bearing ones -- they assert this engine agrees with the code
    that actually trades -- but the incumbents are not part of this repository. Skip
    cleanly when they are absent instead of failing with FileNotFoundError, and let anyone
    who does have them point at the checkout with STRATIFY_INCUMBENT_ROOT.
    """
    root = Path(os.getenv("STRATIFY_INCUMBENT_ROOT", str(PKG.parent)))
    path = root / rel
    if not path.exists():
        pytest.skip(f"incumbent not present: {rel} (set STRATIFY_INCUMBENT_ROOT to compare)")
    return path



def _production_charges():
    """Load weekly_options_research/charges.py directly -- it is the incumbent."""
    path = _incumbent("BACKTEST/weekly_options_research/charges.py")
    spec = importlib.util.spec_from_file_location("prod_charges", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- charges

@pytest.mark.parametrize("entry,exit_,legs", [
    (100.0, 40.0, 2), (250.0, 0.0, 2), (60.0, 0.04, 2),
    (300.0, 500.0, 4), (12.5, 12.5, 4), (0.0, 0.0, 2),
])
@pytest.mark.parametrize("symbol,lot", [("NIFTY", 65), ("NIFTY", 75), ("SENSEX", 20)])
def test_matches_production_charges(entry, exit_, legs, symbol, lot):
    """Credit structures must cost exactly what production says they cost."""
    prod = _production_charges()
    assert charges.round_trip(entry, exit_, lot, 1, symbol, n_legs=legs).total == \
        pytest.approx(prod.two_leg_charges(entry, exit_, lot, 1, symbol, n_legs=legs))


def test_rates_are_unchanged():
    prod = _production_charges()
    assert charges.BROKERAGE_PER_ORDER == prod.BROKERAGE_PER_ORDER
    assert charges.STT_RATE == prod.STT_RATE
    assert charges.EXCHANGE_TXN_RATE == prod.EXCHANGE_TXN_RATE
    assert charges.SEBI_RATE == prod.SEBI_RATE
    assert charges.GST_RATE == prod.GST_RATE
    assert charges.STAMP_DUTY_RATE == prod.STAMP_DUTY_RATE
    assert charges.WORTHLESS_THRESHOLD_PTS == prod.WORTHLESS_THRESHOLD_PTS


def test_debit_entry_pays_stt_on_the_sell_not_the_buy():
    """The reason this module exists. A long option buys to open and sells to close, so
    STT falls on the EXIT, and stamp duty on the entry."""
    c = charges.round_trip(100.0, 140.0, 65, 1, "NIFTY", n_legs=1, entry_is_sell=False)
    assert c.stt == pytest.approx(140.0 * 65 * charges.STT_RATE)
    assert c.stamp_duty == pytest.approx(100.0 * 65 * charges.STAMP_DUTY_RATE)


def test_worthless_exit_incurs_no_exit_charge():
    c = charges.round_trip(80.0, 0.04, 65, 1, "NIFTY", n_legs=2)
    assert c.brokerage == charges.BROKERAGE_PER_ORDER * 2
    assert c.stamp_duty == 0.0


# ---------------------------------------------------------------- contracts

def test_lot_size_changes_inside_the_window():
    """75 through 2025-12-30, 65 from 2026-01-06. A flat constant is a 15.4 % error."""
    assert contracts.lot_size(dt.date(2025, 11, 25)) == 75
    assert contracts.lot_size(dt.date(2025, 12, 30)) == 75
    assert contracts.lot_size(dt.date(2026, 1, 6)) == 65
    assert contracts.lot_size(dt.date(2026, 6, 30)) == 65


def test_unknown_expiry_refuses_to_guess():
    with pytest.raises(contracts.UnknownExpiry):
        contracts.lot_size(dt.date(2030, 1, 1))


def test_strike_selection_tie_breaks_further_otm():
    """Target 24025 sits exactly between 24000 and 24050."""
    expiry = dt.date(2026, 1, 27)
    spot = 24025.0
    assert contracts.select_strike(spot, 0.0, "CE", expiry) == 24050
    assert contracts.select_strike(spot, 0.0, "PE", expiry) == 24000


def test_strike_selection_is_deterministic():
    expiry = dt.date(2026, 1, 27)
    for _ in range(50):
        assert contracts.select_strike(24025.0, 0.0, "CE", expiry) == 24050


# ---------------------------------------------------------------- margin

def test_defined_risk_margin_is_max_loss_when_that_clears_the_floor():
    """A 2000-wide condor's 163-pt max loss is dwarfed by its own width -- but 163 pts on a
    24000 spot is only 0.68% of notional, well under NIFTY's 3.9% live-measured floor, so
    the correct margin is the floor, not the raw max loss. Widen the combo so max loss
    itself clears the floor, to isolate "is max-loss picked up at all" from "is the floor
    applied" (see the floor-binding case below)."""
    legs = [{"action": "SELL", "option_type": "CE", "strike": 25000, "premium_pts": 40},
            {"action": "BUY",  "option_type": "CE", "strike": 27500, "premium_pts": 20},
            {"action": "SELL", "option_type": "PE", "strike": 23000, "premium_pts": 35},
            {"action": "BUY",  "option_type": "PE", "strike": 20500, "premium_pts": 18}]
    pts, note = margin.margin_points("iron_condor", legs, 24000)
    assert pts == pytest.approx(2463.0)  # width 2500 - net credit 37
    assert "maximum theoretical loss" in note


def test_defined_risk_margin_floors_a_tight_debit_spread_but_never_past_its_width():
    """Regression test for DECISIONS.md open action #3: a live Fyers SPAN check on a SENSEX
    ATM-long/OTM-short call debit spread (this exact leg shape) found real broker margin of
    Rs63,700-66,000 against a Rs3,700-4,200 debit paid. Before this fix, margin_points()
    returned the raw 70-point max loss here, producing the 200-900% CAGR bug that held
    debit_spread back from the public spec.

    The floor alone (5.5% of 77000 = 4235 pts) would ask for more than this combo can
    structurally lose -- its width is only 1000 pts (78000-77000) -- so the floor is capped
    at width: the fix raises margin well above the 70-pt raw max loss, but not all the way
    to what SPAN actually charged. That gap is real and stated in the module docstring, not
    hidden."""
    legs = [{"action": "BUY",  "option_type": "CE", "strike": 77000, "premium_pts": 220},
            {"action": "SELL", "option_type": "CE", "strike": 78000, "premium_pts": 150}]
    pts, note = margin.margin_points("debit_spread", legs, 77000, symbol="SENSEX")
    raw_max_loss = margin.max_loss_points(legs)
    width = margin._width_pts(legs)
    assert raw_max_loss == pytest.approx(70.0)
    assert width == pytest.approx(1000.0)
    assert pts == pytest.approx(1000.0)  # width caps the 4235-pt floor down to 1000
    assert pts > raw_max_loss * 14  # still a large, deliberate correction vs the raw max loss
    assert "16-17x" in note
    assert "capped" in note


def test_defined_risk_margin_never_exceeds_its_own_width():
    """The floor must never claim a hedged combo could lose more than its structural
    ceiling -- that would make return-on-margin numbers nonsensical, not just approximate."""
    legs = [{"action": "BUY",  "option_type": "CE", "strike": 24000, "premium_pts": 5},
            {"action": "SELL", "option_type": "CE", "strike": 24050, "premium_pts": 3}]
    pts, _ = margin.margin_points("debit_spread", legs, 24000, symbol="NIFTY")
    assert pts <= 50.0 + 1e-6  # width


def test_long_option_margin_has_no_floor():
    """A single bought leg has no width to floor against and no SPAN exposure-margin quirk
    to correct for -- margin must stay exactly the premium paid, unmodified."""
    legs = [{"action": "BUY", "option_type": "CE", "strike": 24000, "premium_pts": 45}]
    pts, note = margin.margin_points("long_option", legs, 24000, symbol="SENSEX")
    assert pts == pytest.approx(45.0)
    assert "Exact" in note


def test_naked_margin_is_flagged_approximate():
    legs = [{"action": "SELL", "option_type": "CE", "strike": 24500, "premium_pts": 40},
            {"action": "SELL", "option_type": "PE", "strike": 23500, "premium_pts": 35}]
    pts, note = margin.margin_points("short_strangle", legs, 24000, strike_offset_steps=10)
    assert pts > 0
    assert "optimistic" in note


def test_margin_curve_is_clamped_not_extrapolated():
    far = margin.naked_margin_pct(999)
    assert far == margin.naked_margin_pct(30)


def test_naked_margin_falls_as_the_strike_moves_out():
    assert margin.naked_margin_pct(1) > margin.naked_margin_pct(10) > margin.naked_margin_pct(30)


# ---------------------------------------------------------------- slippage

def test_slippage_never_below_half_a_tick():
    assert slippage.leg_slippage_pts(100, override_pts=0.0) == slippage.MIN_HALF_SPREAD_PTS
    assert min(v for _, v in slippage._ASSUMED_HALF_SPREAD_PTS) >= slippage.MIN_HALF_SPREAD_PTS


def test_slippage_widens_with_distance():
    assert (slippage.assumed_half_spread_pts(100)
            < slippage.assumed_half_spread_pts(1000)
            < slippage.assumed_half_spread_pts(3000))


def test_measured_slippage_reports_insufficiency_rather_than_guessing():
    value, note = slippage.measured_half_spread_pts("NIFTY")
    assert value is None or isinstance(value, float)
    if value is None:
        assert "need" in note or "no paper-trading book" in note


# ---------------------------------------------------------------- liquidity

def test_itm_strikes_are_reported_thin():
    thin, note = liquidity.assess(dte=5, otm_points=-1000)
    assert thin and "thin" in note


def test_near_money_weeklies_are_liquid():
    thin, _ = liquidity.assess(dte=5, otm_points=500)
    assert not thin


def test_unobserved_cell_is_not_silently_liquid():
    thin, note = liquidity.assess(dte=5, otm_points=99999)
    assert thin


def test_naked_margin_scales_with_the_size_of_the_naked_position():
    """The calibration prices ONE strangle -- one short call plus one short put against one
    lot of notional. It was returned verbatim whatever the size, so a hundred short calls
    blocked the same capital as one and return on margin was inflated a hundredfold."""
    def leg(action, otype, strike, qty=1, premium=50.0):
        return {"action": action, "option_type": otype, "strike": strike, "qty": qty,
                "premium_pts": premium}

    one, _ = margin.margin_points(None, [leg("SELL", "CE", 24200), leg("SELL", "PE", 23800)],
                                  24000.0, strike_offset_steps=4)
    hundred, _ = margin.margin_points(None, [leg("SELL", "CE", 24200, qty=100)],
                                      24000.0, strike_offset_steps=4)
    assert hundred == pytest.approx(one * 100)
    # max of the two sides, not the sum: a plain strangle stays exactly the calibrated unit
    assert margin.naked_units([leg("SELL", "CE", 24200), leg("SELL", "PE", 23800)]) == 1
    # a covering long takes one short off the naked count
    assert margin.naked_units([leg("SELL", "CE", 24200, qty=3),
                               leg("BUY", "CE", 24600)]) == 2
    # unequal sides charge the larger one
    assert margin.naked_units([leg("SELL", "CE", 24200, qty=3),
                               leg("SELL", "PE", 23800, qty=5)]) == 5
