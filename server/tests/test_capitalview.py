"""The account view: the rules card, the browser sizing loop, and the calendar.

THE LOAD-BEARING TEST IN THIS FILE is test_browser_sizing_agrees_with_the_engine. The page
recomputes position sizing in JavaScript so the controls can move without a round trip,
which means the model exists twice. Two implementations of the same arithmetic drift; the
only defence is to run both over the same series and compare, which is what that test does
with the real Node runtime rather than a re-implementation of the JS in Python.

Everything else here is either an invariant a reader relies on -- the calendar covers every
trade, not the released sample -- or a regression for a bug that shipped once.
"""
import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server import capitalview, reportui, rulecard, sizing   # noqa: E402

LOT = 75
START = dt.date(2025, 7, 1)


def _trade(i, pts, margin=150.0, every=7):
    entry = START + dt.timedelta(days=i * every)
    exit_ = entry + dt.timedelta(days=2)
    return {
        "n": i + 1, "entry": f"{entry} 09:30", "exit": f"{exit_} 15:29",
        "expiry": str(exit_), "dte_at_entry": 2, "exit_reason": "EXPIRY",
        "spot_at_entry": 25000 + i * 30, "gross_points": pts + 0.2,
        "slippage_points": 0.2, "net_points": pts, "margin_points": margin,
        "max_loss_points": 400.0,
        "return_on_margin": round(pts / margin, 5) if margin else None,
        "lot_size": LOT, "charges_rupees": 190.0,
        "pnl_rupees": round(pts * LOT - 190.0, 2), "holding_minutes": 3239,
        "legs": [{"action": "SELL", "type": "CE", "strike": 25500,
                  "entry_price": 40.0, "exit_price": 0.0, "closed_by": "EXPIRY"}],
    }


def payload(n=50, margin=150.0, released=25, curve_margin=True):
    ts = [_trade(i, 18.0 if i % 4 else -46.0, margin) for i in range(n)]
    rows, eq, peak = [], 0.0, 0.0
    for t in ts:
        eq += t["pnl_rupees"]
        peak = max(peak, eq)
        row = [t["exit"][:10], round(t["pnl_rupees"]), round(eq), round(eq - peak)]
        if curve_margin:
            row += [round(t["margin_points"] * t["lot_size"]), 2]
        rows.append(row)
    cols = ["date", "pnl_rupees", "equity_rupees", "drawdown_rupees"]
    return {
        "spec": {"legs": [{"side": "sell", "type": "CE", "strike": {"pct_offset": 1.0}}],
                 "entry": {"cadence": "weekly", "dte": 2, "time": "09:30"}},
        "summary": {"n_trades": n, "period": {"from": "2025-07-01", "to": "2026-06-30"},
                    "win_rate": 0.75, "total_pnl_rupees": round(eq, 2),
                    "ratios": {"sharpe": 0.6}},
        "honesty": {"verdict": "mixed_evidence", "health_score": 52, "rubric": []},
        "interpretation": {"reading": ["A reading."]},
        "equity_curve": {"columns": cols + (["margin_rupees", "days_held"]
                                            if curve_margin else []), "rows": rows},
        "trades": ts[:released],
        "trade_detail": {"prices_included": True, "trades_returned": released,
                         "trades_total": n},
    }


# --------------------------------------------------------------- the two implementations

# The page's own loop, lifted verbatim from capitalview.JS. Copied rather than imported
# because the point is to run the SHIPPED arithmetic, and a paraphrase of it here would
# test this file instead of the page.
HARNESS = """
function pkey(d, mode){
  if(mode==='month') return d.slice(0,7);
  if(mode==='quarter') return d.slice(0,4)+'Q'+Math.floor((+d.slice(5,7)-1)/3);
  if(mode==='week'){
    var dt = new Date(d+'T00:00:00Z');
    dt.setUTCDate(dt.getUTCDate() - ((dt.getUTCDay()+6)%7));
    return dt.toISOString().slice(0,10);
  }
  return '';
}
const D = __DATA__, cfg = __CFG__;
let eq = cfg.cap, peak = eq, taken = 0, skipped = 0, maxDD = 0;
let baseEq = cfg.cap, lastKey = null;
for (const r of D.t) {
  const m1 = r[2];
  if (!(m1 > 0)) continue;
  const key = pkey(r[0], cfg.rebase);
  if (key !== lastKey) { baseEq = eq; lastKey = key; }
  const base = cfg.rebase === 'trade' ? eq : cfg.rebase === 'never' ? cfg.cap : baseEq;
  const n = Math.floor(base * cfg.deploy / m1);
  if (n < 1) { skipped++; continue; }
  eq += r[1] * n; if (eq > peak) peak = eq;
  const dd = eq - peak; if (dd < maxDD) maxDD = dd;
  taken++;
}
console.log(JSON.stringify({end: Math.round(eq*100)/100, taken, skipped,
                            maxDD: Math.round(maxDD*100)/100}));
"""


def _node(data, cfg):
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False) as f:
        f.write(HARNESS.replace("__DATA__", json.dumps(data))
                       .replace("__CFG__", json.dumps(cfg)))
        path = f.name
    out = subprocess.run(["node", path], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.skipif(shutil.which("node") is None,
                    reason="needs the Node runtime to run the page's own sizing loop")
@pytest.mark.parametrize("rebase", sizing.REBASE)
@pytest.mark.parametrize("deploy", [0.02, 0.10, 0.40])
def test_browser_sizing_agrees_with_the_engine(rebase, deploy):
    """The page sizes in JS so the controls move without a round trip. That makes the
    model exist twice, and the only defence against drift is to run both.

    Every rebase frequency is checked, not just the two ends: the week/month/quarter
    buckets are the ones where a date-arithmetic difference between Python and JS would
    hide, and Monday-of-week in particular is spelt very differently in each."""
    data = capitalview.series(payload())
    trades = [{"pnl_rupees": t[1], "margin_points": t[2], "lot_size": 1,
               "entry": t[0], "exit": t[0]} for t in data["t"]]
    py = sizing.apply(trades, capital=1_000_000, deploy=deploy, rebase=rebase)
    js = _node(data, {"cap": 1_000_000, "deploy": deploy, "rebase": rebase})
    assert js["end"] == pytest.approx(py["ending_capital"], abs=0.5)
    assert js["taken"] == py["trades_taken"]
    assert js["skipped"] == py["trades_skipped_insufficient_capital"]
    assert js["maxDD"] == pytest.approx(py["max_drawdown_rupees"], abs=0.5)


# ------------------------------------------------------------------------- the series

def test_calendar_covers_every_trade_not_the_released_sample():
    """The whole reason margin went onto the equity curve. A standard response carries 25
    trade rows and the curve carries all 50; sizing the sample would silently describe a
    shorter, different strategy."""
    data = capitalview.series(payload(n=50, released=25))
    assert data["total"] == 50 and data["released"] == 25
    assert len(data["t"]) == 50


def test_released_trades_are_matched_to_their_curve_points():
    """Curve rows are exit-ordered and trade rows entry-ordered, so the two are joined on
    (date, whole-rupee P&L). Every released trade must find its point exactly once."""
    data = capitalview.series(payload(n=50, released=25))
    linked = [r[3] for r in data["t"] if r[3] >= 0]
    assert sorted(linked) == list(range(25))


def test_margin_falls_back_to_the_median_and_says_so():
    """An older stored payload has no margin column. Sizing it off the median released
    margin is defensible; presenting that as exact is not."""
    data = capitalview.series(payload(curve_margin=False))
    assert data["exact"] is False and data["medianMargin"] == 150.0 * LOT
    assert "median margin" in capitalview.render(payload(curve_margin=False))
    assert data["exact"] is not True


def test_a_position_the_margin_model_prices_at_zero_falls_back_to_one_lot():
    """A calendar spread returns margin 0 from the engine's model. There is then no
    capital to express a return as a per cent of -- and an account view full of zeroes
    reads as a broken page rather than as the honest answer."""
    data = capitalview.series(payload(margin=0.0))
    assert data["mode"] == "onelot" and data["sizable"] == 0
    html = capitalview.render(payload(margin=0.0))
    assert "No capital model for this position" in html
    assert 'id="cvCfg"' not in html, "controls must not be offered when nothing is sizable"


def test_one_priced_trade_in_fifty_is_not_an_account_view():
    """The calendar spread priced exactly one of its 49 trades and zero for the rest. On
    an "any margin at all" test it entered the sized view and described an account that
    took a single trade all year -- worse than saying nothing."""
    p = payload(n=50, margin=0.0)
    mi = p["equity_curve"]["columns"].index("margin_rupees")
    p["equity_curve"]["rows"][0][mi] = 6500   # one trade the model did price
    data = capitalview.series(p)
    assert data["sizable"] == 1 and data["mode"] == "onelot"
    assert "only 1 of these 50" in capitalview.render(p)


def test_no_trades_renders_nothing_rather_than_an_empty_calendar():
    empty = dict(payload(), equity_curve={"columns": [], "rows": []}, trades=[])
    assert capitalview.render(empty) == ""


def test_the_window_span_matches_the_engines():
    """CAGR is raised to the power of 1/years, so the two implementations must measure the
    same window: earliest entry to latest exit, not the requested date range."""
    p = payload(n=50)
    data = capitalview.series(p)
    trades = [{"pnl_rupees": t["pnl_rupees"], "margin_points": t["margin_points"],
               "lot_size": t["lot_size"], "entry": t["entry"], "exit": t["exit"]}
              for t in p["trades"]]
    # sizing._years over the FULL book; the released sample carries the first entry.
    full = [dict(t, entry=f"{START + dt.timedelta(days=i * 7)} 09:30",
                 exit=f"{START + dt.timedelta(days=i * 7 + 2)} 15:29")
            for i, t in enumerate(trades * 2)][:50]
    assert data["years"] == pytest.approx(sizing._years(full), abs=0.01)


# ----------------------------------------------------------------------- the rules card

def test_absent_criteria_are_dropped_from_the_strip_but_not_from_the_page():
    """Three columns reading "no target", "no stop" and "none" took a third of the strip
    to report absences. They are dropped -- but the FACT is not: the second line of the
    gloss states it, which is where a reader meets it first anyway."""
    card = rulecard.describe({"legs": [{"side": "sell", "type": "PE",
                                        "strike": {"pct_offset": -1.0}}],
                              "entry": {"cadence": "weekly", "dte": 2, "time": "09:30"}})
    assert card["stop"] == [] and card["target"] == [] and card["adjust"] == []
    html = reportui._rules(card)
    for gone in ("Stop loss", "Take profit", "Adjustments"):
        assert gone not in html, f"{gone} column drawn with nothing in it"
    assert "Entry criteria" in html and "Exit criteria" in html
    assert "No target, no stop, no adjustment." in card["gloss"][1]


def test_a_loss_rule_is_read_as_a_stop_and_a_profit_rule_as_a_target():
    """The protocol has no `stop_loss` keyword -- a stop is a rule whose condition is a
    loss threshold and whose action is close. Classification happens by reading it."""
    card = rulecard.describe({
        "legs": [{"side": "sell", "type": "CE", "strike": {"pct_offset": 1.0}}],
        "entry": {"cadence": "weekly", "dte": 2, "time": "09:30"},
        "rules": [{"when": {"pnl_pct_of_credit": {"gte": 70}}, "then": "close"},
                  {"when": {"pnl_pct_of_credit": {"lte": -120}}, "then": "close"},
                  {"when": {"leg_mark_mult": {"gte": 1.6, "leg": 0}},
                   "then": {"roll": {"legs": [0], "to": {"pct_offset": 2.0}}}}]})
    assert card["target"][0]["short"] == "70% of credit"
    assert card["stop"][0]["short"] == "120% of credit"
    assert len(card["adjust"]) == 1


def test_a_put_below_spot_is_not_described_as_a_negative_per_cent():
    """"-1.5% from spot" is exact and reads badly: below is a direction, not a sign."""
    card = rulecard.describe({"legs": [{"side": "sell", "type": "PE",
                                        "strike": {"pct_offset": -1.5}}],
                              "entry": {"cadence": "daily", "time": "09:30"}})
    assert card["legs"][0]["where"] == "1.5% below spot"
    assert "-1.5" not in card["gloss"][0] and "−1.5" not in card["gloss"][0]


def test_a_calendar_spread_says_the_legs_are_in_different_expiries():
    """Without it the gloss describes two identical legs that would net to nothing."""
    card = rulecard.describe({
        "legs": [{"side": "sell", "type": "CE", "strike": {"pct_offset": 0.5},
                  "expiry": "near"},
                 {"side": "buy", "type": "CE", "strike": {"pct_offset": 0.5},
                  "expiry": "next"}],
        "entry": {"cadence": "weekly", "dte": 2, "time": "09:30"}})
    assert "near expiry" in card["gloss"][0] and "next expiry" in card["gloss"][0]


def test_a_preset_spec_reports_only_the_exits_the_engine_applies():
    """backtest._exit_trigger reads sl_mult and tp_pct per structure. The card must not
    claim a stop the engine will not apply."""
    card = rulecard.describe({"structure": "short_strangle", "cadence": "weekly",
                              "entry_time": "09:30",
                              "params": {"pct_offset": 4.0, "entry_dte": 1}})
    if card.get("error") and "signals.py" in card["error"]:
        # A preset spec parses through the signals module, an optional runtime input this
        # repository does not ship. rulecard swallows the FileNotFoundError into the card
        # by design (a report must render), so conftest cannot see it -- re-raise so the
        # run says why this was not attempted instead of failing on an empty stop list.
        raise FileNotFoundError(card["error"].split(": ", 1)[-1])
    assert card["stop"] == [], "short_strangle with no sl_mult has no stop"
    with_sl = rulecard.describe({"structure": "short_strangle", "cadence": "weekly",
                                 "entry_time": "09:30",
                                 "params": {"pct_offset": 4.0, "sl_mult": 2.0}})
    assert with_sl["stop"][0]["short"] == "200% of credit"


def test_an_unparseable_spec_says_so_instead_of_inventing_a_description():
    card = rulecard.describe({"legs": [{"side": "sideways", "type": "XX"}]})
    assert card["error"] and "no longer parses" in card["error"]
    assert "no longer parses" in reportui._rules(card)
    assert "sideways" in reportui._rules(card)


# --------------------------------------------------------------------------- the page

def test_the_report_opens_with_the_rules_before_any_number():
    """A report that opens with a P&L figure asks to be trusted before the reader knows
    what produced it."""
    # Search the BODY: every one of these class names also appears in the stylesheet.
    body = reportui.render(payload(), "bt_test").split("<body>", 1)[1]
    pos = body.index('class="rules"')
    assert body.index("<h1>") < pos, "the name must come first"
    assert pos < body.index('id="capital"'), "the account view came before the rules"
    assert pos < body.index('class="checks"'), "the evidence came before the rules"


def test_the_page_carries_no_class_that_the_chart_catalogue_already_defines():
    """`.cv` was the evidence-row value class, and reusing it set every lede in the
    account section in monospace. Prefixes are the whole defence."""
    from server import design, reportlab

    def bare(src):
        """Selectors that match on ONE class and nothing else -- the only kind that can
        collide across stylesheets. `.tab .n` and `.cv-day .hd .n` share a name and
        cannot reach each other."""
        return set(re.findall(r"(?:^|[},])\s*\.([a-zA-Z][\w-]*)\s*(?:,|\{)",
                              src, re.M))

    theirs = set()
    for src in (reportlab.STYLE, reportlab.BRIDGE, reportui._CHART_CSS, design.MARK_CSS):
        theirs |= bare(src)
    # .up/.down/.flat are deliberately shared: they are the semantic P&L pair.
    assert not (bare(capitalview.CSS) & theirs) - {"up", "down", "flat"}


def test_the_leg_table_never_shows_a_gain_as_a_negative_number():
    """A short leg whose price falls has gained. The column reports the leg's own points,
    so its sign and its colour cannot disagree."""
    assert "l.a==='SELL' ? l['in']-l.out : l.out-l['in']" in capitalview.JS
    assert "sgn(-mv)" not in capitalview.JS
