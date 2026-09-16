"""Rich-result tests.

Two jobs. The arithmetic ones make the released detail auditable -- if the equity curve
disagrees with the headline P&L, the detail is worse than useless because it looks like
evidence. The boundary ones pin what is released and what is not, because that boundary
is a deliberate product decision and is exactly the kind of thing that erodes silently.
"""
import datetime as dt
import json
import sys
from pathlib import Path

import pytest

# The directory holding engine/ -- i.e. this checkout, wherever it is cloned.
# Was `parents[3] / "stratify_mcp"`, which resolved to a SIBLING directory of that
# name and silently tested a different copy of the code from any other layout.
PKG = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PKG))

from engine import backtest, detail, metrics                # noqa: E402

INTRADAY = {"structure": "long_option", "symbol": "NIFTY",
            "params": {"pct_offset": 0.5, "direction": "CE"},
            "entry_time": "11:00", "exit_time": "14:00", "cadence": "daily"}


@pytest.fixture(scope="module")
def rich():
    result = backtest.run(INTRADAY)
    return result, metrics.summarise(result), detail.build(result)


def _rows(d):
    """[date, pnl_rupees, equity_rupees, drawdown_rupees, margin_rupees] -- the columnar
    form the curve is returned in, because repeating five key names 246 times would be a
    third of the response and then doubled by the MCP envelope."""
    assert d["equity_curve"]["columns"] == detail.EQUITY_COLUMNS
    return d["equity_curve"]["rows"]


def test_equity_curve_ends_where_the_headline_says(rich):
    result, summary, d = rich
    assert _rows(d)[-1][2] == pytest.approx(summary["total_pnl_rupees"], abs=1.0)


def test_curve_drawdown_agrees_with_the_summary(rich):
    """Two independent computations of the same quantity. A caller who plots the curve and
    reads the headline must not see two different worst cases."""
    _, summary, d = rich
    worst = min(r[3] for r in _rows(d))
    assert worst == pytest.approx(summary["max_drawdown_rupees"], abs=1.0)


def test_curve_is_ordered_by_exit_not_entry(rich):
    """On a weekly cadence positions overlap, and ordering by entry credits profit to a
    moment the account had not received it. Invisible intraday, material on a swing."""
    _, _, d = rich
    dates = [r[0] for r in _rows(d)]
    assert dates == sorted(dates)


def test_every_returned_trade_reconciles(rich):
    _, _, d = rich
    for t in d["trades"]:
        assert t["net_points"] == pytest.approx(
            t["gross_points"] - t["slippage_points"], abs=0.01)
        assert t["pnl_rupees"] == pytest.approx(
            t["net_points"] * t["lot_size"] - t["charges_rupees"], abs=0.5)


def test_breakdowns_sum_back_to_the_whole(rich):
    _, summary, d = rich
    for key in ("by_month", "by_exit_reason", "by_weekday_of_entry", "by_dte_at_entry"):
        assert sum(b["n_trades"] for b in d["breakdown"][key].values()) == summary["n_trades"]
        assert sum(b["pnl_rupees"] for b in d["breakdown"][key].values()) == pytest.approx(
            summary["total_pnl_rupees"], abs=1.0)


def test_price_points_counts_real_prints_only(rich):
    """The meter has to count what was actually disclosed. Entry legs are real prints; a
    clock exit is a real print; a settlement price is intrinsic value computed from the
    INDEX and discloses nothing about the option table, so it is not counted."""
    _, _, d = rich
    expected = 0
    for t in d["trades"]:
        expected += len(t["legs"])                       # entries
        if t["exit_reason"] == "TIME":
            expected += len(t["legs"])                   # clock exits
    assert d["price_points_released"] == expected


def test_stopped_out_trades_carry_no_per_leg_exit_price(rich):
    """SQL resolves the COMBINED position value at the firing minute, never the individual
    legs, so there is nothing to disclose and the row says so rather than inventing one."""
    _, _, d = rich
    for t in d["trades"]:
        if t["exit_reason"] in ("SL", "TP"):
            assert all("exit_price" not in l for l in t["legs"])
            assert "exit_price_note" in t


def test_prices_are_withheld_below_the_floor(monkeypatch, rich):
    """The control that stops a rich result being used as a price lookup: pick one strike,
    one narrow window, read the quote back. Below the floor a run gets aggregates only."""
    result, _, _ = rich
    monkeypatch.setattr(detail, "PRICE_DETAIL_MIN_TRADES", 10_000)
    d = detail.build(result)
    assert d["trade_detail"]["prices_included"] is False
    assert d["price_points_released"] == 0
    # Strikes still ship: a strike is a rule OUTPUT, not a quote, and the replay
    # track publishes them on the same basis. What must not ship is a PRICE.
    assert all("entry_price" not in json.dumps(t) for t in d["trades"])
    assert "withheld" in d["trade_detail"]["reason"]


def test_truncation_never_silently_shortens_an_aggregate(rich):
    """A cut table is fine; a cut equity curve is a lie. If the aggregates followed the
    table, a caller would read a drawdown from the first 5 trades as the whole year's."""
    result, summary, _ = rich
    d = detail.build(result, max_trades=5)
    assert len(d["trades"]) == 5
    assert len(_rows(d)) == summary["n_trades"]
    assert _rows(d)[-1][2] == pytest.approx(summary["total_pnl_rupees"], abs=1.0)
    assert "truncation" in d["trade_detail"]


def test_no_trades_is_not_a_crash():
    class _Empty:
        trades = []
    d = detail.build(_Empty())
    assert d["equity_curve"]["rows"] == [] and d["price_points_released"] == 0


def test_curve_carries_the_margin_one_lot_blocked(rich):
    """Per-trade rows are capped and trimmed; the curve carries every trade. Without a
    margin figure on each point nothing downstream can size the WHOLE series, so a capital
    view could only be computed over the released sample and would quietly describe a
    shorter, different strategy."""
    _, _, d = rich
    i = detail.EQUITY_COLUMNS.index("margin_rupees")
    rows = _rows(d)
    assert all(len(r) == len(detail.EQUITY_COLUMNS) for r in rows)
    assert all(isinstance(r[i], (int, float)) and r[i] >= 0 for r in rows)


def test_the_exit_note_never_contradicts_the_row_it_sits_on(rich):
    """It did. `exit_prices is None` was read before the exit reason, so rows whose reason
    was EXPIRY and whose legs carried an exit price of 0.00 were labelled "closed on a
    stop or target: there is no per-leg price to report" -- a sentence disagreeing with
    the two fields either side of it."""
    _, _, d = rich
    for t in d["trades"]:
        note = t.get("exit_price_note")
        if not note:
            continue
        priced = any(l.get("exit_price") is not None for l in t.get("legs") or [])
        if "no per-leg price" in note:
            assert not priced, f"trade {t['n']} reports leg prices under a note denying them"
        if t["exit_reason"] == "EXPIRY":
            assert "settled" in note, f"trade {t['n']} settled but the note says otherwise"
