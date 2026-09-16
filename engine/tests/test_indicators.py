"""Index indicators as entry conditions: rsi_N, close_vs_sma_N_pct, ema_F_vs_S_pct.

The single most important property here is that NO value can see the day it is read on.
That is not a check bolted onto the computation; it is the choice of input series. The
tests below pin the arithmetic against a plain reference, then pin the look-ahead
boundary explicitly, because the last time a signal read its own day's close it made 29
of 51 live strategies fantasy.
"""
import datetime as dt
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engine import marks, strategy  # noqa: E402


# ---------------------------------------------------------------- grammar

def test_indicator_names_are_recognised_and_bounded():
    assert strategy.indicator_field("rsi_14")["kind"] == "rsi"
    assert strategy.indicator_field("close_vs_sma_50_pct")["n"] == 50
    assert strategy.indicator_field("ema_9_vs_21_pct") == {
        "kind": "ema_cross", "n": 9, "m": 21, "name": "ema_9_vs_21_pct"}
    assert strategy.indicator_field("vix") is None
    assert strategy.indicator_field("rsi") is None
    for bad in ("rsi_1", "rsi_251", "close_vs_sma_0_pct", "ema_21_vs_9_pct"):
        with pytest.raises(strategy.StrategyError):
            strategy.indicator_field(bad)


def test_an_indicator_condition_parses_and_reads_as_a_market_field():
    cond = strategy.parse_condition({"rsi_14": {"lt": 30}}, "entry.when", 2)
    assert cond["op"] == "cmp" and cond["field"] == "rsi_14"
    assert strategy.is_market_field("rsi_14")
    assert not strategy.is_market_field("pnl_pts")


def test_a_strategy_lists_only_the_indicators_it_names():
    raw = {"legs": [{"side": "sell", "type": "CE", "strike": {"pct_offset": 1.0}}],
           "entry": {"cadence": "weekly", "dte": 3, "time": "09:30",
                     "when": {"all": [{"rsi_14": {"lt": 70}},
                                      {"close_vs_ema_50_pct": {"gt": 0}},
                                      {"vix": {"gte": 12}}]}}}
    strat = strategy.parse(raw, window=(dt.date(2025, 7, 1), dt.date(2026, 6, 30)))
    assert set(strat.indicators) == {"rsi_14", "close_vs_ema_50_pct"}
    plain = strategy.parse({"legs": raw["legs"],
                            "entry": {"cadence": "weekly", "dte": 3, "time": "09:30"}},
                           window=(dt.date(2025, 7, 1), dt.date(2026, 6, 30)))
    assert plain.indicators == {}, "no indicator named, none computed"


# ---------------------------------------------------------------- arithmetic

def _ref_sma(xs, n):
    return [None if i < n - 1 else sum(xs[i - n + 1:i + 1]) / n for i in range(len(xs))]


def _ref_ema(xs, n):
    out, k, v = [], 2 / (n + 1), None
    for i, x in enumerate(xs):
        if i < n - 1:
            out.append(None)
        elif i == n - 1:
            v = sum(xs[:n]) / n
            out.append(v)
        else:
            v = x * k + v * (1 - k)
            out.append(v)
    return out


def _ref_rsi(xs, n):
    """Straight from Wilder, written independently of the implementation."""
    out = [None] * len(xs)
    if len(xs) <= n:
        return out
    ups = [max(xs[i] - xs[i - 1], 0) for i in range(1, len(xs))]
    dns = [max(xs[i - 1] - xs[i], 0) for i in range(1, len(xs))]
    au, ad = sum(ups[:n]) / n, sum(dns[:n]) / n
    for i in range(n, len(xs)):
        if i > n:
            au = (au * (n - 1) + ups[i - 1]) / n
            ad = (ad * (n - 1) + dns[i - 1]) / n
        out[i] = 100.0 if ad == 0 else 100 - 100 / (1 + au / ad)
    return out


SERIES = [100 + 10 * math.sin(i / 3.0) + (i % 7) * 0.4 for i in range(120)]


def test_sma_ema_rsi_match_an_independent_reference():
    assert marks._sma(SERIES, 20) == pytest.approx(_ref_sma(SERIES, 20), nan_ok=True)
    for a, b in zip(marks._ema(SERIES, 9), _ref_ema(SERIES, 9)):
        assert (a is None) == (b is None) and (a is None or a == pytest.approx(b))
    for a, b in zip(marks._rsi(SERIES, 14), _ref_rsi(SERIES, 14)):
        assert (a is None) == (b is None) and (a is None or a == pytest.approx(b))


def test_rsi_stays_in_range_and_is_undefined_until_it_has_enough_data():
    r = marks._rsi(SERIES, 14)
    assert r[:14] == [None] * 14
    assert all(0.0 <= v <= 100.0 for v in r[14:])


def test_relative_fields_are_in_percent_with_the_right_sign():
    up = [100.0] * 30 + [110.0]           # close jumps above a flat average
    spec = strategy.indicator_field("close_vs_sma_20_pct")
    last = marks._indicator_series(spec, up)[-1]
    assert last > 0 and last == pytest.approx((110 / ((100 * 19 + 110) / 20) - 1) * 100)


# ---------------------------------------------------------------- look-ahead

def test_indicator_on_day_d_uses_only_closes_before_d(monkeypatch):
    """THE test. Build a synthetic market_day where each row's prev_close is the close of
    the row before, then check that the RSI attached to a day changes when an EARLIER
    close changes and does NOT change when that day's own close changes."""
    days = [dt.date(2025, 1, 1) + dt.timedelta(days=i) for i in range(60)]
    closes = [100 + i + (3 if i % 5 == 0 else 0) for i in range(60)]

    def fake_rows(sql, params=None, cache=True):
        return [{"d": d, "prev_close": closes[i - 1] if i else closes[0]}
                for i, d in enumerate(days)]

    monkeypatch.setattr(marks.db, "rows", fake_rows)
    spec = {"rsi_14": strategy.indicator_field("rsi_14")}
    base = marks.indicator_days(days[40], days[59], spec)[days[50]]["rsi_14"]

    # Change day 50's OWN close: the value read at 09:15 of day 50 must not move.
    closes[50] += 40.0
    same = marks.indicator_days(days[40], days[59], spec)[days[50]]["rsi_14"]
    assert same == pytest.approx(base), "the indicator saw its own day's close"

    # Change day 49's close: that IS yesterday from day 50's point of view -- it must move.
    closes[49] += 40.0
    moved = marks.indicator_days(days[40], days[59], spec)[days[50]]["rsi_14"]
    assert moved != pytest.approx(base), "yesterday's close should have changed the value"


def test_the_first_day_of_a_run_is_warm_not_undefined(monkeypatch):
    """A 50-day average must be defined on day one of a backtest: the query reads back
    past d_from for exactly that reason. Without the warm-up every gate would refuse the
    first two months of every run and nobody would know why."""
    days = [dt.date(2024, 1, 1) + dt.timedelta(days=i) for i in range(500)]
    seen = {}

    def fake_rows(sql, params=None, cache=True):
        seen.update(params or {})
        lo = dt.date.fromisoformat(params["a"])
        return [{"d": d, "prev_close": 100.0 + i} for i, d in enumerate(days) if d >= lo]

    monkeypatch.setattr(marks.db, "rows", fake_rows)
    spec = {"close_vs_sma_50_pct": strategy.indicator_field("close_vs_sma_50_pct")}
    out = marks.indicator_days(days[400], days[420], spec)
    assert dt.date.fromisoformat(seen["a"]) < days[400] - dt.timedelta(days=200)
    assert out[days[400]]["close_vs_sma_50_pct"] is not None
    assert set(out) == set(days[400:421]), "only the requested window is returned"


def test_nothing_is_computed_when_nothing_is_asked_for(monkeypatch):
    called = []
    monkeypatch.setattr(marks.db, "rows", lambda *a, **k: called.append(1) or [])
    assert marks.indicator_days(dt.date(2025, 1, 1), dt.date(2025, 2, 1), {}) == {}
    assert not called, "an empty spec must not touch the database"
