"""Strategies nobody would build a configurator for.

The hundred in torture_100 asked whether the protocol can SAY a hundred different things.
This asks something narrower and meaner: whether the engine's SEMANTICS survive ideas that
were never in anyone's head when it was written. A position that flips from short volatility
to long. A roll that doubles its own size each time it fires. A leg whose strike is defined
by another leg. A condition using `eq` on a float. A trade entered in the last fourteen
minutes of an expiry session and settled at the mean of the close.

None of these are recommendations. Several are bad ideas on purpose -- a bad idea that runs
and reports an honest loss is the product working; a bad idea that raises an exception is a
protocol with a hole in it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from torture_100 import L, pct, pts, prem, dlt, fromleg, ATM, R, _sig, _close_conditions

X = []


def x(name, legs, entry, rules=(), exit=None, portfolio=None, max_adj=None,
      resolution=None, note=""):
    d = {"name": name, "legs": legs, "entry": entry}
    if rules:
        d["rules"] = list(rules)
    if exit:
        d["exit"] = exit
    if portfolio:
        d["portfolio"] = portfolio
    if max_adj is not None:
        d["max_adjustments"] = max_adj
    if resolution is not None:
        d["resolution"] = resolution
    X.append((d, note))


# ------------------------------------------------ A. positions that change their mind
x("Short vol until it isn't", [L("sell","CE",pct(1.0)), L("sell","PE",pct(-1.0))],
  {"cadence":"weekly","dte":4,"time":"09:20"},
  [R({"pnl_pct_of_credit":{"lte":-0.75}},
     {"close_and_open":{"close":"all",
                        "open":[L("buy","CE",ATM), L("buy","PE",ATM)]}}),
   R({"pnl_pts":{"gte":60}}, "close")],
  max_adj=2, note="takes the loss on short premium and FLIPS to owning gamma")

x("Long vol until it isn't", [L("buy","CE",ATM), L("buy","PE",ATM)],
  {"cadence":"weekly","dte":3,"time":"09:25"},
  [R({"all":[{"minutes_held":{"gte":600}},{"pnl_pts":{"lte":-10}}]},
     {"close_and_open":{"close":"all",
                        "open":[L("sell","CE",pct(1.2)), L("sell","PE",pct(-1.2))]}})],
  max_adj=1, note="the same flip in reverse: give up on the move, sell the decay")

x("Call becomes put", [L("sell","CE",pct(0.8))],
  {"cadence":"weekly","dte":2,"time":"09:32"},
  [R({"spot_beyond_strike":{"gte":-10,"leg":0}},
     {"close_and_open":{"close":[0], "open":[L("sell","PE",pct(-0.8))]}}, 3)],
  max_adj=4, note="chases the trend by switching which tail it sells")

x("Condor that only migrates upward",
  [L("sell","CE",pct(1.0)), L("buy","CE",pct(1.8)),
   L("sell","PE",pct(-1.0)), L("buy","PE",pct(-1.8))],
  {"cadence":"weekly","dte":5,"time":"09:38"},
  [R({"spot_beyond_strike":{"gte":-40,"leg":0}},
     {"roll":{"legs":[0,1],"to":pct(1.6)}}, 4)],
  max_adj=5, note="the call side runs, the put side never moves -- a one-way ratchet")

x("Calendar that collapses into a vertical",
  [L("sell","CE",ATM), L("buy","CE",ATM,expiry="next")],
  {"cadence":"weekly","dte":4,"time":"09:43"},
  [R({"pnl_pts":{"lte":-18}},
     {"close_and_open":{"close":[1], "open":[L("buy","CE",pct(1.2))]}})],
  max_adj=1, note="swaps the back-month long for a same-month one mid-trade")


# --------------------------------------------- B. strikes defined by other strikes
x("Constant-premium roll", [L("sell","PE",prem(60))],
  {"cadence":"weekly","dte":5,"time":"09:49"},
  [R({"leg_mark":{"lte":20,"leg":0}}, {"roll":{"legs":[0],"to":prem(60)}}, 8)],
  max_adj=10, note="every time the short decays to 20, re-sell 60 -- a premium treadmill")

x("Constant-delta roll", [L("sell","CE",dlt(0.25)), L("sell","PE",dlt(0.25))],
  {"cadence":"weekly","dte":6,"time":"09:55"},
  [R({"leg_mark_mult":{"gte":1.7,"leg":0}}, {"roll":{"legs":[0],"to":dlt(0.25)}}, 5),
   R({"leg_mark_mult":{"gte":1.7,"leg":1}}, {"roll":{"legs":[1],"to":dlt(0.25)}}, 5)],
  max_adj=10, note="re-delta whichever side moved, independently, all week")

x("Wings pinned to the body",
  [L("sell","CE",pct(1.1)), L("sell","PE",pct(-1.1))],
  {"cadence":"weekly","dte":4,"time":"10:02"},
  [R({"drawdown_from_peak":{"gte":14}},
     {"open":[L("buy","CE",fromleg(0, pct=0.9)), L("buy","PE",fromleg(1, pct=-0.9))]})],
  max_adj=1, note="the hedges are placed relative to the SHORTS, not to spot")

x("Hedge 200 points beyond", [L("sell","CE",dlt(0.30)), L("buy","CE",fromleg(0, points=200))],
  {"cadence":"weekly","dte":3,"time":"10:08"},
  [R({"pnl_pct_of_max":{"gte":0.60}}, "close")],
  note="a leg whose strike is stated in points beyond another leg, at entry")

x("Roll into the leg that stayed", [L("sell","CE",pct(1.3)), L("sell","PE",pct(-1.3))],
  {"cadence":"weekly","dte":5,"time":"10:15"},
  [R({"leg_pnl_pts":{"lte":-22,"leg":0}},
     {"roll":{"legs":[0],"to":fromleg(1, pct=2.6)}}, 3)],
  max_adj=4, note="the roll target is measured off the OTHER leg's strike")


# --------------------------------------------------- C. size that changes with events
x("Martingale roll", [L("sell","PE",pct(-1.5))],
  {"cadence":"weekly","dte":5,"time":"10:22"},
  [R({"leg_mark_mult":{"gte":2.0,"leg":0}}, {"roll":{"legs":[0],"to":pct(-2.5),"qty":2}}),
   R({"pnl_pts":{"lte":-90}}, "close")],
  max_adj=2, note="doubles size while moving away -- the classic way to blow up")

x("Anti-martingale pyramid", [L("sell","CE",pct(1.4)), L("sell","PE",pct(-1.4))],
  {"cadence":"weekly","dte":6,"time":"10:28"},
  [R({"pnl_pct_of_credit":{"gte":0.25}}, {"open":[L("sell","CE",pct(2.0),qty=2)]}),
   R({"pnl_pct_of_credit":{"gte":0.50}}, {"open":[L("sell","PE",pct(-2.6),qty=3)]}),
   R({"drawdown_from_peak":{"gte":40}}, "close")],
  max_adj=3, note="adds MORE size the better it goes, in increasing lots")

x("Add a rung per hour", [L("sell","CE",prem(30))],
  {"cadence":"daily","max_dte":1,"time":"09:45"},
  [R({"time":{"gte":"11:00"}}, {"open":[L("sell","CE",prem(25))]}),
   R({"time":{"gte":"12:30"}}, {"open":[L("sell","CE",prem(20))]}),
   R({"time":{"gte":"14:00"}}, {"open":[L("sell","CE",prem(15))]})],
  exit={"time":"15:20"}, max_adj=3,
  note="four shorts by the close, each sold at a lower price than the last")

x("Average into the loser", [L("sell","PE",dlt(0.20))],
  {"cadence":"weekly","dte":4,"time":"10:35"},
  [R({"pnl_pts":{"lte":-25}}, {"open":[L("sell","PE",dlt(0.20))]}, 2),
   R({"pnl_pts":{"lte":-120}}, "close")],
  max_adj=3, note="doubles down on a losing short put, twice, then gives up")

x("Hundred lots of nothing", [L("sell","CE",dlt(0.02),qty=100)],
  {"cadence":"weekly","dte":6,"time":"10:41"},
  [R({"leg_mark":{"gte":15,"leg":0}}, "close")],
  note="the quantity ceiling on a nearly worthless option -- margin must not be trivial")


# ------------------------------------------------- D. legal, degenerate, or perverse
x("Exactly at the money, exactly", [L("sell","CE",ATM), L("buy","CE",pct(1.0))],
  {"cadence":"weekly","dte":3,"time":"10:47"},
  [R({"adjustments_done":{"eq":0}}, "close")],
  note="`eq` on a counter that is always 0 -- must close every trade on its first minute")

x("A condition that can never be true", [L("sell","CE",pct(1.2)), L("sell","PE",pct(-1.2))],
  {"cadence":"weekly","dte":4,"time":"10:53"},
  [R({"spot_move_pct":{"eq":0.7777}}, "close")],
  note="`eq` on a float. It should fire never, and the engine should say nothing about it")

x("Self-financing structure",
  [L("sell","CE",prem(100)), L("buy","CE",prem(50)), L("buy","PE",prem(50))],
  {"cadence":"weekly","dte":5,"time":"11:00"},
  [R({"credit_kept_pct":{"lte":-3.0}}, "close")],
  note="near-zero net credit at entry, so credit_kept_pct divides by almost nothing")

x("Open nothing, close nothing", [L("sell","CE",pct(1.5)), L("sell","PE",pct(-1.5))],
  {"cadence":"weekly","dte":4,"time":"11:06"},
  [R({"pnl_pts":{"gte":20}},
     {"close_and_open":{"close":[], "open":[L("buy","CE",pct(3.0))]}})],
  max_adj=1, note="close_and_open with an EMPTY close list -- a pure add by another name")

x("Cap bites before the rule does", [L("sell","CE",prem(25))],
  {"cadence":"daily","max_dte":2,"time":"11:12"},
  [R({"leg_mark_mult":{"gte":1.2,"leg":0}}, {"roll":{"legs":[0],"to":prem(25)}}, 100)],
  exit={"time":"15:18"}, max_adj=6,
  note="max_times 100 against max_adjustments 6: the CAP must be what stops it")

x("Everything closes at once",
  [L("sell","CE",pct(0.9)), L("sell","PE",pct(-0.9)),
   L("buy","CE",pct(1.5)), L("buy","PE",pct(-1.5))],
  {"cadence":"weekly","dte":3,"time":"11:19"},
  [R({"pnl_pts":{"gte":5}}, {"close_legs":"all"})],
  note='close_legs "all" rather than close -- the same thing said a different way')


# ------------------------------------------------------- E. the edges of the session
x("The first minute", [L("sell","CE",ATM), L("sell","PE",ATM)],
  {"cadence":"daily","max_dte":0,"time":"09:15"},
  [R({"pnl_pct_of_credit":{"gte":0.20}}, "close")],
  exit={"time":"15:29"}, note="opens on the session's first tradeable minute")

x("The last fourteen minutes", [L("sell","CE",pct(0.3)), L("sell","PE",pct(-0.3))],
  {"cadence":"weekly","dte":0,"time":"15:15"},
  [R({"pnl_pts":{"lte":-12}}, "close")],
  note="entered inside the settlement window on expiry day and held to the mean")

x("Thirty-minute expiry scalp", [L("buy","CE",ATM), L("buy","PE",ATM)],
  {"cadence":"weekly","dte":0,"time":"09:16"},
  [R({"pnl_pct_of_credit":{"lte":-0.35}}, "close")],
  exit={"time":"09:46"}, note="a half-hour of expiry-morning gamma, then out")

x("Overnight only", [L("sell","CE",pct(1.0)), L("sell","PE",pct(-1.2))],
  {"cadence":"daily","max_dte":3,"time":"15:10"},
  [R({"time":{"between":["09:15","09:45"]}}, "close")],
  max_adj=1, note="on near the close, off in the first half hour of the NEXT session")

x("Held across the whole week", [L("sell","CE",dlt(0.12)), L("sell","PE",dlt(0.12))],
  {"cadence":"weekly","dte":7,"time":"09:17"},
  [R({"minutes_held":{"gte":10000}}, "close")],
  note="a minutes_held bound larger than the position's own life -- never fires")


# ------------------------------------------------------------ F. shapes with no name
x("The whole smile",
  [L("sell","CE",pct(0.5)), L("sell","CE",pct(1.0)), L("sell","CE",pct(1.5)),
   L("sell","CE",pct(2.0)), L("sell","PE",pct(-0.5)), L("sell","PE",pct(-1.0)),
   L("sell","PE",pct(-1.5)), L("sell","PE",pct(-2.0)),
   L("buy","CE",pct(3.0),qty=4), L("buy","PE",pct(-3.0),qty=4)],
  {"cadence":"weekly","dte":4,"time":"11:26"},
  [R({"pnl_pct_of_credit":{"gte":0.40}}, "close")],
  note="eight shorts across the chain under two four-lot wings")

x("Reverse iron condor",
  [L("buy","CE",pct(0.6)), L("sell","CE",pct(1.6)),
   L("buy","PE",pct(-0.6)), L("sell","PE",pct(-1.6))],
  {"cadence":"weekly","dte":5,"time":"11:33"},
  [R({"pnl_pct_of_max":{"gte":0.50}}, "close")],
  note="long the body, short the wings -- a defined-risk bet ON movement")

x("Diagonal strangle",
  [L("sell","CE",pct(1.2)), L("sell","PE",pct(-1.2),expiry="next")],
  {"cadence":"weekly","dte":3,"time":"11:40"},
  [R({"leg_pnl_pts":{"lte":-30,"leg":0}}, {"close_legs":[0]})],
  max_adj=1, note="the two sides of a strangle living in different weeks")

x("Tail hunter", [L("buy","CE",dlt(0.03)), L("buy","PE",dlt(0.03))],
  {"cadence":"weekly","dte":6,"time":"11:47"},
  [R({"leg_mark_mult":{"gte":8.0,"leg":1}}, "close")],
  note="buys only the far tails and asks for an eightfold move to pay")

x("Three expiries, three shapes",
  [L("sell","CE",ATM), L("sell","PE",ATM),
   L("buy","CE",pct(1.5),expiry="next"), L("buy","PE",pct(-1.5),expiry="next"),
   L("buy","CE",pct(3.0),expiry="far"), L("buy","PE",pct(-3.0),expiry="far")],
  {"cadence":"weekly","dte":4,"time":"11:54"},
  [R({"pnl_rupees":{"lte":-14000}}, "close")],
  note="a short straddle under two layers of longer-dated protection")

x("Ratio across the whole board",
  [L("sell","CE",pct(1.0),qty=3), L("buy","CE",pct(2.0),qty=1),
   L("sell","PE",pct(-1.0),qty=3), L("buy","PE",pct(-2.0),qty=1)],
  {"cadence":"weekly","dte":5,"time":"12:01"},
  [R({"spot_move_pct":{"between":[-0.3,0.3]}}, "close")],
  note="3:1 on both sides -- mostly naked, and the margin must know it")

x("Barrier condor",
  [L("sell","CE",pct(1.0)), L("sell","PE",pct(-1.0)),
   L("buy","CE",pct(2.0)), L("buy","PE",pct(-2.0))],
  {"cadence":"weekly","dte":6,"time":"12:07"},
  [R({"any":[{"spot_beyond_strike":{"gte":0,"leg":0}},
             {"spot_beyond_strike":{"gte":0,"leg":1}}]}, "close")],
  note="a no-touch: the first time either short goes in the money, it is over")

x("Escalating trail", [L("sell","CE",dlt(0.16)), L("sell","PE",dlt(0.16))],
  {"cadence":"weekly","dte":5,"time":"12:14"},
  [R({"all":[{"pnl_pts":{"between":[15,35]}},{"drawdown_from_peak":{"gte":20}}]}, "close"),
   R({"all":[{"pnl_pts":{"between":[35,70]}},{"drawdown_from_peak":{"gte":12}}]}, "close"),
   R({"all":[{"pnl_pts":{"gt":70}},{"drawdown_from_peak":{"gte":6}}]}, "close")],
  note="the trail TIGHTENS as profit grows -- three bands, three tolerances")

x("Harvest anything worthless",
  [L("sell","CE",prem(40)), L("sell","PE",prem(40)),
   L("buy","CE",prem(8)), L("buy","PE",prem(8))],
  {"cadence":"weekly","dte":5,"time":"12:20"},
  [R({"leg_mark":{"lte":0.5,"leg":2}}, {"close_legs":[2]}),
   R({"leg_mark":{"lte":0.5,"leg":3}}, {"close_legs":[3]}),
   R({"pnl_pct_of_credit":{"gte":0.70}}, "close")],
  max_adj=3, note="sells back each wing the moment it stops being worth anything")


if __name__ == "__main__":
    import json, time, traceback
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from engine import strategy as strategy_mod, simulate, metrics, spec as spec_mod

    seen = {}
    for spec, _ in X:
        k = _sig([spec["entry"], spec["legs"], spec.get("rules"), spec.get("exit"),
                  _close_conditions(spec)])
        assert k not in seen, f"{spec['name']} duplicates {seen[k]}"
        seen[k] = spec["name"]
    print(f"{len(X)} out-of-the-box strategies, all distinct\n")

    window = spec_mod.window_for("free")
    rows, t0all = [], time.time()
    for i, (spec, note) in enumerate(X, 1):
        rec = {"n": i, "name": spec["name"], "note": note}
        t0 = time.time()
        try:
            parsed = strategy_mod.parse(spec, window=window)
            rec["shape"] = parsed.structure
            res = simulate.run(parsed, lots=1)
            m = metrics.summarise(res)
            rec.update(status="ok", trades=len(res.trades),
                       actions=sum(len(t.adjustments) for t in res.trades),
                       net=m.get("total_pnl_points"), win=m.get("win_rate"),
                       margin=m.get("avg_margin_points"),
                       reasons=m.get("exit_reasons"), skipped=res.skipped,
                       notes=res.notes, warnings=m.get("warnings"))
        except strategy_mod.StrategyError as e:
            rec.update(status="refused", error=str(e))
        except Exception as e:
            rec.update(status="CRASH", error=f"{type(e).__name__}: {e}",
                       tb=traceback.format_exc()[-1500:])
        rec["secs"] = round(time.time() - t0, 2)
        rows.append(rec)
        flag = {"ok": " ", "refused": "?", "CRASH": "!"}[rec["status"]]
        print(f"{flag}{i:>3}. {rec['name'][:34]:<34} {rec.get('shape','-')[:19]:<19} "
              f"{rec.get('trades',''):>4} tr {rec.get('actions',''):>4} act "
              f"{rec.get('net',''):>8} pts {rec.get('margin',''):>7} mgn "
              f"{rec['secs']:>6.2f}s {rec.get('error','')[:60]}", flush=True)
    print(f"\ntotal {time.time() - t0all:.1f}s")
    json.dump(rows, open("/tmp/tortureX.json", "w"), indent=1, default=str)
