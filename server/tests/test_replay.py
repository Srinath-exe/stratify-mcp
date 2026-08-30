"""The replay screen and the tracks behind it.

The tests that matter most here are not about the UI. They are about the boundary: the
replay makes an options position watchable minute by minute, and the only reason it is
allowed to exist is that every series on it is either the index or a signed sum over two or
more legs. A future edit that adds `entry_price` to a leg -- which would look like a
harmless UI improvement in a diff -- is a data leak, and these are what catch it.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engine import replay  # noqa: E402
from server import replay_view  # noqa: E402

LEGS4 = [{"action": "SELL", "type": "CE", "strike": 25400},
         {"action": "BUY", "type": "CE", "strike": 25650},
         {"action": "SELL", "type": "PE", "strike": 24900},
         {"action": "BUY", "type": "PE", "strike": 24650}]
LEGS2 = [{"action": "SELL", "type": "CE", "strike": 25400},
         {"action": "SELL", "type": "PE", "strike": 24900}]


def raw_bars(n=6, credit=40.0):
    # Minutes roll over the hour, which the encoder's calendar conversion has to survive.
    bars = [{"t": f"2025-07-15 {9 + (30 + i) // 60:02d}:{(30 + i) % 60:02d}",
             "o": 25100.0 + i, "h": 25120.0 + i, "l": 25080.0 + i, "c": 25100.0 + i,
             "p": credit - i * 2}
            for i in range(n)]
    bars[-1]["x"] = "EXPIRY"
    return bars


def trade(legs=None, n=6, credit=40.0):
    legs = LEGS4 if legs is None else legs
    bars = raw_bars(n, credit)
    return {"n": 1, "entry": "2025-07-15 09:30", "exit": "2025-07-15 09:35",
            "expiry": "2025-07-17", "dte": 2, "exit_reason": "EXPIRY",
            "spot_entry": 25100.0, "legs": legs, "entry_credit": credit,
            "net_points": 10.0, "margin_points": 250.0, "lot_size": 75,
            "charges_rupees": 120.0, "pnl_rupees": 630.0,
            "i0": 0, "i1": n - 1,
            "p": replay._quant([b["p"] for b in bars])}


def track(legs=None, n=6, credit=40.0, bucket=1):
    """The whole payload: one continuous index series plus trade overlays into it."""
    return {"spot": replay._encode(raw_bars(n, credit)),
            "expiries": [{"i": n - 1, "date": "2025-07-17"}],
            "trades": [trade(legs, n, credit)],
            "bucket_minutes": bucket}


PAYLOAD = {"spec": {"structure": "iron_condor",
                    "params": {"pct_offset": 1.0, "pct_width": 1.0}},
           "summary": {"period": {"from": "2025-07-01", "to": "2026-06-30"}}}


# ------------------------------------------------------------------ the boundary

def test_a_leg_carries_a_strike_and_a_side_and_nothing_else():
    replay.release_audit(track())
    leaky = track()
    t = leaky["trades"][0]
    t["legs"] = [dict(t["legs"][0], entry_price=61.25)] + t["legs"][1:]
    with pytest.raises(AssertionError, match="non-strike"):
        replay.release_audit(leaky)


def test_the_index_series_carries_no_column_the_module_did_not_define():
    leaky = track()
    leaky["spot"]["ce"] = [61.25]
    with pytest.raises(AssertionError, match="unexpected columns"):
        replay.release_audit(leaky)


def test_a_trade_carries_no_field_the_module_did_not_define():
    """An allow-list, not a deny-list: a new field has to be considered rather than merely
    not-yet-forbidden."""
    leaky = track()
    leaky["trades"][0]["leg_marks"] = [61.25, 12.0]
    with pytest.raises(AssertionError, match="unexpected fields"):
        replay.release_audit(leaky)


def test_the_audit_reports_a_zero_price_release():
    assert replay.release_audit(track())["price_points_released"] == 0


def test_a_single_leg_position_is_refused():
    """A one-leg 'combined premium' IS that leg's price. `long_option` is a structure the
    engine supports, so this is a live hole rather than a theoretical one."""
    with pytest.raises(replay.NotReleasable, match="two or more legs"):
        replay.release_audit(track(legs=LEGS4[:1]))


def test_a_per_leg_pnl_is_nowhere_on_the_page():
    """A per-leg P&L is a per-leg price by another name. The page says so and shows none."""
    doc = replay_view.render(PAYLOAD, track())
    assert "call side" not in doc and "put side" not in doc
    assert "Per-leg P&amp;L is not shown" in doc


def test_no_option_price_reaches_the_rendered_page():
    doc = replay_view.render(PAYLOAD, track())
    assert "entry_price" not in doc and "leg_price" not in doc
    blob = json.loads(doc.split("window.__REPLAY__=")[1].split(";</script>")[0])
    for tr in blob["trades"]:
        for leg in tr["legs"]:
            assert set(leg) == {"action", "type", "strike"}


# ------------------------------------------------------------------ the encoding

def test_the_encoding_round_trips_exactly():
    """Prices are stored as integer twentieths because NIFTY ticks in 0.05. If that ever
    stops being true the data is being silently rounded, which a chart would hide."""
    src = raw_bars(40)
    B = replay._encode(src)
    t, c = B["t0"], 0
    for i, want in enumerate(src):
        if i:
            t += B["dt"][i] * 60
        c += B["c"][i]
        assert c / B["q"] == pytest.approx(want["c"])
        assert (c + B["o"][i]) / B["q"] == pytest.approx(want["o"])
        assert (c + B["h"][i]) / B["q"] == pytest.approx(want["h"])
        assert (c + B["l"][i]) / B["q"] == pytest.approx(want["l"])
        assert B["p"][i] / B["q"] == pytest.approx(want["p"])
    assert B["n"] == len(src)


def test_the_timeline_survives_a_short_month():
    """The first encoder packed the date fields arithmetically, which is exact within a
    month and wrong across a short one -- 28 Feb to 1 Mar came out as four days. The client
    rebuilds timestamps by accumulating these, so every bar after it would have been
    dragged out of position."""
    assert replay._minutes("2026-03-01 09:15") - replay._minutes("2026-02-28 09:15") == 1440
    assert replay._minutes("2025-01-01 09:15") - replay._minutes("2024-12-31 09:15") == 1440


def test_expiries_are_marked_by_index_into_the_continuous_series():
    t = track(n=9)
    assert t["expiries"] == [{"i": 8, "date": "2025-07-17"}]


def test_a_trade_is_a_span_within_the_one_index_series():
    """The chart is continuous now; a trade is i0..i1 with its premium aligned bar for bar.
    A mismatch here would draw the position over the wrong candles."""
    t = trade(n=12)
    assert len(t["p"]) == t["i1"] - t["i0"] + 1


def test_the_exit_marker_is_kept_as_a_sparse_index():
    B = replay._encode(raw_bars(30))
    assert B["x"] == {"29": "EXPIRY"}


def test_an_empty_trade_still_encodes_to_a_usable_shape():
    B = replay._encode([])
    assert B["n"] == 0 and B["dt"] == []


def test_a_column_of_nothing_is_not_shipped():
    """The continuous index series carries no position data, so `p` was coming out as
    92,000 literal nulls -- half a megabyte of payload saying nothing, on a series where
    the field has no meaning."""
    plain = [{"t": "2025-07-15 09:30", "o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0, "p": None}]
    B = replay._encode(plain)
    assert "p" not in B and "x" not in B
    assert "p" in replay._encode(raw_bars(3))


# ------------------------------------------------------------------ the page

def test_the_page_renders_and_carries_the_disclaimer():
    doc = replay_view.render(PAYLOAD, track())
    assert doc.startswith("<!doctype html>")
    assert "Not investment advice" in doc and "Iron Condor" in doc


def test_the_chart_credit_survives():
    """Lightweight Charts is Apache-2.0 and the licence requires the attribution."""
    assert "attributionLogo:true" in replay_view.render(PAYLOAD, track())


def test_the_shared_svg_helpers_are_aliased_before_anything_can_shadow_them():
    doc = replay_view.render(PAYLOAD, track())
    assert "var S_=S,T_=T;" in doc
    assert doc.index("var S_=S,T_=T;") < doc.index("var D = window.__REPLAY__")


def test_the_timeframe_selector_never_offers_finer_than_the_data():
    """Claiming a resolution the bars do not have is the one thing a chart must not do."""
    coarse = track(bucket=5)
    doc = replay_view.render(PAYLOAD, coarse)
    assert 'data-tf="1"' not in doc
    assert 'data-tf="5"' in doc and 'data-tf="60"' in doc
    assert 'data-tf="1"' in replay_view.render(PAYLOAD, track())


def test_exit_rules_are_stated_even_when_there_are_none():
    assert "no stop, no target" in replay_view.render(PAYLOAD, track())
    guarded = dict(PAYLOAD, spec=dict(PAYLOAD["spec"],
                                      params={"pct_offset": 1.0, "sl_mult": 2.0}))
    assert "stop 2.0× credit" in replay_view.render(guarded, track())


def test_capital_and_deploy_reach_the_client():
    doc = replay_view.render(PAYLOAD, track(), capital=2_500_000, deploy=0.25)
    blob = json.loads(doc.split("window.__REPLAY__=")[1].split(";</script>")[0])
    assert blob["capital"] == 2_500_000 and blob["deploy"] == 0.25
    assert 'value="2500000"' in doc and 'value="25"' in doc


# ------------------------------------------------------------------ the arithmetic

def test_the_payoff_curve_needs_only_strikes_and_the_credit():
    """The load-bearing claim of the whole panel: if the payoff ever needs a per-leg
    price, it cannot ship."""
    tr = trade()

    def payoff(spot):
        v = tr["entry_credit"]
        for leg in tr["legs"]:
            intr = (max(spot - leg["strike"], 0) if leg["type"] == "CE"
                    else max(leg["strike"] - spot, 0))
            v -= (1 if leg["action"] == "SELL" else -1) * intr
        return v

    assert payoff(25100) == pytest.approx(tr["entry_credit"])            # max profit
    assert payoff(26500) == pytest.approx(tr["entry_credit"] - 250)      # capped, call side
    assert payoff(24000) == pytest.approx(tr["entry_credit"] - 250)      # capped, put side


def test_report_links_to_the_replay_only_when_there_is_one():
    from server import fullreport
    # One real trade row, not an empty list: a backtest with no trades renders the
    # "produced no trades" page, which correctly has nothing to replay -- so an empty
    # list tests the wrong branch of the very thing this asserts.
    row = {"n": 1, "entry": "2025-01-07 09:30", "exit": "2025-01-09 15:29",
           "exit_reason": "EXPIRY", "spot_at_entry": 23500.0, "net_points": 12.5,
           "slippage_points": 0.2, "margin_points": 150, "return_on_margin": 0.083,
           "lot_size": 75, "charges_rupees": 190.0, "pnl_rupees": 937.5,
           "holding_minutes": 3239, "dte_at_entry": 2}
    candles = [{"time": "2025-01-07", "open": 23500.0, "high": 23600.0,
                "low": 23400.0, "close": 23550.0},
               {"time": "2025-01-09", "open": 23550.0, "high": 23650.0,
                "low": 23500.0, "close": 23600.0}]
    args = ({"spec": {"structure": "iron_condor"}, "summary": {}}, [row], candles, "bt_1")
    assert "Watch it trade" not in fullreport.render(*args)
    assert "Watch it trade" in fullreport.render(*args, replay_url="x-replay.html")


def test_the_base_resolution_is_derived_from_the_span():
    """A year at 1-minute is a fair download; seven years at 1-minute is five megabytes to
    look at a chart nobody zooms into that far."""
    assert replay.base_bucket(250) == 1
    assert replay.base_bucket(1750) > 1


def test_every_offered_timeframe_divides_the_base():
    """A 25-minute base aggregated into 30-minute candles gives buckets holding one bar and
    buckets holding two -- uneven candles presented as even ones."""
    for days in (250, 750, 1750, 5000):
        b = replay.base_bucket(days)
        for tf in replay_view.TIMEFRAMES:
            if tf >= b:
                assert tf % b == 0, f"{tf}m does not divide a {b}m base"


def test_speed_is_market_time_not_a_multiplier():
    """"3x" is meaningless until you know what it is three times. "30 min / sec" says a
    session passes in twelve and a half seconds, which a reader can picture beforehand."""
    doc = replay_view.render(PAYLOAD, track())
    for label in ("5 min / sec", "15 min / sec", "1 hour / sec", "4 hours / sec"):
        assert f">{label}</option>" in doc, label
    assert replay_view.DEFAULT_SPEED in [v for v, _ in replay_view.SPEEDS]


def test_speed_is_a_select_and_the_timeframe_is_not():
    """As buttons the speeds sat beside the candle-resolution row looking identical to it
    -- two strips of 5m/15m/30m/1h meaning entirely different things."""
    doc = replay_view.render(PAYLOAD, track())
    assert 'id="spd"' in doc and "data-speed=" not in doc
    assert 'data-tf="15"' in doc          # the timeframe stays a button row
    assert doc.count('<span class="tag">speed</span>') == 1


def test_the_base_bar_length_reaches_the_client():
    """Speed is market minutes per second, and one base bar is BASE minutes of market. Get
    that wrong and every speed is off by the timeframe factor."""
    doc = replay_view.render(PAYLOAD, track(bucket=5))
    blob = json.loads(doc.split("window.__REPLAY__=")[1].split(";</script>")[0])
    assert blob["base"] == 5


def test_the_replay_does_not_skip_the_flat_stretches():
    """Waiting is what the strategy does most of the time; teleporting over it hides both
    how long the account sits idle and what the index did to the levels the next position
    will be built around."""
    doc = replay_view.render(PAYLOAD, track())
    assert "ST.gi = tr.i0; advanceSpot(); openIt(tr);" not in doc
    assert "if(!ST.open && ST.gi >= tr.i0) openIt(tr);" in doc


def test_the_expiry_rule_names_itself():
    assert "' EXPIRY'" in replay_view.render(PAYLOAD, track())


def test_the_sizing_controls_offer_a_compounding_cadence():
    """Sizing off running equity after every trade is the most aggressive reading there
    is: it presses hardest right after a lucky run and shrinks every position at the worst
    moment of a drawdown. Nobody trades that way -- money moves on a cadence -- so the page
    has to offer one. On this strategy the choice is worth 3.5 points of return."""
    doc = replay_view.render(PAYLOAD, track())
    for mode in ("trade", "week", "month", "quarter", "never"):
        assert f'value="{mode}"' in doc, mode
    assert "compounds <b>" in doc


def test_margin_per_lot_is_shown_and_not_only_the_total():
    """What one lot ties up decides how many lots the account can carry, and it is the one
    figure a reader cannot derive from anything else on the page."""
    doc = replay_view.render(PAYLOAD, track())
    assert "marginPerLot" in doc and "/lot" in doc


def test_the_lot_count_is_fixed_at_entry():
    """sizingBase() advances the compounding period as a side effect, so recomputing lots
    on every repaint would both corrupt the cadence and resize a position mid-trade."""
    doc = replay_view.render(PAYLOAD, track())
    assert "ST.lots = lotsFor(sizingBase(tr), tr)" in doc
