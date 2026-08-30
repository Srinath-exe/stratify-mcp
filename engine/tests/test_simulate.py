"""The general simulator: rule evaluation, position accounting, and the guards.

The DB-backed equivalence test at the bottom is the important one -- it asserts the two
engines agree exactly on the physics of an identical position. Everything above it is
arithmetic that must hold without a database.
"""
import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engine import simulate as sim, strategy as S  # noqa: E402

W = (dt.date(2019, 1, 1), dt.date(2026, 6, 30))
T0 = dt.datetime(2025, 9, 2, 9, 30)


def pos(credit_legs=((("CE", "SELL", 24200, 40.0), ("PE", "SELL", 23800, 38.0)))):
    p = sim.Position(T0.date(), T0, 24000.0, {"near": dt.date(2025, 9, 4)})
    for i, (ty, act, k, px) in enumerate(credit_legs):
        sim._add_leg(p, ty, k, act, 1, dt.date(2025, 9, 4), px, T0, 24000.0, i, 50, 24000)
    p.credit_at_entry = p.credit
    p.max_profit = sim._max_profit(p.legs)
    return p


def state(p, marks, spot=24000.0, ts=None):
    return sim._state(p, ts or (T0 + dt.timedelta(minutes=30)),
                      {l["id"]: marks[i] for i, l in enumerate(p.open_legs)}, spot)


def cond(raw, n=2):
    return S.parse_condition(raw, "t", n)


# ------------------------------------------------------------------- accounting

def test_the_sign_convention_matches_the_older_engine():
    """sold leg profits when the price falls, bought leg when it rises. Getting this
    backwards is silent: the numbers stay plausible and every trade is inverted."""
    p = pos()
    st = state(p, [30.0, 30.0])          # both shorts halved
    assert st["pnl"] == pytest.approx((40 - 30) + (38 - 30))
    assert st["cost"] == pytest.approx(60.0)          # what it costs to close
    p2 = pos((("CE", "BUY", 24200, 40.0),))
    assert state(p2, [55.0])["pnl"] == pytest.approx(15.0)


def test_credit_kept_and_pct_of_credit_use_the_ENTRY_credit():
    """A roll that collects more premium must not quietly loosen a stop the author wrote
    against the credit they originally took in."""
    p = pos()
    st = state(p, [20.0, 19.0])
    assert sim._field("pnl_pct_of_credit", None, p, st, 75, 1) == pytest.approx(39 / 78)
    p.credit += 100.0                                  # as a roll would
    assert sim._field("pnl_pct_of_credit", None, p, st, 75, 1) == pytest.approx(39 / 78)


def test_a_condition_about_a_closed_leg_is_false_not_an_error():
    p = pos()
    sim._close_leg(p.legs[0], 10.0, T0, "ADJUST")
    st = state(p, [30.0])
    assert evaluate_false(cond({"leg_mark": {"leg": 0, "gte": 0}}), p, st)


def evaluate_false(c, p, st):
    return sim.evaluate(c, p, st, 75, 1) is False


def test_trailing_conditions_track_the_running_extremes():
    p = pos()
    state(p, [20.0, 19.0])                             # peak: +39
    st = state(p, [30.0, 30.0])                        # now:  +18
    assert sim._field("drawdown_from_peak", None, p, st, 75, 1) == pytest.approx(21.0)


def test_and_or_not_compose():
    p = pos()
    st = state(p, [20.0, 19.0])
    assert sim.evaluate(cond({"all": [{"pnl_pts": {"gte": 30}},
                                      {"combined_premium": {"lte": 50}}]}), p, st, 75, 1)
    assert not sim.evaluate(cond({"all": [{"pnl_pts": {"gte": 30}},
                                          {"combined_premium": {"gte": 50}}]}),
                            p, st, 75, 1)
    assert sim.evaluate(cond({"not": {"pnl_pts": {"lte": 0}}}), p, st, 75, 1)


# ---------------------------------------------------------------------- guards

def test_a_self_cancelling_pair_is_refused_but_a_calendar_is_not():
    """SHIPPED ONCE, BOTH WAYS. A SELL and a BUY of the same contract cancel to nothing and
    once produced an 'iron fly' reporting 100.6% return on margin. Keyed WITHOUT the
    expiry, the same guard threw away every calendar spread -- same strike, different
    contract, cancels nothing."""
    same = [{"expiry": dt.date(2025, 9, 4), "option_type": "CE", "strike": 24000,
             "action": "SELL", "qty": 1, "premium_pts": 40.0},
            {"expiry": dt.date(2025, 9, 4), "option_type": "CE", "strike": 24000,
             "action": "BUY", "qty": 1, "premium_pts": 40.0}]
    assert sim._structurally_sound(same) == "offsetting_legs"
    cal = [dict(same[0]), dict(same[1], expiry=dt.date(2025, 9, 11), premium_pts=70.0)]
    assert sim._structurally_sound(cal) is None


def test_max_profit_is_none_when_the_upside_is_open_ended():
    """A rule written against 'percent of max profit' must not fire against a ceiling the
    position does not have."""
    capped = pos((("CE", "SELL", 24200, 40.0), ("CE", "BUY", 24400, 15.0)))
    assert capped.max_profit == pytest.approx(25.0)
    naked_long = pos((("CE", "BUY", 24200, 40.0),))
    assert naked_long.max_profit is None


# ------------------------------------------------------------------- portfolio

class FakeTrade:
    def __init__(self, pnl, day):
        self.pnl_rupees = pnl
        self.exit_ts = dt.datetime(2025, 9, day, 15, 29)
        self.entry_ts = self.exit_ts


def book(strat_kw, pnls):
    st = S.parse({"legs": [{"side": "sell", "type": "CE", "strike": {"pct_offset": 1.0}}],
                  "period": {"from": "2025-07-01", "to": "2026-06-30"}, **strat_kw},
                 window=W)
    return sim.apply_portfolio(st, [FakeTrade(p, i + 1) for i, p in enumerate(pnls)])


def test_skip_after_loss_skips_exactly_one_cycle():
    """SHIPPED ONCE. Reading the flag off the last KEPT trade made the skip permanent:
    after the first loss the last kept trade stayed the loser forever, and a 75%-win
    strategy took nine trades out of fifty-one."""
    # win, LOSS, (skipped), win, LOSS, (skipped) -> four taken, two stood down
    kept, dropped = book({"portfolio": {"skip_after_loss": True}},
                         [100, -100, 100, 100, -100, 100])
    assert [t.pnl_rupees for t in kept] == [100, -100, 100, -100]
    assert dropped["skip_after_loss"] == 2


def test_stop_after_losses_ends_the_book():
    kept, dropped = book({"portfolio": {"stop_after_losses": 2}},
                         [100, -10, -10, 500, 500])
    assert [t.pnl_rupees for t in kept] == [100, -10, -10]
    assert dropped["stop_after_losses"] == 2


def test_a_drawdown_stop_is_measured_against_a_stated_capital():
    kept, _ = book({"portfolio": {"stop_after_drawdown_pct": 5}},
                   [100_000, -60_000, 20_000, 20_000])
    assert len(kept) == 2                       # 60k on 10L is 6%, past the 5% stop


# --------------------------------------------------------------- equivalence (DB)

def _db_ready():
    try:
        from engine import db
        db.use_tier("pro")
        db.rows("SELECT 1 AS n")
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_ready(), reason="needs ClickHouse")
@pytest.mark.parametrize("legs,preset", [
    ([{"side": "sell", "type": "CE", "strike": {"pct_offset": 1.5}},
      {"side": "sell", "type": "PE", "strike": {"pct_offset": -1.5}}],
     {"structure": "short_strangle", "params": {"pct_offset": 1.5, "entry_dte": 2}}),
    ([{"side": "sell", "type": "CE", "strike": {"pct_offset": 1.0}},
      {"side": "buy", "type": "CE", "strike": {"pct_offset": 2.0}},
      {"side": "sell", "type": "PE", "strike": {"pct_offset": -1.0}},
      {"side": "buy", "type": "PE", "strike": {"pct_offset": -2.0}}],
     {"structure": "iron_condor",
      "params": {"pct_offset": 1.0, "pct_width": 1.0, "entry_dte": 2}}),
])
def test_the_two_engines_agree_on_the_physics(legs, preset):
    """The reason the general engine can be trusted. Identical position, identical
    entries, identical gross points, slippage, net points and margin. Charges differ by
    design -- the general engine bills per leg on real turnover instead of on the net
    premium -- and that is the only difference allowed."""
    from engine import backtest, db, spec as V1
    db.use_tier("pro")
    period = {"from": "2025-07-01", "to": "2026-06-30"}
    new = sim.run(S.parse({"legs": legs,
                           "entry": {"cadence": "weekly", "dte": 2, "time": "09:30"},
                           "period": period}, window=W))
    old = backtest.run(V1.parse(dict(preset, entry_time="09:30", cadence="weekly",
                                     period=period), tier="pro"))
    assert len(new.trades) == len(old.trades)
    for a, b in zip(sorted(new.trades, key=lambda t: t.entry_ts),
                    sorted(old.trades, key=lambda t: t.entry_ts)):
        assert a.entry_ts == b.entry_ts and a.expiry == b.expiry
        assert a.pnl_pts == pytest.approx(b.pnl_pts, abs=1e-6)
        assert a.slippage_pts == pytest.approx(b.slippage_pts, abs=1e-6)
        assert a.margin_pts == pytest.approx(b.margin_pts, abs=1e-6)


def test_a_coarse_resolution_actually_reaches_the_database():
    """Both coarse resolutions raised TypeError before a query was ever sent: the thinning
    clause used SQL's % operator, and clickhouse_connect binds parameters with Python's
    own `query % params`, so the driver read it as a format spec. A whole documented
    feature was dead. Assert the built SQL carries no bare % outside a %(name)s binding."""
    import re
    from engine import marks
    seen = {}

    def fake_rows(sql, parameters=None, cache=True):
        seen["sql"] = sql
        # exactly what the driver does when parameters are present
        if parameters:
            sql % {k: "'x'" for k in parameters}
        return []

    real, marks.db.rows = marks.db.rows, fake_rows
    try:
        for res in (5, 15):
            marks._spot_slice(dt.datetime(2026, 1, 1, 9, 15),
                              dt.datetime(2026, 1, 1, 15, 29), res, {})
            assert f"modulo(toMinute(timestamp), {res})" in seen["sql"]
            assert not re.search(r"%(?!\()", seen["sql"])
            marks._paths_batch([(dt.date(2026, 1, 1), "CE", 24000)],
                               dt.datetime(2026, 1, 1, 9, 15),
                               dt.datetime(2026, 1, 1, 15, 29), res, 7, {})
            assert not re.search(r"%(?!\()", seen["sql"])
    finally:
        marks.db.rows = real


def _leg(action, otype, strike, qty=1, expiry=dt.date(2026, 1, 8), premium=50.0):
    return {"action": action, "option_type": otype, "strike": strike, "qty": qty,
            "expiry": expiry, "premium_pts": premium}


def test_legs_landing_on_one_contract_are_netted_not_refused():
    """A 1x2 ratio whose premium-selected shorts resolve onto the long's own strike is a
    real position -- net short one call -- and used to be thrown away as 'offsetting'.
    Only a contract netting to exactly zero is refused, because that is the one that
    fakes the margin."""
    ratio = [_leg("BUY", "CE", 24000), _leg("SELL", "CE", 24000, qty=2)]
    assert sim._structurally_sound(ratio) is None
    cancels = [_leg("BUY", "CE", 24000), _leg("SELL", "CE", 24000)]
    assert sim._structurally_sound(cancels) == "offsetting_legs"
    # ...and the same strike in two different expiries still cancels nothing.
    calendar = [_leg("SELL", "CE", 24000),
                _leg("BUY", "CE", 24000, expiry=dt.date(2026, 1, 15))]
    assert sim._structurally_sound(calendar) is None
    # Two legs written on the same contract and the same side are simply size.
    assert sim._structurally_sound([_leg("SELL", "PE", 23500),
                                    _leg("SELL", "PE", 23500)]) is None


LOOKAHEAD_COLUMNS = ("high", "low", "close", "vix_close")


def test_market_state_cannot_read_the_day_it_is_deciding_about():
    """The one bug this project has already paid for: signals read the entry day's 15:30
    close and 29 of 51 live strategies were fantasy, median 24 CAGR points of it.

    market_day stores that day's high, low and close so the table can be rebuilt and
    audited. The guarantee is that the ENGINE never selects them, and it is asserted
    mechanically here rather than left to whoever writes the next field."""
    import re
    from engine import marks
    seen = {}

    def fake_rows(sql, parameters=None, cache=True):
        seen["sql"] = sql
        return []

    real, marks.db.rows = marks.db.rows, fake_rows
    try:
        marks.market_days(dt.date(2026, 1, 1), dt.date(2026, 6, 30))
    finally:
        marks.db.rows = real
    selected = re.search(r"SELECT(.*?)FROM", seen["sql"], re.S).group(1)
    for col in LOOKAHEAD_COLUMNS:
        assert not re.search(rf"(?<![_a-z]){col}(?![_a-z])", selected), \
            f"market_days selects {col!r}, which is not knowable when the entry gate runs"
    # and the ones it DOES read are all settled before the session, or the opening print
    assert "prev_close" in selected and "prev_day_move_pct" in selected
    assert "gap_pct" in selected and "realised_vol_20d" in selected


@pytest.mark.skipif(not _db_ready(), reason="needs ClickHouse")
def test_a_prev_day_gate_picks_exactly_the_days_it_should():
    """End to end: the days a 'yesterday fell 1%' gate trades on must be exactly the days
    an independent read of the daily series says they are."""
    from engine import marks
    window = (dt.date(2025, 7, 1), dt.date(2026, 6, 30))
    days = marks.market_days(*window)
    spec = {"legs": [{"side": "buy", "type": "CE", "strike": "atm"},
                     {"side": "buy", "type": "PE", "strike": "atm"}],
            "entry": {"cadence": "daily", "max_dte": 3, "time": "09:30",
                      "when": {"prev_day_move_pct": {"lte": -1.0}}},
            "exit": {"time": "15:00"}}
    res = sim.run(S.parse(spec, window=window))
    traded = {t.entry_date for t in res.trades}
    expected = {d for d, r in days.items() if r["prev_day_move_pct"] <= -1.0}
    assert traded, "the gate matched nothing at all, so this proves nothing"
    # Every day it traded really did follow a 1% fall. (The reverse can differ: a
    # qualifying day may be skipped because the chain was too thin to locate the money.)
    assert traded <= expected, sorted(traded - expected)[:5]
