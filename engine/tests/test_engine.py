"""Engine tests.

The load-bearing one is test_exit_trigger_matches_production: it lifts
check_exit_trigger() out of paper_trading/engine.py by AST and asserts our port agrees
with it across a grid. A stop-loss rule that means something subtly different in the
backtest than in the live system is the single most damaging bug this engine could ship,
and it is exactly the shape of the 53 % discrepancy that "exit at 3pm" caused earlier.
"""
import ast
import datetime as dt
import sys
import os
from pathlib import Path

import pytest

# The directory holding engine/ -- i.e. this checkout, wherever it is cloned.
# Was `parents[3] / "stratify_mcp"`, which resolved to a SIBLING directory of that
# name and silently tested a different copy of the code from any other layout.
PKG = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PKG))

from engine import backtest, db, spec as spec_mod          # noqa: E402
from engine.config import charges as charges_mod           # noqa: E402


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



# ---------------------------------------------------------------- spec validation

def test_debit_spread_is_refused_with_a_reason():
    with pytest.raises(spec_mod.SpecError, match="margin-sizing bug"):
        spec_mod.parse({"structure": "debit_spread", "params": {}})


def test_unknown_param_is_an_error_not_ignored():
    with pytest.raises(spec_mod.SpecError, match="does not take"):
        spec_mod.parse({"structure": "short_strangle",
                        "params": {"pct_offset": 1.5, "tp_pct": 0.5}})


def test_non_nifty_symbol_refused():
    with pytest.raises(spec_mod.SpecError, match="NIFTY only"):
        spec_mod.parse({"structure": "iron_fly", "symbol": "BANKNIFTY",
                        "params": {"pct_width": 1.0}})


def test_window_outside_the_tier_is_refused():
    """The window is a tier property, so the refusal must name the caller's tier window,
    not a global constant."""
    with pytest.raises(spec_mod.SpecError, match="your tier serves"):
        spec_mod.parse({"structure": "iron_fly", "params": {"pct_width": 1.0},
                        "period": {"from": "2019-01-01", "to": "2026-06-30"}}, tier="free")


def test_a_paid_tier_may_reach_further_back():
    sp = spec_mod.parse({"structure": "iron_fly", "params": {"pct_width": 1.0},
                         "period": {"from": "2019-06-01", "to": "2026-06-30"}}, tier="pro")
    assert sp.date_from.year == 2019


def test_free_tier_cannot_borrow_the_paid_window():
    with pytest.raises(spec_mod.SpecError):
        spec_mod.parse({"structure": "iron_fly", "params": {"pct_width": 1.0},
                        "period": {"from": "2019-06-01", "to": "2026-06-30"}}, tier="free")


def test_narrow_period_hits_the_anti_oracle_floor():
    with pytest.raises(spec_mod.SpecError, match="at least 20"):
        spec_mod.parse({"structure": "iron_fly", "params": {"pct_width": 1.0},
                        "period": {"from": "2026-01-05", "to": "2026-01-12"}})


def test_coverage_floor_rejects_a_thin_run():
    """check_coverage()'s params are scan-basis now (decision D1 open action #8, resolved
    2026-08-25), not trade-basis -- renamed to match so a caller can't pass the wrong count
    by accident."""
    with pytest.raises(spec_mod.SpecError, match="distinct contracts"):
        spec_mod.check_coverage(n_contracts_scanned=4, n_days_scanned=40)
    with pytest.raises(spec_mod.SpecError, match="candidate days"):
        spec_mod.check_coverage(n_contracts_scanned=100, n_days_scanned=3)


def test_coverage_floor_is_measured_on_scans_not_trades():
    """The exact false positive DECISIONS.md's open action #8 describes: 'a credit_spread
    with a donchian bias over the full year trades on only 15 days, because the bias reads
    neutral most weeks -- and is refused.' A donchian bias over a full year is a legitimate
    selective strategy, not an oracle attempt -- the underlying spec still scans a full
    year of real chain data every cycle regardless of what the bias decides, so the floor
    (measured on scans, not trades) must not reject it just because most cycles read
    neutral."""
    from engine import backtest
    r = backtest.run({"structure": "credit_spread", "symbol": "NIFTY",
                      "params": {"pct_offset": 1.5, "pct_width": 1.0},
                      "bias": "donchian", "entry_time": "09:30",
                      "period": {"from": "2025-07-01", "to": "2026-06-30"}})
    # Confirms this really is the thin-trading-days shape the fix targets -- if it traded
    # on plenty of days, the old trade-based floor would never have been in danger of
    # tripping here and this test would prove nothing about the fix.
    assert r.n_trading_days < spec_mod.MIN_TRADING_DAYS


def test_eod_is_1529_not_1500():
    """The 53 % discrepancy came from these two meaning the same thing."""
    assert spec_mod.EOD_MINUTE == 15 * 60 + 29
    assert spec_mod.parse({"structure": "iron_fly", "params": {"pct_width": 1.0},
                           "entry_time": "15:00"}).entry_minute == 15 * 60


# ---------------------------------------------------------------- exit semantics

def _production_check_exit_trigger():
    """Lift check_exit_trigger out of paper_trading/engine.py without importing the module
    (which pulls in market data clients). The function is pure arithmetic on a dict."""
    src = _incumbent("paper_trading/engine.py").read_text()
    tree = ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "check_exit_trigger")
    ns = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "<prod>", "exec"), ns)
    return ns["check_exit_trigger"]


EXIT_GRID = [
    ("short_strangle", {"sl_mult": 2.0}, 40.0, None),
    ("short_strangle", {"sl_mult": 0.3}, 40.0, None),
    ("short_strangle", {}, 40.0, None),
    ("credit_spread", {"sl_mult": 1.5, "tp_pct": 0.5}, 30.0, 200.0),
    ("credit_spread", {"sl_mult": 1.5}, 30.0, 200.0),
    ("credit_spread", {"tp_pct": 0.8}, 30.0, 200.0),
    ("iron_condor", {"sl_mult": 1.0}, 25.0, 200.0),
    ("iron_fly", {"tp_pct": 0.5}, 90.0, 300.0),
]


@pytest.mark.parametrize("structure,params,credit,width", EXIT_GRID)
def test_exit_trigger_matches_production(structure, params, credit, width):
    prod = _production_check_exit_trigger()
    sp = spec_mod.StrategySpec(structure=structure, params=params)
    for step in range(-400, 401, 7):
        exit_value = step / 2.0
        ours = backtest._exit_trigger(sp, credit, width, exit_value)
        theirs_fired, theirs_reason = prod(structure, params, credit, width, exit_value)
        assert (ours is not None) == theirs_fired, (structure, exit_value)
        assert ours == (theirs_reason if theirs_fired else None), (structure, exit_value)


def test_condor_and_fly_hold_to_expiry():
    """Matching structures.py: no SL/TP branch exists for these, so none may fire."""
    for structure in ("iron_condor", "iron_fly"):
        sp = spec_mod.StrategySpec(structure=structure,
                                   params={"sl_mult": 0.01, "tp_pct": 0.01})
        for v in (-1e6, -100.0, 0.0, 100.0, 1e6):
            assert backtest._exit_trigger(sp, 30.0, 200.0, v) is None


def test_long_option_stop_is_a_fraction_of_premium_paid():
    sp = spec_mod.StrategySpec(structure="long_option",
                               params={"pct_offset": 0.0, "direction": "CE",
                                       "sl_pct": 0.5, "tp_pct": 1.0})
    credit = -100.0                      # paid 100
    assert backtest._exit_trigger(sp, credit, None, 51.0) is None
    assert backtest._exit_trigger(sp, credit, None, 50.0) == "SL"
    assert backtest._exit_trigger(sp, credit, None, 199.0) is None
    assert backtest._exit_trigger(sp, credit, None, 200.0) == "TP"


# ---------------------------------------------------------------- sign conventions

def test_net_value_sign_convention():
    legs = [{"option_type": "CE", "action": "SELL", "strike": 25000},
            {"option_type": "CE", "action": "BUY", "strike": 25200}]
    prices = {("CE", 25000): 50.0, ("CE", 25200): 20.0}
    assert backtest._net_value(legs, prices) == pytest.approx(30.0)


def test_intrinsic_at_settlement():
    legs = [{"option_type": "CE", "action": "SELL", "strike": 25000},
            {"option_type": "PE", "action": "SELL", "strike": 24000}]
    px = backtest._intrinsic(legs, 25150.0)
    assert px[("CE", 25000)] == pytest.approx(150.0)
    assert px[("PE", 24000)] == pytest.approx(0.0)


def test_nearest_listed_tie_breaks_toward_further_otm():
    strikes = [24000, 24050]
    assert backtest._nearest_listed(strikes, 24025, prefer_higher=True) == 24050
    assert backtest._nearest_listed(strikes, 24025, prefer_higher=False) == 24000


def test_width_is_the_wider_wing():
    legs = [{"option_type": "CE", "action": "SELL", "strike": 25000},
            {"option_type": "CE", "action": "BUY", "strike": 25300},
            {"option_type": "PE", "action": "SELL", "strike": 24000},
            {"option_type": "PE", "action": "BUY", "strike": 23800}]
    assert backtest._width_pts(legs) == 300


# ---------------------------------------------------------------- end to end

STRANGLE = {"structure": "short_strangle", "symbol": "NIFTY",
            "params": {"pct_offset": 1.5, "sl_mult": 2.0, "entry_dte": 4},
            "entry_time": "09:30"}


@pytest.fixture(scope="module")
def strangle_result():
    return backtest.run(STRANGLE)


def test_runs_a_full_year(strangle_result):
    assert len(strangle_result.trades) > 40
    assert strangle_result.n_trading_days > 40
    assert strangle_result.n_contracts >= spec_mod.MIN_DISTINCT_CONTRACTS


def test_pnl_reconciles_to_the_convention(strangle_result):
    for t in strangle_result.trades:
        assert t.pnl_pts == pytest.approx(
            t.entry_credit_pts + t.exit_value_pts - t.slippage_pts)


def test_rupee_pnl_uses_that_expiry_lot_size(strangle_result):
    """The whole point of contract_spec: 75 before 2026-01-06, 65 after."""
    lots = {t.expiry: t.lot_size for t in strangle_result.trades}
    assert any(v == 75 for v in lots.values())
    assert any(v == 65 for v in lots.values())
    for t in strangle_result.trades:
        assert t.pnl_rupees == pytest.approx(
            t.pnl_pts * t.lot_size - t.charges_rupees)


def test_charges_are_always_a_cost(strangle_result):
    assert all(t.charges_rupees > 0 for t in strangle_result.trades)


def test_deterministic(strangle_result):
    again = backtest.run(STRANGLE)
    assert [t.pnl_pts for t in again.trades] == \
           [t.pnl_pts for t in strangle_result.trades]
    assert [t.legs[0]["strike"] for t in again.trades] == \
           [t.legs[0]["strike"] for t in strangle_result.trades]


def test_disclosures_are_attached(strangle_result):
    joined = " ".join(strangle_result.warnings)
    assert "Deep-ITM assignment" in joined
    assert "modelled, not measured" in joined


def test_a_gate_actually_removes_cycles(strangle_result):
    gated = backtest.run(dict(STRANGLE, gate="avoid_shock"))
    assert len(gated.trades) < len(strangle_result.trades)
    assert any("skipped by gate" in n for n in gated.notes)


def test_bias_on_a_neutral_structure_is_refused_not_ignored():
    """A bias cannot steer a strangle, so accepting one would be a silent no-op."""
    with pytest.raises(spec_mod.SpecError, match="direction-neutral"):
        backtest.run(dict(STRANGLE, bias="ema_trend"))


def test_direction_and_bias_together_are_refused():
    with pytest.raises(spec_mod.SpecError, match="contradict"):
        backtest.run({"structure": "credit_spread", "entry_time": "09:30",
                      "params": {"pct_offset": 1.0, "pct_width": 1.0, "direction": "PE"},
                      "bias": "ema_trend"})


def test_bias_chooses_the_side_per_cycle():
    r = backtest.run({"structure": "credit_spread", "entry_time": "09:30", "bias": "ema_trend",
                      "params": {"pct_offset": 1.0, "pct_width": 1.0, "entry_dte": 4}})
    sides = {t.legs[0]["option_type"] for t in r.trades}
    assert sides == {"CE", "PE"}          # a fixed direction could never produce both


def test_signals_frame_has_no_lookahead():
    """A 09:30 entry must not see the day's later high, low or close."""
    from engine import signals
    for r in backtest.run(STRANGLE).trades[:8]:
        frame = signals.frame_at(r.entry_date, 570)
        today = frame[frame["date"] == r.entry_date]
        if today.empty:
            continue
        full = db.rows(
            "SELECT max(high) h, min(low) l, argMax(close, timestamp) c "
            "FROM stratify.spot_1min WHERE toDate(timestamp) = %(d)s",
            {"d": r.entry_date})[0]
        assert today["high"].iloc[0] <= full["h"] + 1e-6
        assert today["low"].iloc[0] >= full["l"] - 1e-6


@pytest.mark.parametrize("structure,params", [
    ("iron_condor", {"pct_offset": 1.5, "pct_width": 1.0, "entry_dte": 4}),
    ("iron_fly", {"pct_width": 1.5, "entry_dte": 4}),
    ("credit_spread", {"pct_offset": 1.0, "pct_width": 1.0, "direction": "PE",
                       "sl_mult": 1.5, "tp_pct": 0.5, "entry_dte": 4}),
    ("long_option", {"pct_offset": 0.5, "direction": "CE", "sl_pct": 0.5,
                     "tp_pct": 1.0, "entry_dte": 4}),
])
def test_every_v1_structure_runs(structure, params):
    r = backtest.run({"structure": structure, "params": params, "entry_time": "09:30"})
    assert len(r.trades) > 40
    n_legs = spec_mod.StrategySpec(structure=structure, params=params).n_legs
    assert all(len(t.legs) == n_legs for t in r.trades)


def test_defined_risk_margin_never_exceeds_width(strangle_result):
    r = backtest.run({"structure": "iron_condor",
                      "params": {"pct_offset": 1.5, "pct_width": 1.0, "entry_dte": 4},
                      "entry_time": "09:30"})
    for t in r.trades:
        assert t.margin_pts <= backtest._width_pts(t.legs) + 1e-6


def test_long_option_margin_is_the_premium():
    r = backtest.run({"structure": "long_option",
                      "params": {"pct_offset": 0.5, "direction": "CE", "entry_dte": 4},
                      "entry_time": "09:30"})
    for t in r.trades:
        assert t.margin_pts == pytest.approx(-t.entry_credit_pts)


def test_long_option_charges_match_production():
    """structures._long_option_charges exists precisely because two_leg_charges has the
    wrong buy/sell convention for a bought option. Our direction-aware model must equal
    production's dedicated function, not its default one."""
    src = _incumbent("BACKTEST/weekly_options_research/structures.py").read_text()
    tree = ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "_long_option_charges")
    ns = {"chg": charges_mod}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "<prod>", "exec"), ns)
    prod = ns["_long_option_charges"]
    for paid, received in [(100.0, 140.0), (12.0, 0.0), (55.5, 55.5), (300.0, 0.04)]:
        ours = charges_mod.round_trip(paid, received, 65, 1, "NIFTY",
                                      n_legs=1, entry_is_sell=False)
        assert ours.total == pytest.approx(prod(paid, received, 65, 1, "NIFTY"))


# ---------------------------------------------------------------- intraday cadence
#
# v1 could only express "one entry per weekly expiry, held to a stop or to settlement".
# A user asking for "buy at 11, sell at 2, every day" was told it was not expressible --
# which was true, and was a limitation of the cycle model rather than of the data. These
# pin the two things that changed: WHEN a position may open, and WHEN it must close.

INTRADAY = {"structure": "long_option", "symbol": "NIFTY",
            "params": {"pct_offset": 0.5, "direction": "CE"},
            "entry_time": "11:00", "exit_time": "14:00", "cadence": "daily"}


@pytest.fixture(scope="module")
def intraday_result():
    return backtest.run(INTRADAY)


def test_daily_cadence_trades_every_session_not_every_expiry(intraday_result):
    """58 expiries in this window, ~246 trading days. A daily cadence has to land near the
    second number; landing near the first would mean it silently stayed weekly."""
    assert len(intraday_result.trades) > 200
    assert intraday_result.n_trading_days > 200


def test_a_timed_exit_closes_the_same_session(intraday_result):
    for t in intraday_result.trades:
        assert t.exit_ts.date() == t.entry_ts.date(), "position survived its own session"
        assert t.exit_ts.hour * 60 + t.exit_ts.minute == 840   # 14:00
        assert t.exit_reason in ("TIME", "SL", "TP")
        assert t.exit_reason != "EXPIRY", "a squared-off position cannot reach settlement"


def test_a_timed_exit_charges_two_crossings_not_one(intraday_result):
    """A settlement crosses no spread; a 14:00 square-off crosses one. Charging a timed
    exit like a settlement would hand every intraday strategy half a leg of free edge."""
    for t in intraday_result.trades[:20]:
        per_leg = sum(backtest.slippage.leg_slippage_pts(l["otm_points"]) for l in t.legs)
        assert t.slippage_pts == pytest.approx(2.0 * per_leg)


def test_a_stop_after_the_square_off_does_not_fire():
    """The bound that makes a timed exit honest. Without it the SQL would still find the
    first stop anywhere in the contract's remaining life -- including hours or days after
    the position was closed -- and report it as the exit. That is lookahead, and it would
    make every stopped-out intraday trade worse or better than it really was.

    A stop set absurdly tight fires almost immediately; the assertion is not that it fired
    but that whenever it did, it did so inside the holding window.
    """
    spec = dict(INTRADAY)
    spec["params"] = dict(spec["params"], sl_pct=0.05)
    result = backtest.run(spec)
    entry_m, exit_m = 11 * 60, 14 * 60
    for t in result.trades:
        m = t.exit_ts.hour * 60 + t.exit_ts.minute
        assert t.exit_ts.date() == t.entry_ts.date()
        assert entry_m < m <= exit_m, f"exit at {t.exit_ts} is outside the holding window"
    assert any(t.exit_reason == "SL" for t in result.trades), "the tight stop never fired"


def test_eod_exit_on_expiry_day_settles_rather_than_reading_the_last_tick():
    """'EOD' is 15:29. On a contract expiring that day the position does not TRADE out at
    15:29 -- it settles, and NSE settles against the mean of the index over the final 30
    minutes. Reading the option's last print instead would quietly change the strategy,
    and on an illiquid far strike that print can be minutes stale and far from intrinsic.
    """
    result = backtest.run({
        "structure": "long_option", "symbol": "NIFTY",
        "params": {"pct_offset": 0.5, "direction": "CE"},
        "entry_time": "11:00", "exit_time": "EOD", "cadence": "daily", "max_dte": 0})
    assert result.trades
    for t in result.trades:
        assert t.entry_date == t.expiry
        assert t.exit_reason in ("EXPIRY", "SL", "TP")


def test_max_dte_restricts_which_sessions_qualify():
    result = backtest.run({
        "structure": "long_option", "symbol": "NIFTY",
        "params": {"pct_offset": 0.5, "direction": "CE"},
        "entry_time": "11:00", "exit_time": "14:00", "cadence": "daily", "max_dte": 1})
    assert result.trades
    assert max(t.dte for t in result.trades) <= 1


def test_weekly_cadence_is_untouched_by_any_of_this(strangle_result):
    """The default has to mean exactly what it meant before: one entry per expiry, held
    to a stop or to settlement."""
    assert len({t.expiry for t in strangle_result.trades}) == len(strangle_result.trades)
    assert all(t.exit_reason in ("SL", "TP", "EXPIRY") for t in strangle_result.trades)


def test_daily_cadence_includes_the_budget_sunday():
    """2026-02-01 is a SUNDAY on which NSE ran a full session -- the Union Budget special.
    A weekly cadence never sees it because no contract expires on a Sunday. A daily one
    enters on it, so it is a real trading day here and the calendar must not filter it out
    on the assumption that markets close at the weekend.
    """
    cycles = backtest._cycles(spec_mod.parse(
        dict(INTRADAY, period={"from": "2026-01-15", "to": "2026-02-15"})))
    days = {c["trade_date"] for c in cycles}
    assert dt.date(2026, 2, 1) in days


# ---------------------------------------------------------------- margin integrity
#
# Two defects found by auditing "does every trade carry a margin and charges". Charges were
# always present. Margin was not, and the way it failed was worse than a missing number: a
# structure could be built whose legs cancelled, producing a position that did not exist
# but still reported P&L.

MARGIN_CASES = [
    ("short_strangle", {"pct_offset": 1.5, "sl_mult": 2.0}),
    ("credit_spread", {"pct_offset": 1.0, "pct_width": 1.0, "sl_mult": 1.5, "direction": "CE"}),
    ("iron_condor", {"pct_offset": 1.5, "pct_width": 1.0}),
    ("iron_fly", {"pct_width": 1.5}),
    ("long_option", {"pct_offset": 0.5, "direction": "CE"}),
]


@pytest.mark.parametrize("structure,params", MARGIN_CASES)
@pytest.mark.parametrize("cadence", ["weekly", "daily"])
def test_every_trade_carries_a_positive_margin_and_charge(structure, params, cadence):
    """Margin is the denominator of return-on-margin and charges are the whole point of a
    net result. A zero in either is not a rounding issue: metrics.py drops margin<=0 trades
    from the ROM mean WITHOUT saying so, so a single zero silently changes what the
    headline ratio was averaged over."""
    spec = {"structure": structure, "params": dict(params), "entry_time": "11:00"}
    if cadence == "daily":
        spec.update(cadence="daily", exit_time="14:00")
    else:
        spec["params"]["entry_dte"] = 4
    result = backtest.run(spec)
    assert result.trades
    for t in result.trades:
        assert t.margin_pts > 0, f"{structure} {t.entry_ts} has no margin"
        assert t.charges_rupees > 0, f"{structure} {t.entry_ts} paid no charges"


@pytest.mark.parametrize("structure,params", MARGIN_CASES)
@pytest.mark.parametrize("cadence", ["weekly", "daily"])
def test_no_structure_contains_a_self_cancelling_leg_pair(structure, params, cadence):
    """A SELL and a BUY of the same option type on the SAME strike contribute no premium,
    no risk and no margin. The generic dedup does not catch it -- its key includes `action`,
    so the two legs are distinct entries and both survive.

    Observed before the guard: an iron condor whose credit, max loss and margin were all
    exactly zero, and an iron fly with a flat PE side that was really a deep-ITM CE credit
    spread and reported 100.6 % return on margin in one trade.
    """
    spec = {"structure": structure, "params": dict(params), "entry_time": "11:00"}
    if cadence == "daily":
        spec.update(cadence="daily", exit_time="14:00")
    else:
        spec["params"]["entry_dte"] = 4
    for t in backtest.run(spec).trades:
        for side in ("CE", "PE"):
            sells = {l["strike"] for l in t.legs
                     if l["option_type"] == side and l["action"] == "SELL"}
            buys = {l["strike"] for l in t.legs
                    if l["option_type"] == side and l["action"] == "BUY"}
            assert not (sells & buys), f"{structure} {t.entry_ts}: {side} legs cancel"


def test_offsetting_pair_detector_is_not_fooled_by_the_action_key():
    """The regression in one assertion: these two legs are a distinct (type, strike,
    action) pair, so the generic dedup passes them, and they still cancel."""
    legs = [{"option_type": "CE", "strike": 25150, "action": "SELL", "premium_pts": 0.4},
            {"option_type": "CE", "strike": 25150, "action": "BUY", "premium_pts": 0.4}]
    assert len({(l["option_type"], l["strike"], l["action"]) for l in legs}) == len(legs)
    assert backtest._has_offsetting_pair(legs) is True


def test_atm_must_be_near_spot_or_the_cycle_is_skipped():
    """Every structure here is positioned relative to ATM, and `offset_steps` -- which
    picks the naked-short SPAN ratio off a clamped calibration grid -- is measured from it.
    A minute too thin to contain the real ATM does not give a worse fill, it gives a
    different strategy: measured drifts reached 33 strike steps, 1,650 index points."""
    step = 50.0
    spot = 25831.35
    chain = {"CE": {25450: 433.8, 26300: 7.3}, "PE": {25450: 13.85, 23550: 45.6}}
    spec = spec_mod.parse({"structure": "iron_fly", "params": {"pct_width": 1.5},
                           "entry_time": "11:00"})
    reasons = {}
    legs = backtest._build_legs(spec, chain, spot, dt.date(2026, 1, 20), reasons=reasons)
    assert legs is None
    assert reasons["atm_drift"] == 1
    assert abs(25450 - spot) > backtest.MAX_ATM_DRIFT_STEPS * step


def test_a_skipped_cycle_says_which_rule_skipped_it():
    """Three different failures used to share one note claiming the chain lacked a real
    print, which was true of only one of them."""
    result = backtest.run({"structure": "iron_fly", "params": {"pct_width": 1.5,
                                                              "entry_dte": 4},
                           "entry_time": "11:00"})
    assert any("ATM could not be located" in n for n in result.notes)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_parameters_are_refused(bad):
    """nan passes EVERY numeric bound in the validator, because every comparison against
    nan is False. It then reached the stop level, where `price <= nan` is False at every
    minute, so the stop never fired -- and the run came back as 50 clean expiry
    settlements at +39,385 rupees with `sl_mult: nan` echoed back as though applied. The
    same strategy with a real stop loses 5,884. A silently inverted answer is worse than
    an error."""
    with pytest.raises(spec_mod.SpecError, match="finite"):
        spec_mod.parse({"structure": "short_strangle", "entry_time": "11:00",
                        "params": {"pct_offset": 1.5, "sl_mult": bad, "entry_dte": 4}})


def test_finite_parameters_still_pass():
    spec = spec_mod.parse({"structure": "short_strangle", "entry_time": "11:00",
                           "params": {"pct_offset": 1.5, "sl_mult": 2.0, "entry_dte": 4}})
    assert spec.params["sl_mult"] == 2.0
