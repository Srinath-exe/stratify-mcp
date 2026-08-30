"""Strategies whose REASON for trading is the point.

Everything in torture_100 and torture_exotic could say what to open and how to manage it.
None of them could say WHY this week and not last week -- every rule field was about the
live position, the spot level or the clock. These twenty-four are the other half: the gate
is the strategy, and the structure behind it is deliberately plain so that any difference
in the result comes from the gate alone.

Each one also carries the number of cycles its gate REJECTED. A gate that rejects nothing
is not a gate, and a gate that rejects everything is a typo -- both look like a working
strategy in a report that only shows the trades that happened.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from torture_100 import L, pct, prem, dlt, ATM, R

M = []


def m(name, legs, entry, rules=(), exit=None, max_adj=None, note=""):
    d = {"name": name, "legs": legs, "entry": entry}
    if rules:
        d["rules"] = list(rules)
    if exit:
        d["exit"] = exit
    if max_adj is not None:
        d["max_adjustments"] = max_adj
    M.append((d, note))


STRANGLE = [L("sell","CE",pct(1.2)), L("sell","PE",pct(-1.2))]
STRADDLE = [L("buy","CE",ATM), L("buy","PE",ATM)]
CONDOR = [L("sell","CE",pct(1.0)), L("sell","PE",pct(-1.0)),
          L("buy","CE",pct(2.0)), L("buy","PE",pct(-2.0))]

# ------------------------------------------------------------------ volatility level
m("Sell premium only when it is dear", STRANGLE,
  {"cadence":"weekly","dte":3,"time":"09:30","when":{"vix":{"gte":15}}},
  note="the request that started this: 'only when India VIX is above 15'")
m("Sell premium only when it is cheap", [L("sell","CE",pct(1.4)), L("sell","PE",pct(-1.4))],
  {"cadence":"weekly","dte":3,"time":"09:35","when":{"vix":{"lte":12}}},
  note="the contrarian inverse — most people would not run it, and now they can check")
m("Buy cheap gamma", STRADDLE,
  {"cadence":"weekly","dte":2,"time":"09:40","when":{"vix":{"between":[10,13]}}},
  note="own volatility only when it is on sale")
m("Sell into a falling VIX", [L("sell","CE",dlt(0.2)), L("sell","PE",dlt(0.2))],
  {"cadence":"weekly","dte":4,"time":"09:45",
   "when":{"all":[{"vix_prev_close":{"gte":14}},{"vix_change_pct":{"lte":-3}}]}},
  note="yesterday was frightened, today is calming — two VIX fields against each other")
m("Out on a volatility spike", [L("sell","CE",pct(1.1)), L("sell","PE",pct(-1.1))],
  {"cadence":"weekly","dte":4,"time":"09:50"},
  [R({"vix_change_pct":{"gte":15}}, "close")],
  note="VIX as a live EXIT, not just an entry gate")
m("Widen when volatility jumps", STRANGLE,
  {"cadence":"weekly","dte":5,"time":"09:55"},
  [R({"vix_change_pct":{"gte":8}}, {"roll":{"legs":"all","to":pct(2.2)}}, 2)],
  max_adj=3, note="the gate drives a mid-trade adjustment")

# ------------------------------------------------------------------ yesterday
m("Buy the day after a fall", STRADDLE,
  {"cadence":"daily","max_dte":3,"time":"09:30","when":{"prev_day_move_pct":{"lte":-1.0}}},
  exit={"time":"15:00"}, note="'only after yesterday closed down 1%'")
m("Fade the fall", [L("sell","PE",pct(-1.5)), L("buy","PE",pct(-2.5))],
  {"cadence":"daily","max_dte":4,"time":"09:32","when":{"prev_day_move_pct":{"lte":-1.5}}},
  exit={"time":"15:05"}, note="sell puts INTO weakness")
m("Fade the rally", [L("sell","CE",pct(1.5)), L("buy","CE",pct(2.5))],
  {"cadence":"daily","max_dte":4,"time":"09:34","when":{"prev_day_move_pct":{"gte":1.5}}},
  exit={"time":"15:07"}, note="the mirror image, so the pair can be compared")
m("Only after a quiet day", CONDOR,
  {"cadence":"daily","max_dte":5,"time":"09:36",
   "when":{"prev_day_move_pct":{"between":[-0.3,0.3]}}},
  exit={"time":"15:12"}, note="range-bound yesterday, range-bound today")
m("Sell the gap up", [L("sell","CE",prem(50))],
  {"cadence":"daily","max_dte":2,"time":"09:20","when":{"gap_pct":{"gte":0.5}}},
  exit={"time":"15:10"}, note="acts on the opening print, five minutes after the bell")
m("Sell the gap down", [L("sell","PE",prem(50))],
  {"cadence":"daily","max_dte":2,"time":"09:22","when":{"gap_pct":{"lte":-0.5}}},
  exit={"time":"15:14"}, note="the other side of the same idea")

# ------------------------------------------------------------------ the calendar
m("Mondays only", STRANGLE,
  {"cadence":"daily","max_dte":5,"time":"09:30","when":{"day_of_week":{"eq":1}}},
  exit={"time":"15:20"}, note="'only on Mondays'")
m("Fridays only", [L("sell","CE",pct(0.8)), L("sell","PE",pct(-0.8))],
  {"cadence":"daily","max_dte":5,"time":"09:38","when":{"day_of_week":{"eq":5}}},
  exit={"time":"15:18"}, note="the other end of the week")
m("The middle of the week", [L("sell","CE",dlt(0.25)), L("sell","PE",dlt(0.25))],
  {"cadence":"daily","max_dte":4,"time":"09:42","when":{"day_of_week":{"between":[2,4]}}},
  exit={"time":"15:16"}, note="Tuesday to Thursday, avoiding both edges")
m("Anything but Wednesday", [L("sell","CE",pct(1.6)), L("sell","PE",pct(-1.6))],
  {"cadence":"daily","max_dte":5,"time":"09:44","when":{"not":{"day_of_week":{"eq":3}}}},
  exit={"time":"15:22"}, note="a negated weekday")

# ------------------------------------------------------------------ regime
m("Only in calm", [L("sell","CE",pct(0.9)), L("sell","PE",pct(-0.9))],
  {"cadence":"weekly","dte":3,"time":"10:00","when":{"realised_vol_20d":{"lte":10}}},
  note="20 sessions of realised volatility, ending YESTERDAY")
m("Only in a storm", [L("sell","CE",pct(2.5)), L("sell","PE",pct(-2.5))],
  {"cadence":"weekly","dte":3,"time":"10:05","when":{"realised_vol_20d":{"gte":18}}},
  note="sell far when the market is actually moving")
m("The middle regime", CONDOR,
  {"cadence":"weekly","dte":4,"time":"10:10","when":{"realised_vol_20d":{"between":[12,16]}}},
  note="neither calm nor stormy")
m("Realised above implied", [L("buy","CE",dlt(0.4)), L("buy","PE",dlt(0.4))],
  {"cadence":"weekly","dte":5,"time":"10:15",
   "when":{"all":[{"realised_vol_20d":{"gte":14}},{"vix":{"lte":13}}]}},
  note="the market has been moving more than options are priced for — buy them")

# ------------------------------------------------------------------ monthly
m("Monthly strangle", [L("sell","CE",pct(3.0)), L("sell","PE",pct(-3.0))],
  {"cadence":"monthly","time":"09:30"},
  note="'monthly expiry' — twelve trades a year, entered three weeks out")
m("Monthly condor, one week out", CONDOR,
  {"cadence":"monthly","dte":7,"time":"09:33"},
  note="the monthly contract, held for its last week only")
m("Monthly, a month out", [L("sell","CE",dlt(0.15)), L("sell","PE",dlt(0.15))],
  {"cadence":"monthly","dte":30,"time":"09:37"},
  note="entered as soon as the contract is a month from expiry")
m("Everything at once", [L("sell","CE",pct(2.0)), L("sell","PE",pct(-2.0)),
                          L("buy","CE",pct(3.5)), L("buy","PE",pct(-3.5))],
  {"cadence":"monthly","dte":14,"time":"09:41",
   "when":{"all":[{"vix":{"gte":12}},
                  {"realised_vol_20d":{"lte":20}},
                  {"day_of_week":{"between":[1,5]}},
                  {"prev_day_move_pct":{"between":[-2,2]}}]}},
  [R({"vix_change_pct":{"gte":20}}, "close")],
  note="monthly cadence, four gates, and a VIX exit — every new capability in one spec")


if __name__ == "__main__":
    import json, time, traceback
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from engine import strategy as strategy_mod, simulate, metrics, spec as spec_mod

    window = spec_mod.window_for("free")
    rows, t0all = [], time.time()
    print(f"{len(M)} market-state strategies\n")
    for i, (spec, note) in enumerate(M, 1):
        rec = {"n": i, "name": spec["name"], "note": note}
        t0 = time.time()
        try:
            parsed = strategy_mod.parse(spec, window=window)
            res = simulate.run(parsed, lots=1)
            mm = metrics.summarise(res)
            rec.update(status="ok", trades=len(res.trades),
                       rejected=res.skipped.get("entry_condition", 0),
                       actions=sum(len(t.adjustments) for t in res.trades),
                       net=mm.get("total_pnl_points"), win=mm.get("win_rate"),
                       vix=parsed.needs_vix)
        except Exception as e:
            rec.update(status="CRASH", error=f"{type(e).__name__}: {e}",
                       tb=traceback.format_exc()[-900:])
        rec["secs"] = round(time.time() - t0, 2)
        rows.append(rec)
        flag = " " if rec["status"] == "ok" else "!"
        print(f"{flag}{i:>3}. {rec['name'][:32]:<32} {rec.get('trades',''):>4} tr  "
              f"gate rejected {rec.get('rejected',''):>4}  {rec.get('net',''):>9} pts  "
              f"{rec['secs']:>5.2f}s {rec.get('error','')[:55]}", flush=True)
    ok = [r for r in rows if r["status"] == "ok"]
    dead = [r for r in ok if r["trades"] == 0]
    nogate = [r for r in ok if r["rejected"] == 0 and "when" in M[r["n"]-1][0]["entry"]]
    print(f"\ntotal {time.time() - t0all:.1f}s | ran {len(ok)}/{len(rows)}")
    print(f"gates that rejected nothing: {[r['name'] for r in nogate] or 'none'}")
    print(f"strategies with no trades:   {[r['name'] for r in dead] or 'none'}")
    json.dump(rows, open("/tmp/tortureM.json", "w"), indent=1, default=str)
