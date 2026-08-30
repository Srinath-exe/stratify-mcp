"""The open protocol: what it accepts, what it refuses, and what it means.

Every refusal test exists because a strategy that runs as something other than what its
author wrote is worse than one that fails. Every acceptance test exists because the
capability probe found the request refused before this module existed.
"""
import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engine import strategy as S  # noqa: E402

W = (dt.date(2019, 1, 1), dt.date(2026, 6, 30))
P = {"from": "2025-07-01", "to": "2026-06-30"}


def leg(side="sell", ty="CE", strike=None, **kw):
    return dict({"side": side, "type": ty,
                 "strike": strike if strike is not None else {"pct_offset": 1.0}}, **kw)


def spec(**kw):
    base = {"legs": [leg(), leg("sell", "PE", {"pct_offset": -1.0})],
            "entry": {"cadence": "weekly", "dte": 2, "time": "09:30"}, "period": P}
    base.update(kw)
    return base


def parse(**kw):
    return S.parse(spec(**kw), window=W)


# ------------------------------------------------------------------ what it accepts

@pytest.mark.parametrize("legs,shape", [
    ([leg("sell", "CE"), leg("sell", "PE", {"pct_offset": -1.0})], "short_strangle"),
    ([leg("sell", "CE", "atm"), leg("sell", "PE", "atm")], "short_straddle"),
    ([leg("buy", "CE", "atm"), leg("buy", "PE", "atm")], "long_straddle"),
    ([leg("sell", "CE"), leg("buy", "CE", {"pct_offset": 2.0})], "vertical_spread"),
    ([leg("sell", "CE"), leg("buy", "CE", {"pct_offset": 2.0}),
      leg("sell", "PE", {"pct_offset": -1.0}),
      leg("buy", "PE", {"pct_offset": -2.0})], "iron_condor"),
    ([leg("sell", "CE"), leg("buy", "CE", {"pct_offset": 2.0}),
      leg("sell", "PE", {"pct_offset": -1.0}),
      leg("buy", "PE", {"pct_offset": -3.0})], "broken_wing_condor"),
    ([leg("buy", "CE", {"pct_offset": 0.5}),
      leg("sell", "CE", {"pct_offset": 1.5}, qty=2)], "ratio"),
    ([leg("sell", "CE", "atm", expiry="near"),
      leg("buy", "CE", "atm", expiry="next")], "calendar"),
    ([leg("sell", "CE", {"pct_offset": 0.5}, expiry="near"),
      leg("buy", "CE", {"pct_offset": 1.5}, expiry="next")], "diagonal"),
    ([leg("sell", "PE", {"pct_offset": -1.0}), leg("sell", "CE"),
      leg("buy", "CE", {"pct_offset": 1.5})], "jade_lizard"),
])
def test_the_classic_shapes_are_recognised_from_the_legs(legs, shape):
    """The shape is DERIVED, not declared. A reader should see 'iron_condor' when the legs
    form one and an honest 'custom_5_leg' when they do not -- never the nearest preset the
    position does not actually match."""
    assert S.parse(spec(legs=legs), window=W).structure == shape


@pytest.mark.parametrize("strike", [
    {"pct_offset": 1.0}, {"points_offset": 300}, "atm", 24000,
    {"premium_near": 50}, {"delta_near": 0.2}, {"pct_offset": 1.0, "ref": "entry"},
])
def test_every_way_of_naming_a_strike_parses(strike):
    parse(legs=[leg("sell", "CE", strike)])


def test_a_rule_can_change_the_position_not_only_end_it():
    st = parse(rules=[
        {"when": {"leg_mark_delta": {"leg": 0, "gte": 30}},
         "then": {"roll": {"legs": [0], "to": {"pct_offset": 1.5}}}, "max_times": 3}],
        max_adjustments=3)
    assert st.rules[0]["then"]["do"] == "roll"
    assert "roll leg(s) [0]" in "\n".join(st.describe())


def test_book_level_rules_are_separate_from_position_rules():
    """'Stand down after three losers' depends on trades that have already closed, so no
    per-trade condition can express it."""
    st = parse(portfolio={"stop_after_losses": 3, "skip_after_loss": True})
    assert st.portfolio == {"stop_after_losses": 3, "skip_after_loss": True}


def test_entry_may_be_any_minute_of_the_session():
    """v1 allowed nine clock times because a table had nine columns. Entering at 09:20 is
    not exotic, it is Tuesday."""
    for t in ("09:15", "09:20", "11:07", "14:45", "15:29"):
        assert parse(entry={"cadence": "weekly", "dte": 2, "time": t}).entry_minute


# ----------------------------------------------------------------- what it refuses

@pytest.mark.parametrize("bad,says", [
    ({"legs": []}, "list of what to open"),
    ({"legs": [leg("hold")]}, "sell' or 'buy"),
    ({"legs": [{"side": "sell", "type": "XX", "strike": 1}]}, "'CE' or 'PE'"),
    ({"legs": [leg()], "entry": {"cadence": "fortnightly"}}, "cadence"),
    ({"legs": [leg()], "entry": {"time": "08:00"}}, "outside the trading session"),
    ({"legs": [leg()], "entry": {"time": "nine"}}, "clock time"),
    ({"legs": [leg()], "rules": [{"when": {"nope": {"gte": 1}}, "then": "close"}]},
     "unknown field"),
    ({"legs": [leg()], "rules": [{"when": {"leg_mark": {"gte": 1}}, "then": "close"}]},
     "needs \"leg\""),
    ({"legs": [leg()], "rules": [{"when": {"leg_mark": {"leg": 9, "gte": 1}},
                                  "then": "close"}]}, "leg index"),
    ({"legs": [leg()], "rules": [{"when": {"pnl_pts": {"gte": 1, "lte": 2}},
                                  "then": "close"}]}, "exactly one comparator"),
    ({"legs": [leg()], "rules": [{"when": {"pnl_pts": {"gte": 1}},
                                  "then": {"teleport": {}}}]}, "unknown action"),
    ({"legs": [leg()], "portfolio": {"stop_when_sad": True}}, "unknown field"),
    ({"legs": [leg()], "resolution": 7}, "1, 5 or 15"),
    ({"legs": [leg()], "symbol": "BANKNIFTY"}, "only NIFTY"),
])
def test_a_bad_strategy_is_refused_with_a_reason(bad, says):
    bad.setdefault("period", P)
    with pytest.raises(S.StrategyError) as e:
        S.parse(bad, window=W)
    assert says in str(e.value), str(e.value)


def test_a_rule_that_cannot_terminate_is_refused():
    """max_adjustments bounds the loop. A rule that mutates the position with a budget of
    zero would either never fire or never stop, and both are worse than being told."""
    with pytest.raises(S.StrategyError) as e:
        parse(max_adjustments=0,
              rules=[{"when": {"pnl_pts": {"lte": -10}},
                      "then": {"roll": {"legs": [0], "to": "atm"}}}])
    assert "max_adjustments is 0" in str(e.value)


def test_two_rules_with_the_same_condition_are_refused():
    """Only the first could ever fire. Running it as written is how someone concludes
    their second stop 'did not work'."""
    r = {"when": {"pnl_pts": {"lte": -50}}, "then": "close"}
    with pytest.raises(S.StrategyError) as e:
        parse(rules=[r, dict(r)])
    assert "same condition" in str(e.value)


def test_the_tier_gates_the_window_and_nothing_else():
    """The whole design commitment in one test: a free key gets every leg, every rule,
    every selector -- only a shorter history."""
    free = (dt.date(2025, 7, 1), dt.date(2026, 6, 30))
    rich = spec(legs=[leg("sell", "CE", {"delta_near": 0.15}),
                      leg("buy", "CE", "atm", expiry="next")],
                rules=[{"when": {"drawdown_from_peak": {"gte": 20}}, "then": "close"}],
                portfolio={"stop_after_losses": 3})
    assert S.parse(rich, window=free).n_legs == 2          # accepted on the free window
    rich["period"] = {"from": "2019-01-01", "to": "2026-06-30"}
    with pytest.raises(S.StrategyError) as e:
        S.parse(rich, window=free)
    assert "ONLY thing a tier changes" in str(e.value)


def test_a_time_band_accepts_clock_strings_like_every_other_time_comparison():
    """`time` took "09:15" under gt/gte/lt/lte/eq and a bare number under `between` -- so
    "only during the first half hour", the whole reason a time band exists, was the one
    form that failed."""
    band = S.parse({"legs": [{"side": "sell", "type": "CE", "strike": "atm"}],
                    "entry": {"cadence": "weekly", "dte": 2, "time": "09:30"},
                    "rules": [{"when": {"time": {"between": ["09:15", "09:45"]}},
                               "then": "close"}]},
                   window=(dt.date(2025, 7, 1), dt.date(2026, 6, 30)))
    cond = band.rules[0]["when"]
    assert cond["bound"] == [555, 585]
    assert "09:15–09:45" in S._cond_words(cond)
    with pytest.raises(S.StrategyError):
        S.parse({"legs": [{"side": "sell", "type": "CE", "strike": "atm"}],
                 "entry": {"cadence": "weekly", "dte": 2, "time": "09:30"},
                 "rules": [{"when": {"time": {"between": ["09:45", "09:15"]}},
                            "then": "close"}]},
                window=(dt.date(2025, 7, 1), dt.date(2026, 6, 30)))


def test_the_market_state_fields_are_the_reason_for_the_trade():
    """The protocol could express any position and any way of managing it, and could not
    express WHY it was taken. These seven fields are that gap closed."""
    s = S.parse({"legs": [{"side": "sell", "type": "CE", "strike": "atm"}],
                 "entry": {"cadence": "monthly", "time": "09:30",
                           "when": {"all": [{"vix": {"gte": 15}},
                                            {"prev_day_move_pct": {"lte": -1.0}},
                                            {"day_of_week": {"between": [1, 5]}},
                                            {"realised_vol_20d": {"lt": 20}},
                                            {"gap_pct": {"between": [-0.5, 0.5]}}]}},
                 "rules": [{"when": {"vix_change_pct": {"gte": 10}}, "then": "close"}]},
                window=(dt.date(2025, 7, 1), dt.date(2026, 6, 30)))
    assert s.cadence == "monthly" and s.entry_dte == 21    # a month's cadence, not a week's
    assert s.needs_vix                                     # so the minute series is fetched
    assert not S.parse({"legs": [{"side": "sell", "type": "CE", "strike": "atm"}],
                        "entry": {"cadence": "weekly", "dte": 2, "time": "09:30",
                                  "when": {"realised_vol_20d": {"lt": 20}}}},
                       window=(dt.date(2025, 7, 1), dt.date(2026, 6, 30))).needs_vix
    # a weekday outside the week is a typo, not a rule that silently never matches
    with pytest.raises(S.StrategyError, match="Monday"):
        S.parse({"legs": [{"side": "sell", "type": "CE", "strike": "atm"}],
                 "entry": {"cadence": "weekly", "dte": 2, "time": "09:30",
                           "when": {"day_of_week": {"eq": 9}}}},
                window=(dt.date(2025, 7, 1), dt.date(2026, 6, 30)))
