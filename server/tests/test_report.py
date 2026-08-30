"""The tearsheet: what it computes, and the ways it has already been wrong.

Every test here is either an invariant a reader would rely on -- the calendar has to
compound to the annual table, the concentration counterfactual has to re-run the capital
model -- or a regression for a bug that shipped once. The regressions are named after what
they broke, because a test called "test_grid_class" tells nobody why it exists.
"""
import datetime as dt
import math
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server import analytics, fullreport, sizing   # noqa: E402

LOT = 75
START = dt.date(2020, 1, 7)


def trades(n=120, every=7, pnl=None):
    """A weekly book. Deterministic on purpose: a fixture that moves makes every
    reconciliation test below untestable."""
    out = []
    for i in range(n):
        entry = START + dt.timedelta(days=i * every)
        exit_ = entry + dt.timedelta(days=2)
        pts = pnl(i) if pnl else (18.0 if i % 4 else -46.0)
        out.append({
            "n": i + 1,
            "entry": f"{entry} 09:30", "exit": f"{exit_} 15:29",
            "expiry": str(exit_), "dte_at_entry": 2, "exit_reason": "EXPIRY",
            "spot_at_entry": 12000 + i * 30,
            "gross_points": pts + 0.2, "slippage_points": 0.2, "net_points": pts,
            "margin_points": 150.0, "return_on_margin": round(pts / 150.0, 5),
            "lot_size": LOT, "charges_rupees": 190.0,
            "pnl_rupees": round(pts * LOT - 190.0, 2), "holding_minutes": 3239,
        })
    return out


def candles(days=900):
    out, px = [], 12000.0
    for i in range(days):
        d = START - dt.timedelta(days=20) + dt.timedelta(days=i)
        px *= 1.0004 + (0.004 if i % 11 == 0 else -0.0006 if i % 5 == 0 else 0)
        out.append({"time": str(d), "open": px * .999, "high": px * 1.004,
                    "low": px * .996, "close": round(px, 2)})
    return out


PAYLOAD = {
    "spec": {"structure": "iron_condor", "cadence": "weekly", "entry_time": "09:30",
             "params": {"pct_offset": 1.0, "pct_width": 1.0, "entry_dte": 2},
             "period": {"from": "2020-01-01", "to": "2022-06-30"}},
    "summary": {"period": {"from": "2020-01-01", "to": "2022-06-30"}, "win_rate": 0.75,
                "ratios": {"sharpe": 0.62}},
    "honesty": {"verdict": "mixed_evidence", "health_score": 52,
                "out_of_sample": {"held_up": False,
                                  "in_sample": {"n_trades": 84, "pnl_rupees": 4000.0},
                                  "out_of_sample": {"n_trades": 36, "pnl_rupees": -900.0}},
                "walk_forward": [{"fold": 1, "from": "a", "to": "b", "n_trades": 40,
                                  "pnl_rupees": 2000.0, "profitable": True},
                                 {"fold": 2, "from": "c", "to": "d", "n_trades": 40,
                                  "pnl_rupees": -1200.0, "profitable": False},
                                 {"fold": 3, "from": "e", "to": "f", "n_trades": 40,
                                  "pnl_rupees": 900.0, "profitable": True}],
                "bootstrap_ci_95_return_on_margin": [-0.02, 0.05],
                "multiple_comparisons": {"deflated_sharpe_probability": 0.71,
                                         "variants_tested_last_24h": 3,
                                         "reading": "71% probability the Sharpe is real"}},
    "interpretation": {"do_not_conclude": ["That this is advice."]},
}


def build(t=None, c=None, **kw):
    return analytics.build(t or trades(), c or candles(), **kw)


def render(t=None, c=None, **kw):
    return fullreport.render(PAYLOAD, t or trades(), c or candles(), "bt_test", **kw)


# ------------------------------------------------------------------ reconciliation

def test_the_analytics_and_the_capital_model_agree_on_the_ending_figure():
    """Two paths to the same number is two numbers unless something forces them equal.
    A page whose headline and whose curve disagree is a page nobody can check."""
    t = trades()
    a = build(t)
    direct = sizing.apply(sorted(t, key=lambda x: (x["exit"], x["entry"])))
    assert a["sized"]["ending_capital"] == direct["ending_capital"]
    assert a["equity"][-1]["equity"] == pytest.approx(direct["ending_capital"])


def test_the_calendar_compounds_to_the_annual_table():
    """The heatmap and the year table are two views of one series. They were computed
    separately, so this is the only thing stopping them drifting apart."""
    a = build()
    for year in a["annual"]:
        months = [c for c in a["monthly"]["cells"] if c["year"] == year["year"]]
        chained = 1.0
        for c in months:
            chained *= (1 + c["ret"])
        assert chained - 1 == pytest.approx(year["ret"], abs=1e-9)


def test_the_worst_episode_is_the_reported_maximum_drawdown():
    a = build()
    assert a["episodes"][0]["depth"] == pytest.approx(a["quality"]["max_dd"])
    assert min(u["dd"] for u in a["underwater"]) == pytest.approx(a["quality"]["max_dd"])


# --------------------------------------------------------------------- behaviour

def test_removing_the_best_trades_re_runs_the_capital_model():
    """Subtracting their rupees is the tempting shortcut and it is wrong under
    compounding: every later trade was sized off an account that no longer holds the
    removed gain. The counterfactual must differ from naive subtraction."""
    a = build()
    ranked = sorted(a["scatter"], key=lambda p: p["pnl"], reverse=True)
    naive = a["sized"]["ending_capital"] - sum(p["pnl"] for p in ranked[:5])
    assert a["concentration"]["ex_best5"] != pytest.approx(naive, rel=1e-6)
    assert a["concentration"]["ex_best5"] < a["sized"]["ending_capital"]


def test_no_annual_growth_rate_is_reported_for_a_window_too_short_to_have_one():
    """Annualising a two-day result raises a daily return to the power of 182. The
    answer is meaningless, and for a same-day window it is an OverflowError."""
    a = build(trades(n=2, every=1))
    assert a["quality"]["cagr"] is None
    assert a["sized"]["cagr_pct"] is None


def test_return_on_margin_is_derived_when_the_rows_do_not_carry_it():
    """A row with net points and a margin has said everything the ratio needs. Refusing
    to compute it dropped the entire risk section over a naming difference."""
    t = [{k: v for k, v in row.items() if k != "return_on_margin"} for row in trades()]
    a = build(t)
    assert a["distribution"] is not None
    assert a["distribution"]["median"] == pytest.approx(build()["distribution"]["median"])


def test_volatility_bands_are_cut_on_the_market_not_on_the_trades():
    """Cutting terciles on the trades guarantees three equal groups whatever the market
    did, which hides the case that matters: almost no evidence outside one regime."""
    a = build()
    counts = [b["n"] for b in a["regimes"]["vol"]]
    assert sum(counts) == len(a["scatter"])
    assert len(set(counts)) > 1, "equal buckets means they were cut on the trades"


def test_a_partial_row_costs_one_panel_not_the_whole_document():
    t = [{k: v for k, v in r.items() if k not in ("charges_rupees", "slippage_points")}
         for r in trades()]
    doc = render(t)
    assert doc.startswith("<!doctype html>")


# --------------------------------------------------------------------- the page

def test_every_placeholder_is_resolved():
    """An f-string that loses its braces prints '{q[max_dd]}' to a customer. Scoped to
    the markup: the inlined scripts are full of legitimate JavaScript braces."""
    doc = render()
    markup = doc[doc.index("<body>"):doc.index("<script>")]
    leftovers = re.findall(r"\{[A-Za-z_][A-Za-z0-9_\[\]\"'. ]*\}", markup)
    assert not leftovers, leftovers


def test_the_layout_grid_does_not_reuse_the_class_that_means_green():
    """SHIPPED ONCE. The 12-column grid was '.g' and so was the positive-number colour,
    so every green figure on the page silently became a twelve-column grid: values
    stacked, bars vanished, the calendar's year column tore open."""
    assert ".lay{display:grid" in fullreport.CSS
    assert ".g{color:var(--good)}" in fullreport.CSS
    assert not re.search(r"\.g\{display:grid", fullreport.CSS)
    assert 'class="g"' not in render()


def test_the_trade_table_is_gone_and_the_scatter_replaced_it():
    doc = render()
    assert 'id="sct"' in doc and 'data-f="all"' in doc
    assert "<th>Why</th>" not in doc          # the old per-trade table's header
    assert doc.count("<tr>") < 80, "a 120-trade book should not print 120 rows"


def test_a_short_window_says_so_instead_of_drawing_an_empty_panel():
    short = render(trades(n=20))
    assert 'id="roll"' not in short
    assert "too short to have a full year" in short
    assert 'id="roll"' in render()


def test_the_findings_are_ranked_worst_first():
    a = build()
    out = fullreport.findings(a, PAYLOAD["honesty"], PAYLOAD["summary"], 1_000_000)
    order = {"bad": 0, "warn": 1, "ok": 2}
    assert [order[t] for t, _, _ in out] == sorted(order[t] for t, _, _ in out)
    assert any("held-out" in head for _, head, _ in out)


def test_no_trades_renders_a_page_that_says_so_rather_than_a_wall_of_dashes():
    doc = fullreport.render(PAYLOAD, [], candles(), "bt_empty")
    assert "produced no trades" in doc
    assert "id=\"eq\"" not in doc


def test_the_page_declares_both_themes_and_never_paints_only_inside_one():
    css = fullreport.CSS
    assert ":root[data-theme=dark]" in css and "prefers-color-scheme:dark" in css
    # The heat ramp must exist on bare :root as well as in both dark blocks, or a month
    # cell is the one colour on the page that ignores the viewer's theme.
    bare = re.findall(r":root\{([^}]*)\}", css)
    assert any("--h5:" in b for b in bare), "no light definition on bare :root"
    assert css.count("--h5:") == 3


def test_the_charting_licence_notice_survives():
    assert "Licensed under Apache License 2.0" in fullreport.LWC[:400]
    assert "attributionLogo:true" in fullreport.JS


# ------------------------------------------------------------------ the payoff

CONDOR = {"structure": "iron_condor",
          "params": {"pct_offset": 1.0, "pct_width": 1.0, "entry_dte": 2}}
STRANGLE = {"structure": "short_strangle", "params": {"pct_offset": 1.5, "entry_dte": 2}}


# The generic fixture above is a smooth synthetic index whose trades are unrelated to it,
# which is fine for equity arithmetic and useless here: the payoff panel is precisely the
# join between the two. So these tests build a book that IS a condor -- gross points
# computed from the position's own payoff against the index move that actually happened --
# and then check the report recovers the geometry it was built from.
CREDIT, OFFSET, WIDTH, SPOT0 = 40.0, 1.0, 1.0, 12000.0


def _intr(kind, k, s_):
    return max(s_ - k, 0.0) if kind == "CE" else max(k - s_, 0.0)


def moving_candles(days=900):
    """A deterministic index that actually moves: a slow drift with a repeating wobble
    large enough that trades land outside the strikes as well as inside."""
    out, px = [], SPOT0
    for i in range(days):
        d = START - dt.timedelta(days=20) + dt.timedelta(days=i)
        px *= 1 + 0.011 * math.sin(i / 3.7) + 0.004 * math.cos(i / 1.3) + 0.0002
        out.append({"time": str(d), "open": px, "high": px * 1.004,
                    "low": px * .996, "close": round(px, 2)})
    return out


def condor_trades(cands, n=120, every=7, structure=CONDOR):
    close = {c["time"]: c["close"] for c in cands}
    days = sorted(close)
    legs = fullreport._geometry(structure)
    out = []
    for i in range(n):
        entry = START + dt.timedelta(days=i * every)
        exit_ = entry + dt.timedelta(days=2)
        ed, xd = str(entry), str(exit_)
        if ed not in close or xd not in close:
            continue
        spot, fin = close[ed], close[xd]
        credit = CREDIT * spot / SPOT0                   # scale with the index
        gross = credit + sum(
            sign * _intr(kind, spot * (1 + k / 100), fin) for kind, sign, k in legs)
        net = gross - 0.2
        out.append({
            "n": len(out) + 1, "entry": f"{ed} 09:30", "exit": f"{xd} 15:29",
            "expiry": xd, "dte_at_entry": 2, "exit_reason": "EXPIRY",
            "spot_at_entry": spot, "gross_points": gross, "slippage_points": 0.2,
            "net_points": net, "margin_points": spot * WIDTH / 100,
            "return_on_margin": net / (spot * WIDTH / 100), "lot_size": LOT,
            "charges_rupees": 190.0, "pnl_rupees": net * LOT - 190.0,
            "holding_minutes": 3239})
    return out


def _pos(spec=None, t=None, c=None):
    c = c or moving_candles()
    t = t if t is not None else condor_trades(c, structure=spec or CONDOR)
    a = analytics.build(t, c)
    return fullreport.position(spec or CONDOR, t, a["moves"])


def test_a_short_spread_loses_when_the_index_runs_through_it():
    """SHIPPED ONCE, AND IT DREW THE OPPOSITE OF THE TRUTH. P&L is credit PLUS
    sign x intrinsic (sold = -1). Written with a minus, an iron condor's payoff rose as
    the index ran through the short call -- the picture said the position profited from
    exactly the move it exists to fear."""
    p = _pos()
    spot, legs = p["spot"], p["legs"]

    def payoff(pct):
        s_ = spot * (1 + pct / 100)
        return p["credit"] + sum(
            l["s"] * (max(s_ - spot * (1 + l["k"] / 100), 0) if l["t"] == "CE"
                      else max(spot * (1 + l["k"] / 100) - s_, 0)) for l in legs)

    assert payoff(0) == pytest.approx(p["credit"])          # flat: keep the credit
    assert payoff(5) < 0 and payoff(-5) < 0                 # far either way: a loss
    assert payoff(5) == pytest.approx(payoff(9))            # and the wings cap it


def test_the_maximum_loss_is_the_wing_width_less_the_credit():
    p = _pos()
    width = p["spot"] * WIDTH / 100
    assert p["max_loss"] == pytest.approx(-(width - p["credit"]), rel=0.02)
    assert p["max_profit"] == pytest.approx(p["credit"], abs=0.06)  # 1dp vs 2dp


def test_the_breakevens_sit_one_credit_outside_the_short_strikes():
    p = _pos()
    edge = OFFSET + p["credit"] / p["spot"] * 100           # offset + credit, in per cent
    assert p["breakevens"][0] == pytest.approx(-edge, abs=0.02)
    assert p["breakevens"][-1] == pytest.approx(edge, abs=0.02)


def test_each_side_is_bounded_or_not_on_its_own():
    """A short strangle's PROFIT is exactly the credit while its LOSS is open-ended, and a
    long option is the mirror image. A single is-it-capped flag got both halves wrong: it
    reported the capped side as open-ended too, which is the side a reader sizes against."""
    st = _pos(STRANGLE, t=condor_trades(moving_candles(), structure=STRANGLE))
    assert st["max_profit"] == pytest.approx(st["credit"], abs=0.06)
    assert st["max_loss"] is None and st["capped"] is False

    lc = {"structure": "long_option", "params": {"pct_offset": 1.0, "direction": "call"}}
    lo = _pos(lc, t=condor_trades(moving_candles(), structure=lc))
    assert lo["max_profit"] is None                     # open-ended upside
    assert lo["max_loss"] == pytest.approx(lo["credit"], abs=0.06)   # the premium paid

    assert _pos(CONDOR)["capped"] is True


def test_the_share_finishing_in_profit_tracks_the_win_rate():
    """Two independent routes to nearly the same number: one from the payoff geometry
    against realised index moves, one from the engine's own P&L. A real gap between them
    means the position being drawn is not the position that was traded."""
    c = moving_candles()
    t = condor_trades(c)
    p = _pos(t=t, c=c)
    a = analytics.build(t, c)
    assert p["inside"] == pytest.approx(a["distribution"]["win_rate"], abs=0.08)


def test_the_report_is_charts_not_tables():
    """The point of the rebuild. The only <table> left is the calendar heatmap, which is
    a picture that happens to be made of cells."""
    doc = render()
    markup = doc[doc.index("<body>"):doc.index("<script>")]
    assert markup.count("<table") == 1
    assert 'class="cal"' in markup
    for panel in ("pay", "eq", "uw", "px", "sct", "hist", "conc", "top", "lev", "volsc"):
        assert f'id="{panel}"' in markup, panel


def _sized_trade(max_loss, margin, pnl, i):
    return {"entry": f"2026-01-{i:02d} 09:30", "exit": f"2026-01-{i:02d} 15:00",
            "pnl_rupees": pnl, "margin_points": margin, "lot_size": 75,
            "max_loss_points": max_loss}


def test_risk_sizing_refuses_a_position_with_no_worst_case():
    """"Risk 2% per trade" is not a size on a naked short -- its loss is unbounded, so
    there is nothing to take 2% of. Inventing a proxy (a stop that may gap, or the worst
    loss that happened to occur, which is look-ahead) is how a backtest ruins somebody."""
    bounded = [_sized_trade(200.0, 200.0, 4000.0, i) for i in range(1, 11)]
    naked = [_sized_trade(None, 3000.0, 4000.0, i) for i in range(1, 11)]
    out = sizing.apply(bounded, capital=1_000_000, risk_pct=0.02)
    # 2% of 10 lakh is 20,000; one lot risks 200 x 75 = 15,000 -> exactly one lot
    assert out["lots_median"] == 1 and out["risk_pct"] == 2.0 and out["deploy_pct"] is None
    with pytest.raises(sizing.SizingError, match="no bounded worst case"):
        sizing.apply(naked, capital=1_000_000, risk_pct=0.02)
    with pytest.raises(sizing.SizingError, match="uncovered short"):
        sizing.apply(bounded[:5] + naked[:5], capital=1_000_000, risk_pct=0.02)


def test_risk_sizing_cannot_block_margin_the_account_does_not_have():
    """A tight defined-risk spread has a small worst case and a real margin requirement.
    Sizing on risk alone would take hundreds of lots and silently assume capital nobody
    had; the margin the account can actually block is the binding constraint."""
    cheap_risk_heavy_margin = [_sized_trade(2.0, 400.0, 500.0, i) for i in range(1, 11)]
    out = sizing.apply(cheap_risk_heavy_margin, capital=1_000_000, risk_pct=0.02)
    # risk alone would allow 20000 / (2 x 75) = 133 lots; margin allows 1000000/30000 = 33.
    # The FIRST trade is the binding one -- compounding lifts later trades as equity grows.
    assert out["curve"][0]["lots"] == 33 and out["lots_min"] == 33
