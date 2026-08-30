"""Metrics tests, concentrated on the two honesty floors — the rules that exist because
a competitor's MCP violated both: Sharpe printed on 2 trades, and values of 64.91."""
import datetime as dt
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "stratify_mcp"))

from engine import backtest, metrics, spec as spec_mod  # noqa: E402


def _trade(pnl_pts, margin=100.0, day=1, lot=65):
    d = dt.date(2026, 1, 1) + dt.timedelta(days=day * 7)
    return backtest.Trade(
        expiry=d, entry_date=d, entry_ts=dt.datetime.combine(d, dt.time(9, 30)),
        exit_ts=dt.datetime.combine(d, dt.time(15, 29)), exit_reason="EXPIRY",
        spot_entry=24000.0, legs=[], entry_credit_pts=pnl_pts, exit_value_pts=0.0,
        pnl_pts=pnl_pts, slippage_pts=0.0, margin_pts=margin, lot_size=lot,
        charges_rupees=100.0, pnl_rupees=pnl_pts * lot - 100.0)


def _result(trades):
    return backtest.BacktestResult(
        spec=spec_mod.StrategySpec(structure="short_strangle",
                                   params={"pct_offset": 1.5}),
        trades=trades, warnings=[], notes=[],
        n_contracts=100, n_trading_days=len(trades))


@pytest.mark.parametrize("n", [1, 2, 5, 29])
def test_no_ratio_is_emitted_below_thirty_trades(n):
    s = metrics.summarise(_result([_trade(10.0, day=i) for i in range(n)]))
    assert s["ratios"] is None
    assert "insufficient_sample" in s["ratios_withheld"]
    assert str(n) in s["ratios_withheld"]
    # the descriptive numbers are still there -- withholding a ratio is not hiding the data
    assert s["n_trades"] == n
    assert "total_pnl_rupees" in s


def test_ratios_appear_at_thirty():
    s = metrics.summarise(_result([_trade(10.0 if i % 3 else -8.0, day=i)
                                   for i in range(30)]))
    assert s["ratios"] is not None
    assert s["ratios"]["profit_factor"] is not None


def test_absurd_sharpe_is_flagged_not_reported_as_a_finding():
    # near-identical positive returns -> tiny stdev -> enormous Sharpe
    trades = [_trade(10.0 + (i % 2) * 0.001, day=i) for i in range(40)]
    s = metrics.summarise(_result(trades))
    assert s["ratios"]["sharpe"] > metrics.SHARPE_SANITY_CEILING
    assert any("sanity ceiling" in w for w in s["warnings"])


def test_sharpe_annualises_by_observed_frequency_not_a_constant():
    weekly = [_trade(10.0 if i % 3 else -8.0, day=i) for i in range(40)]
    daily = [_trade(10.0 if i % 3 else -8.0, day=i / 7.0) for i in range(40)]
    sw = metrics.summarise(_result(weekly))["ratios"]["sharpe"]
    sd = metrics.summarise(_result(daily))["ratios"]["sharpe"]
    assert sd > sw          # same edge, traded 7x more often, annualises higher
    assert "40 trades over" in metrics.summarise(_result(weekly))["ratios"]["sharpe_basis"]


def test_max_drawdown_is_peak_to_trough():
    assert metrics._max_drawdown([100.0, -30.0, -20.0, 60.0]) == pytest.approx(-50.0)
    assert metrics._max_drawdown([10.0, 20.0, 30.0]) == 0.0
    assert metrics._max_drawdown([-40.0]) == pytest.approx(-40.0)


def test_return_is_on_margin_not_notional():
    s = metrics.summarise(_result([_trade(5.0, margin=100.0, day=i) for i in range(35)]))
    assert s["mean_return_on_margin"] == pytest.approx(0.05)


def test_no_trades_is_a_status_not_a_crash():
    s = metrics.summarise(_result([]))
    assert s["status"] == "no_trades"
    assert "ratios" not in s


# ---------------------------------------------------------------- margin disclosure

def test_return_on_margin_never_ships_without_its_basis():
    """margin_points() returns (value, note) and the live path discarded the note, so a
    naked strangle's ROM -- built on a ratio calibrated today and applied to the past --
    read exactly like a condor's, whose margin is its maximum loss and is exact."""
    from engine import backtest
    m = metrics.summarise(backtest.run({
        "structure": "short_strangle",
        "params": {"pct_offset": 1.5, "sl_mult": 2.0, "entry_dte": 4},
        "entry_time": "11:00"}))
    assert m["margin_basis"], "margin basis was dropped again"
    assert any("calibrated" in b for b in m["margin_basis"])
    # And it reaches the warnings, not only a field a reader has to go looking for.
    assert any("calibrated" in w for w in m["warnings"])


def test_defined_risk_margin_is_declared_floor_and_width_capped():
    """iron_condor's margin is no longer declared purely 'Exact' -- a live Fyers SPAN check
    found real broker margin exceeds the textbook max loss for a tight defined-risk combo,
    so margin.py now floors it (capped at the combo's own width). See DECISIONS.md open
    action #3 and engine/config/margin.py's docstring."""
    from engine import backtest
    m = metrics.summarise(backtest.run({
        "structure": "iron_condor",
        "params": {"pct_offset": 1.5, "pct_width": 1.0, "entry_dte": 4},
        "entry_time": "11:00"}))
    assert any("floor" in b for b in m["margin_basis"])
    assert any("capped" in b for b in m["margin_basis"])
    assert not any("calibrated" in w for w in m["warnings"])  # still a naked-margin-only warning, not this


def test_long_option_margin_is_untouched_by_the_new_floor():
    """long_option doesn't route through margin.margin_points() at all in production (it has
    its own dedicated note, set directly in backtest.py) -- so this new floor cannot have
    touched it either way. Confirms the end-to-end text is still unmodified premium-paid
    language, not the new floor/width-capped one."""
    from engine import backtest
    m = metrics.summarise(backtest.run({
        "structure": "long_option",
        "params": {"pct_offset": 0.5, "direction": "CE", "entry_dte": 4},
        "entry_time": "11:00"}))
    assert any("Premium paid in full" in b for b in m["margin_basis"])
    assert not any("floor" in b for b in m["margin_basis"])


def test_peak_margin_is_reported_not_only_the_mean():
    """The mean is what the strategy used on a typical day; the peak is the capital it had
    to have available. Sizing an account from the mean under-funds it."""
    from engine import backtest
    m = metrics.summarise(backtest.run({
        "structure": "iron_condor",
        "params": {"pct_offset": 1.5, "pct_width": 1.0, "entry_dte": 4},
        "entry_time": "11:00"}))
    assert m["peak_margin_points"] >= m["avg_margin_points"] > 0


def test_charges_share_is_none_rather_than_absurd_when_gross_is_negative():
    """A share of a negative edge is not a percentage. Reporting one produced 'costs
    consume 447 % of the gross edge' elsewhere in this codebase."""
    from engine import backtest
    m = metrics.summarise(backtest.run({
        "structure": "long_option",
        "params": {"pct_offset": 0.5, "direction": "CE"},
        "entry_time": "11:00", "cadence": "daily", "exit_time": "14:00"}))
    cs = m["charges_share_of_gross"]
    assert cs["charges_points"] > 0
    assert cs["share"] is None or cs["share"] > 0
    if cs["share"] is None:
        assert "not meaningful" in cs["note"]
