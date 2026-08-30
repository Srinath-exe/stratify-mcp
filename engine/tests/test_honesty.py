"""Honesty-panel tests. These encode the rules that a competitor's MCP broke live:
Sharpe on a 2-trade sample, no multiple-comparisons correction across a 9-strategy
search, and an 'OVERFITTED' verdict emitted on zero out-of-sample trades."""
import datetime as dt
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "stratify_mcp"))

from engine import backtest, honesty, metrics, spec as spec_mod  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_variant_log(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(honesty, "VARIANT_LOG", Path(d) / "variants.sqlite")
        yield


def _trade(pnl_pts, day, margin=100.0):
    d = dt.date(2025, 7, 1) + dt.timedelta(days=day * 7)
    return backtest.Trade(
        expiry=d, entry_date=d, entry_ts=dt.datetime.combine(d, dt.time(9, 30)),
        exit_ts=dt.datetime.combine(d, dt.time(15, 29)), exit_reason="EXPIRY",
        spot_entry=24000.0, legs=[], entry_credit_pts=pnl_pts, exit_value_pts=0.0,
        pnl_pts=pnl_pts, slippage_pts=0.5, margin_pts=margin, lot_size=65,
        charges_rupees=100.0, pnl_rupees=pnl_pts * 65 - 100.0)


def _result(trades):
    return backtest.BacktestResult(
        spec=spec_mod.StrategySpec(structure="short_strangle", params={"pct_offset": 1.5}),
        trades=trades, warnings=[], notes=[], n_contracts=100,
        n_trading_days=len(trades))


# ---------------------------------------------------------------- floors

@pytest.mark.parametrize("n", [0, 2, 10, 29])
def test_small_sample_gets_no_panel_at_all(n):
    r = _result([_trade(10.0, i) for i in range(n)])
    p = honesty.panel(r, metrics.summarise(r), now=0.0)
    assert p["verdict"] == "insufficient_evidence"
    assert p["health_score"] == 0
    assert "out_of_sample" not in p
    assert "multiple_comparisons" not in p


def test_zero_out_of_sample_trades_never_yields_a_verdict():
    """The exact defect seen live: 'OVERFITTED — do not trade live' printed on
    oos_total_trades: 0. A claim needs evidence in the held-out slice."""
    r = _result([_trade(10.0, i) for i in range(40)])
    p = honesty.panel(r, metrics.summarise(r), now=0.0)
    assert p["out_of_sample"]["out_of_sample"]["n_trades"] > 0


def test_split_is_chronological_not_random():
    trades = [_trade(10.0, i) for i in range(40)]
    ins, oos = honesty._chronological_split(trades)
    assert max(t.entry_date for t in ins) <= min(t.entry_date for t in oos)
    assert len(oos) == pytest.approx(len(trades) * honesty.OOS_FRACTION, abs=1)


def test_rubric_is_published_with_the_panel():
    r = _result([_trade(10.0, i) for i in range(40)])
    p = honesty.panel(r, metrics.summarise(r), now=0.0)
    assert {c["component"] for c in p["rubric"]} == {c for c, _, _ in honesty.HEALTH_RUBRIC}
    assert sum(c["max_points"] for c in p["rubric"]) == 100
    assert all(c["rule"] for c in p["rubric"])


def test_verdict_never_says_a_strategy_is_good():
    for score in range(0, 101, 5):
        v = honesty._verdict({"health_score": score})
        assert "good" not in v and "buy" not in v and "recommend" not in v


# ---------------------------------------------------------------- multiple comparisons

def test_identical_spec_rerun_is_one_variant():
    r = _result([_trade(10.0 if i % 3 else -8.0, i) for i in range(40)])
    s = metrics.summarise(r)
    for _ in range(5):
        p = honesty.panel(r, s, api_key_id="k", now=1000.0)
    assert p["multiple_comparisons"]["variants_tested_last_24h"] == 1


def _variant_result(offset, trades):
    """Distinct SPEC, which is what a variant is -- the spec hash is what gets logged."""
    return backtest.BacktestResult(
        spec=spec_mod.StrategySpec(structure="short_strangle",
                                   params={"pct_offset": offset}),
        trades=trades, warnings=[], notes=[], n_contracts=100,
        n_trading_days=len(trades))


def test_searching_more_variants_deflates_harder():
    trades = [_trade(10.0 if i % 3 else -8.0, i) for i in range(40)]
    r = _variant_result(1.5, trades)
    first = honesty.panel(r, metrics.summarise(r), api_key_id="k2", now=1000.0)
    for i in range(20):
        alt = [_trade(10.0 + i * 0.1 if j % 3 else -8.0, j) for j in range(40)]
        r2 = _variant_result(0.5 + i * 0.1, alt)
        last = honesty.panel(r2, metrics.summarise(r2), api_key_id="k2", now=1000.0)
    assert last["multiple_comparisons"]["variants_tested_last_24h"] > 10
    assert (last["multiple_comparisons"]["expected_max_sharpe_under_null"]
            > first["multiple_comparisons"]["expected_max_sharpe_under_null"])


def test_variants_expire_from_the_rolling_window():
    r = _result([_trade(10.0 if i % 3 else -8.0, i) for i in range(40)])
    s = metrics.summarise(r)
    honesty.panel(r, s, api_key_id="k3", now=0.0)
    later = honesty.panel(r, s, api_key_id="k3",
                          now=honesty.VARIANT_WINDOW_HOURS * 3600 + 60)
    assert later["multiple_comparisons"]["variants_tested_last_24h"] == 1


def test_variants_are_scoped_per_key():
    r = _result([_trade(10.0 if i % 3 else -8.0, i) for i in range(40)])
    s = metrics.summarise(r)
    for i in range(5):
        r2 = _variant_result(0.5 + i * 0.1,
                             [_trade(10.0 + i if j % 3 else -8.0, j) for j in range(40)])
        honesty.panel(r2, metrics.summarise(r2), api_key_id="noisy", now=1000.0)
    mine = honesty.panel(r, s, api_key_id="quiet", now=1000.0)
    assert mine["multiple_comparisons"]["variants_tested_last_24h"] == 1


# ---------------------------------------------------------------- statistics

def test_phi_inv_round_trips():
    for p in (0.01, 0.1, 0.5, 0.9, 0.975, 0.999):
        assert honesty._phi(honesty._phi_inv(p)) == pytest.approx(p, abs=1e-6)


def test_single_trial_deflates_by_nothing():
    returns = [0.01, -0.02, 0.03, 0.01, 0.0, 0.02, -0.01] * 6
    _, sr0, _ = honesty.deflated_sharpe(returns, 1.0, n_trials=1)
    assert sr0 == 0.0


def test_expected_max_sharpe_grows_with_trials():
    returns = [0.01, -0.02, 0.03, 0.01, 0.0, 0.02, -0.01] * 6
    sharpes = [0.1 * i for i in range(20)]
    _, sr10, _ = honesty.deflated_sharpe(returns, 1.0, 10, sharpes)
    _, sr500, _ = honesty.deflated_sharpe(returns, 1.0, 500, sharpes)
    assert sr500 > sr10 > 0


def test_deflated_sharpe_says_when_it_is_guessing_the_variance():
    returns = [0.01, -0.02, 0.03, 0.01, 0.0, 0.02, -0.01] * 6
    _, _, basis = honesty.deflated_sharpe(returns, 1.0, 10, trial_sharpes=None)
    assert "too few logged trials" in basis


def test_bootstrap_is_seeded_and_reproducible():
    values = [0.01, -0.02, 0.03, 0.01, 0.0, 0.02, -0.01] * 6
    assert honesty.bootstrap_ci(values) == honesty.bootstrap_ci(values)


def test_bootstrap_interval_brackets_the_mean():
    values = [0.01, -0.02, 0.03, 0.01, 0.0, 0.02, -0.01] * 6
    lo, hi = honesty.bootstrap_ci(values)
    assert lo < sum(values) / len(values) < hi


def test_walk_forward_folds_are_contiguous_and_ordered():
    trades = [_trade(10.0, i) for i in range(30)]
    folds = honesty._walk_forward(trades)
    assert len(folds) == honesty.WALK_FORWARD_FOLDS
    assert sum(f["n_trades"] for f in folds) == 30
    assert folds[0]["to"] <= folds[1]["from"] <= folds[1]["to"] <= folds[2]["from"]


def test_cost_drag_is_reported_end_to_end():
    r = _result([_trade(10.0, i) for i in range(40)])
    p = honesty.panel(r, metrics.summarise(r), now=0.0)
    cd = p["cost_drag"]
    assert cd["slippage_points"] > 0 and cd["charges_points"] > 0
    assert cd["net_points_after_costs"] < cd["gross_points_before_costs"]
    assert 0 < cd["share_of_edge_surviving"] < 1
