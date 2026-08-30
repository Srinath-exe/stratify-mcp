"""One hundred strategies that share nothing.

WHY THIS EXISTS. The capability probe asked "can the engine express thirty things a
trader might say?" This asks a harder question: "does it hold up when a hundred people
each ask for something nobody else asked for?" Every strategy below is pairwise distinct
from every other on FOUR independent axes -- how it gets in, what it opens, how it is
managed while open, and how it gets out -- and a check at the bottom refuses to run if
any two share a signature on any of them. That constraint is the test. It is easy to
write a hundred strangles with different numbers; it is not easy to write a hundred
different ANSWERS to "when do I take this off".

The point is not that these are good strategies. Most of them are deliberately strange.
The point is that a system built around presets would refuse most of them at the door.
"""
import datetime as dt


# ------------------------------------------------------------------ shorthand
def L(side, t, strike, qty=1, expiry="near"):
    d = {"side": side, "type": t, "strike": strike}
    if qty != 1:
        d["qty"] = qty
    if expiry != "near":
        d["expiry"] = expiry
    return d


def pct(v, ref=None):
    d = {"pct_offset": v}
    if ref:
        d["ref"] = ref
    return d


def pts(v, ref=None):
    d = {"points_offset": v}
    if ref:
        d["ref"] = ref
    return d


def prem(v):        return {"premium_near": v}
def dlt(v):         return {"delta_near": v}
def fromleg(i, **k): return {"from_leg": dict(leg=i, **k)}
ATM = "atm"


def R(when, then, max_times=1):
    r = {"when": when, "then": then}
    if max_times != 1:
        r["max_times"] = max_times
    return r


S = []


def strat(name, legs, entry, rules=(), exit=None, portfolio=None, max_adj=None,
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
    S.append((d, note))
    return d


# ============================================================ 1-12  intraday clock work
strat("Opening-drive fade", [L("sell","CE",ATM), L("sell","PE",ATM)],
      {"cadence":"daily","max_dte":0,"time":"09:16"},
      [R({"pnl_pct_of_credit":{"gte":0.25}}, "close"),
       R({"pnl_pct_of_credit":{"lte":-0.60}}, "close")],
      exit={"time":"15:10"}, note="0-DTE straddle, symmetric percent-of-credit brackets")

strat("Half off at lunch", [L("sell","CE",pct(0.4)), L("sell","PE",pct(-0.4))],
      {"cadence":"daily","max_dte":0,"time":"09:22"},
      [R({"time":{"gte":"11:00"}}, {"close_legs":[0]})],
      exit={"time":"14:50"}, note="scales out of the call side on the clock alone")

strat("Long straddle with a giveback stop", [L("buy","CE",ATM), L("buy","PE",ATM)],
      {"cadence":"daily","max_dte":0,"time":"09:35"},
      [R({"drawdown_from_peak":{"gte":8}}, "close")],
      exit={"time":"15:00"}, note="debit position, trailing on points given back")

strat("Naked put, doubling stop", [L("sell","PE",pts(-100))],
      {"cadence":"daily","max_dte":0,"time":"09:41"},
      [R({"leg_mark_mult":{"gte":2.0,"leg":0}}, "close")],
      exit={"time":"15:05"}, note="single leg, stop expressed as a price multiple")

strat("Iron fly, capture 45% of theoretical",
      [L("sell","CE",ATM), L("sell","PE",ATM), L("buy","CE",pct(1.5)), L("buy","PE",pct(-1.5))],
      {"cadence":"daily","max_dte":1,"time":"09:52"},
      [R({"pnl_pct_of_max":{"gte":0.45}}, "close")],
      exit={"time":"15:15"}, note="target defined against max theoretical profit")

strat("Call ratio into the drift", [L("buy","CE",ATM), L("sell","CE",prem(30),qty=2)],
      {"cadence":"daily","max_dte":0,"time":"10:07"},
      [R({"spot_move_pct":{"gte":0.35}}, "close")],
      exit={"time":"14:40"}, note="1x2 ratio killed by the move it fears")

strat("Delta-selected long call", [L("buy","CE",dlt(0.30))],
      {"cadence":"daily","max_dte":0,"time":"10:18"},
      [R({"leg_mark_delta":{"lte":-6,"leg":0}}, "close")],
      exit={"time":"15:25"}, note="stop in rupees of premium, not percent")

strat("Cheap strangle, exit on decay",
      [L("sell","CE",prem(25)), L("sell","PE",prem(25))],
      {"cadence":"daily","max_dte":2,"time":"10:33"},
      exit={"time":"15:12","when":{"combined_premium":{"lte":10}}},
      note="no rules at all — the exit CONDITION does the work")

strat("Buy the wreck, sell the bounce", [L("buy","CE",pct(0.5)), L("buy","PE",pct(-0.5))],
      {"cadence":"daily","max_dte":0,"time":"10:44"},
      [R({"runup_from_trough":{"gte":14}}, "close")],
      exit={"time":"14:58"}, note="exits on recovery from the worst mark, not on profit")

strat("Roll the call, then give up", [L("sell","CE",prem(40))],
      {"cadence":"daily","max_dte":0,"time":"11:07"},
      [R({"spot_beyond_strike":{"gte":-20,"leg":0}}, {"roll":{"legs":[0],"to":pct(1.2)}}, 2),
       R({"pnl_pts":{"lte":-35}}, "close")],
      exit={"time":"15:18"}, note="roll first, hard stop underneath it")

strat("Condor that comes off at 14:20",
      [L("sell","CE",pct(0.6)), L("sell","PE",pct(-0.6)),
       L("buy","CE",pct(1.2)), L("buy","PE",pct(-1.2))],
      {"cadence":"daily","max_dte":0,"time":"11:26"},
      [R({"time":{"gte":"14:20"}}, "close")],
      note="the only exit is a rule on the clock — no exit block at all")

strat("Put spread, credit-kept stop",
      [L("sell","PE",dlt(0.20)), L("buy","PE",dlt(0.08))],
      {"cadence":"daily","max_dte":1,"time":"11:48"},
      [R({"credit_kept_pct":{"lte":0.40}}, "close")],
      exit={"time":"15:22"}, note="stop stated as 'I have given back 60% of what I took'")


# ======================================================= 13-24  weekly credit + repair
strat("Strangle that runs from the tested side",
      [L("sell","CE",pct(1.5)), L("sell","PE",pct(-1.5))],
      {"cadence":"weekly","dte":4,"time":"09:19"},
      [R({"spot_beyond_strike":{"gte":-25,"leg":0}}, {"roll":{"legs":[0],"to":pct(1.5)}}, 3),
       R({"pnl_pct_of_credit":{"gte":0.70}}, "close")],
      max_adj=6, note="rolls the challenged call up to three times")

strat("Harvest the wings",
      [L("sell","CE",pct(1.0)), L("sell","PE",pct(-1.0)),
       L("buy","CE",pct(2.0)), L("buy","PE",pct(-2.0))],
      {"cadence":"weekly","dte":3,"time":"09:26"},
      [R({"pnl_pct_of_credit":{"gte":0.50}}, {"close_legs":[2,3]}),
       R({"all":[{"dte":{"lte":0}},{"time":{"gte":"15:00"}}]}, "close")],
      max_adj=2, note="sells the protection back once the trade is won")

strat("Jade lizard, rupee stop",
      [L("sell","PE",pct(-1.2)), L("sell","CE",pct(1.0)), L("buy","CE",pct(1.8))],
      {"cadence":"weekly","dte":2,"time":"09:44"},
      [R({"pnl_pts":{"lte":-45}}, "close")],
      note="no upside risk by construction; the stop is all downside")

strat("Broken wing, roll the whole thing",
      [L("sell","CE",pct(0.8)), L("sell","PE",pct(-0.8)),
       L("buy","CE",pct(1.4)), L("buy","PE",pct(-2.6))],
      {"cadence":"weekly","dte":5,"time":"09:58"},
      [R({"drawdown_from_peak":{"gte":20}}, {"roll":{"legs":"all","to":pct(1.6)}})],
      exit={"when":{"pnl_pct_of_credit":{"gte":0.62}}},
      max_adj=1, note="rolls every leg at once — a full re-centre")

strat("Straddle that becomes a strangle",
      [L("sell","CE",dlt(0.50)), L("sell","PE",dlt(0.50))],
      {"cadence":"weekly","dte":6,"time":"10:11"},
      [R({"spot_move_pct":{"gte":0.80}},
         {"close_and_open":{"close":"all",
                            "open":[L("sell","CE",pct(2.0)), L("sell","PE",pct(-2.0))]}}),
       R({"pnl_pts":{"lte":-95}}, "close")],
      max_adj=1, note="closes the tight position and reopens wide after a move")

strat("Far strangle held on the clock",
      [L("sell","CE",dlt(0.10)), L("sell","PE",dlt(0.10))],
      {"cadence":"weekly","dte":7,"time":"10:29"},
      [R({"minutes_held":{"gte":2000}}, "close")],
      note="exit measured in minutes of exposure")

strat("Call credit spread, rupee risk cap",
      [L("sell","CE",ATM), L("buy","CE",pct(1.0))],
      {"cadence":"weekly","dte":1,"time":"10:52"},
      [R({"pnl_rupees":{"lte":-6000}}, "close")],
      note="stop stated in actual rupees at this lot size")

strat("Expiry-day fly, out by 14:30",
      [L("sell","CE",ATM), L("sell","PE",ATM),
       L("buy","CE",pct(0.9)), L("buy","PE",pct(-0.9))],
      {"cadence":"weekly","dte":0,"time":"11:14"},
      [R({"time":{"gte":"14:30"}}, "close")],
      note="never carried into settlement")

strat("Put ratio spread", [L("buy","PE",ATM), L("sell","PE",pct(-1.5),qty=2)],
      {"cadence":"weekly","dte":4,"time":"11:39"},
      [R({"spot_move_pct":{"lte":-1.2}}, "close")],
      note="1x2, closed if the market comes for the naked short")

strat("Two-stage de-risk", [L("sell","CE",pct(1.3)), L("sell","PE",pct(-1.3))],
      {"cadence":"weekly","dte":3,"time":"12:03"},
      [R({"pnl_pct_of_credit":{"gte":0.35}}, {"close_legs":[1]}),
       R({"pnl_pct_of_credit":{"gte":0.75}}, "close")],
      max_adj=2, note="takes the put side off first, then the rest")

strat("Pull the untested wing in",
      [L("sell","CE",pct(1.1)), L("sell","PE",pct(-1.1)),
       L("buy","CE",pct(2.2)), L("buy","PE",pct(-2.2))],
      {"cadence":"weekly","dte":2,"time":"12:21"},
      [R({"pnl_pct_of_credit":{"between":[0.40,0.60]}},
         {"roll":{"legs":[3],"to":pct(-1.4)}})],
      exit={"when":{"credit_kept_pct":{"lte":0.05}}}, max_adj=1, note="fires only inside a BAND of profit, not above a level")

strat("Buy insurance when it hurts",
      [L("sell","CE",pct(1.4)), L("sell","PE",pct(-1.4))],
      {"cadence":"weekly","dte":5,"time":"12:44"},
      [R({"drawdown_from_peak":{"gte":30}},
         {"open":[L("buy","CE",pct(2.8)), L("buy","PE",pct(-2.8))]}),
       R({"pnl_pts":{"lte":-80}}, "close")],
      max_adj=2, note="adds legs mid-trade rather than closing")


# ========================================================== 25-34  per-leg intelligence
strat("Re-sell the doubled leg",
      [L("sell","CE",prem(50)), L("sell","PE",prem(50))],
      {"cadence":"weekly","dte":4,"time":"13:02"},
      [R({"leg_mark_mult":{"gte":2.0,"leg":0}}, {"roll":{"legs":[0],"to":prem(50)}}, 4),
       R({"minutes_held":{"gte":5000}}, "close")],
      max_adj=8, note="every time the call doubles, sell a new 50-point call")

strat("Swap the tested side for a spread",
      [L("sell","CE",pct(1.2)), L("sell","PE",pct(-1.2))],
      {"cadence":"weekly","dte":3,"time":"13:19"},
      [R({"leg_mark_delta":{"gte":30,"leg":1}},
         {"close_and_open":{"close":[1],
                            "open":[L("sell","PE",pct(-2.0)), L("buy","PE",pct(-3.0))]}}),
       R({"pnl_rupees":{"lte":-11000}}, "close")],
      max_adj=1, note="turns a naked short into a defined-risk spread mid-trade")

strat("Cut the losing vertical, keep the other",
      [L("sell","CE",pct(0.9)), L("buy","CE",pct(1.6)),
       L("sell","PE",pct(-0.9)), L("buy","PE",pct(-1.6))],
      {"cadence":"weekly","dte":2,"time":"13:37"},
      [R({"leg_pnl_pts":{"lte":-25,"leg":0}}, {"close_legs":[0,1]})],
      max_adj=1, note="a condor managed as two independent verticals")

strat("Long call, price-multiple target", [L("buy","CE",dlt(0.35))],
      {"cadence":"daily","max_dte":3,"time":"13:52"},
      [R({"leg_mark_mult":{"gte":1.50,"leg":0}}, "close")],
      exit={"time":"15:08"}, note="+50% on the option itself, nothing else")

strat("Absolute price stop on a short call", [L("sell","CE",prem(60))],
      {"cadence":"weekly","dte":6,"time":"14:06"},
      [R({"leg_mark":{"gte":120,"leg":0}}, "close")],
      note="stop at a price level, not a change")

strat("Roll the put down when breached",
      [L("sell","PE",ATM), L("buy","PE",pct(-2.0))],
      {"cadence":"weekly","dte":1,"time":"14:21"},
      [R({"spot_beyond_strike":{"gte":0,"leg":0}}, {"roll":{"legs":[0],"to":pct(-1.0)}}, 2)],
      exit={"when":{"pnl_pct_of_max":{"gte":0.66}}}, max_adj=3, note="fires the moment the short strike goes in the money")

strat("Either side doubles, everything goes",
      [L("sell","CE",dlt(0.15)), L("sell","PE",dlt(0.15))],
      {"cadence":"daily","max_dte":1,"time":"14:33"},
      [R({"any":[{"leg_mark_mult":{"gte":2.5,"leg":0}},
                 {"leg_mark_mult":{"gte":2.5,"leg":1}}]}, "close")],
      exit={"time":"15:26"}, note="one condition, two legs, an OR between them")

strat("Independent stops per leg",
      [L("sell","CE",pts(60)), L("sell","PE",pts(-60))],
      {"cadence":"weekly","dte":5,"time":"14:47"},
      [R({"leg_mark_mult":{"gte":2.0,"leg":0}}, {"close_legs":[0]}),
       R({"leg_mark_mult":{"gte":2.0,"leg":1}}, {"close_legs":[1]}),
       R({"pnl_pct_of_credit":{"gte":0.60}}, "close")],
      max_adj=3, note="each side stops itself; the survivor runs on")

strat("Call ladder", [L("sell","CE",pct(0.7)), L("sell","CE",pct(1.4)), L("sell","CE",pct(2.1))],
      {"cadence":"weekly","dte":3,"time":"15:01"},
      [R({"leg_pnl_pts":{"lte":-40,"leg":0}}, {"close_legs":[0]}),
       R({"spot_move_pct":{"gte":1.5}}, "close")],
      max_adj=2, note="three shorts up the chain, unwound from the nearest")

strat("Keep the strangle symmetric",
      [L("sell","CE",pct(1.0)), L("sell","PE",pct(-1.0))],
      {"cadence":"weekly","dte":4,"time":"15:14"},
      [R({"leg_mark_mult":{"lte":0.35,"leg":0}},
         {"roll":{"legs":[1],"to":fromleg(0, pct=-2.0)}}, 2),
       R({"all":[{"dte":{"lte":0}},{"time":{"gte":"13:45"}}]}, "close")],
      max_adj=3, note="the roll target is defined RELATIVE TO ANOTHER LEG")


# ============================================================ 35-44  time spreads
strat("ATM call calendar",
      [L("sell","CE",ATM), L("buy","CE",ATM,expiry="next")],
      {"cadence":"weekly","dte":4,"time":"09:17"},
      [R({"pnl_pts":{"gte":20}}, "close")],
      note="two expiries, same strike")

strat("Put calendar into the last afternoon",
      [L("sell","PE",ATM), L("buy","PE",ATM,expiry="next")],
      {"cadence":"weekly","dte":3,"time":"09:24"},
      [R({"all":[{"dte":{"lte":0}},{"time":{"gte":"14:00"}}]}, "close")],
      note="held until the front leg's own expiry afternoon")

strat("Call diagonal with a giveback rule",
      [L("sell","CE",pct(1.0)), L("buy","CE",pct(2.0),expiry="next")],
      {"cadence":"weekly","dte":5,"time":"09:33"},
      [R({"drawdown_from_peak":{"gte":12}}, "close")],
      note="different strikes AND different expiries")

strat("Double calendar",
      [L("sell","CE",pct(0.8)), L("buy","CE",pct(0.8),expiry="next"),
       L("sell","PE",pct(-0.8)), L("buy","PE",pct(-0.8),expiry="next")],
      {"cadence":"weekly","dte":2,"time":"09:46"},
      [R({"pnl_rupees":{"gte":5000}}, "close")],
      note="four legs across two expiries")

strat("Sell near, own far",
      [L("sell","CE",pct(1.2)), L("sell","PE",pct(-1.2)),
       L("buy","CE",pct(2.4),expiry="next"), L("buy","PE",pct(-2.4),expiry="next")],
      {"cadence":"weekly","dte":6,"time":"09:56"},
      [R({"minutes_held":{"gte":3000}}, "close")],
      note="a covered strangle whose hedge outlives the shorts")

strat("Re-sell the front month",
      [L("buy","CE",pct(1.0),expiry="far"), L("sell","CE",prem(40))],
      {"cadence":"weekly","dte":4,"time":"10:09"},
      [R({"leg_mark":{"lte":8,"leg":1}}, {"roll":{"legs":[1],"to":prem(40)}}, 3),
       R({"spot_move_pct":{"gte":3.0}}, "close")],
      max_adj=4, note="long far-dated call financed by repeatedly re-sold near calls")

strat("Calendar only when it is cheap",
      [L("sell","PE",pct(-0.5)), L("buy","PE",pct(-0.5),expiry="next")],
      {"cadence":"weekly","dte":3,"time":"10:23","when":{"combined_premium":{"gte":-60}}},
      [R({"pnl_pct_of_max":{"gte":0.30}}, "close")],
      note="entry gate on what the spread costs; the pnl_pct_of_max rule is UNDEFINED for an open-ended debit and must read as false, not error")

strat("Reverse calendar",
      [L("buy","PE",ATM), L("sell","PE",ATM,expiry="next")],
      {"cadence":"weekly","dte":1,"time":"10:37"},
      [R({"spot_move_pct":{"lte":-0.9}}, "close")],
      note="long the front, short the back — the calendar upside down")

strat("Butterfly in time",
      [L("buy","CE",pct(1.0)), L("sell","CE",pct(1.0),qty=2,expiry="next"),
       L("buy","CE",pct(1.0),expiry="far")],
      {"cadence":"weekly","dte":7,"time":"10:49"},
      [R({"pnl_pts":{"lte":-25}}, "close")],
      note="one strike, three expiries, 1-2-1")

strat("Ratio diagonal",
      [L("sell","CE",pct(1.0),qty=2), L("buy","CE",ATM,expiry="next")],
      {"cadence":"weekly","dte":2,"time":"11:02"},
      [R({"spot_beyond_strike":{"gte":40,"leg":0}}, "close")],
      note="unequal quantities across unequal expiries")


# ======================================================== 45-54  unequal quantities
strat("Call ratio 1x2", [L("buy","CE",ATM), L("sell","CE",pct(1.0),qty=2)],
      {"cadence":"weekly","dte":4,"time":"11:16"},
      [R({"spot_move_pct":{"gte":1.0}}, "close")],
      note="one long, two short — margin must see the uncovered short")

strat("Put ratio 1x3", [L("buy","PE",pct(-0.5)), L("sell","PE",pct(-2.0),qty=3)],
      {"cadence":"weekly","dte":5,"time":"11:31"},
      [R({"pnl_pct_of_credit":{"lte":-1.5}}, "close")],
      note="three-to-one; the stop is a multiple of the credit")

strat("Call backspread", [L("sell","CE",ATM), L("buy","CE",pct(1.2),qty=2)],
      {"cadence":"weekly","dte":6,"time":"11:52"},
      [R({"spot_move_pct":{"between":[-0.2,0.2]}}, "close")],
      note="long convexity, closed if the market goes nowhere")

strat("Unbalanced condor",
      [L("sell","CE",pct(0.9),qty=2), L("buy","CE",pct(1.8),qty=2),
       L("sell","PE",pct(-0.9)), L("buy","PE",pct(-1.8))],
      {"cadence":"weekly","dte":3,"time":"12:08"},
      [R({"pnl_pts":{"gte":55}}, "close")],
      note="twice as much call side as put side")

strat("Premium-selected 3x2", [L("sell","CE",prem(35),qty=3), L("buy","CE",prem(15),qty=2)],
      {"cadence":"weekly","dte":2,"time":"12:26"},
      [R({"leg_mark_delta":{"gte":25,"leg":0}}, "close")],
      note="quantities and strikes both chosen by premium")

strat("Broken-wing put fly",
      [L("buy","PE",pct(-0.5)), L("sell","PE",pct(-1.5),qty=2), L("buy","PE",pct(-3.0))],
      {"cadence":"weekly","dte":4,"time":"12:41"},
      [R({"runup_from_trough":{"gte":18}}, "close")],
      note="1-2-1 with unequal wings")

strat("Put ladder",
      [L("sell","PE",pct(-0.7)), L("sell","PE",pct(-1.4)), L("sell","PE",pct(-2.1))],
      {"cadence":"weekly","dte":3,"time":"12:57"},
      [R({"leg_pnl_pts":{"lte":-30,"leg":2}}, "close")],
      note="stopped by the FURTHEST strike's own loss")

strat("One short, four cheap longs",
      [L("sell","CE",dlt(0.25)), L("buy","CE",dlt(0.05),qty=4)],
      {"cadence":"weekly","dte":5,"time":"13:11"},
      [R({"spot_move_pct":{"gte":2.0}}, "close")],
      note="a crash-up hedge bolted onto a short call")

strat("Ten-lot far put spread",
      [L("sell","PE",pct(-2.5),qty=10), L("buy","PE",pct(-3.5),qty=10)],
      {"cadence":"weekly","dte":6,"time":"13:28"},
      [R({"pnl_rupees":{"lte":-25000}}, "close")],
      note="size on both legs; rupee stop scales with it")

strat("Five by three call spread",
      [L("sell","CE",prem(45),qty=5), L("buy","CE",prem(20),qty=3)],
      {"cadence":"weekly","dte":1,"time":"13:44"},
      [R({"credit_kept_pct":{"lte":-0.5}}, "close")],
      note="partially covered; two of the five shorts are naked")


# ============================================================= 55-64  entry gates
strat("Only if the strangle pays 120",
      [L("sell","CE",pct(1.6)), L("sell","PE",pct(-1.6))],
      {"cadence":"weekly","dte":4,"time":"13:58","when":{"combined_premium":{"gte":120}}},
      [R({"pnl_pct_of_credit":{"gte":0.55}}, "close")],
      note="refuses the cycle when premium is thin")

strat("Only when the market is quiet",
      [L("sell","CE",pct(1.1)), L("sell","PE",pct(-1.1)), L("buy","CE",pct(2.5))],
      {"cadence":"weekly","dte":3,"time":"14:12","when":{"combined_premium":{"lte":45}}},
      [R({"pnl_pct_of_credit":{"gte":0.65}}, "close")],
      note="the inverse gate — take it only when premium is cheap")

strat("Goldilocks credit band",
      [L("sell","CE",dlt(0.22)), L("sell","PE",dlt(0.22))],
      {"cadence":"weekly","dte":5,"time":"14:27","when":{"combined_premium":{"between":[60,90]}}},
      [R({"drawdown_from_peak":{"gte":25}}, "close")],
      note="a band, not a floor")

strat("Only above 24000", [L("sell","PE",pct(-1.8)), L("buy","PE",pct(-3.0))],
      {"cadence":"weekly","dte":2,"time":"14:39","when":{"spot":{"gte":24000}}},
      [R({"pnl_pts":{"lte":-20}}, "close")],
      note="a level filter on the index itself")

strat("Inside the range", [L("sell","CE",pct(2.0)), L("sell","PE",pct(-2.0))],
      {"cadence":"weekly","dte":6,"time":"14:52","when":{"spot":{"between":[20000,30000]}}},
      [R({"minutes_held":{"gte":4000}}, "close")],
      note="index inside a band at entry")

strat("Only if the call itself is rich", [L("sell","CE",pct(0.75))],
      {"cadence":"weekly","dte":4,"time":"15:03","when":{"leg_mark":{"gte":80,"leg":0}}},
      [R({"leg_mark_mult":{"gte":1.8,"leg":0}}, "close")],
      note="gate on ONE leg's price at entry")

strat("Only if the hedge is cheap",
      [L("sell","PE",pct(-1.0)), L("buy","PE",pct(-2.0))],
      {"cadence":"weekly","dte":3,"time":"15:17","when":{"leg_mark":{"lte":25,"leg":1}}},
      [R({"pnl_pct_of_max":{"gte":0.55}}, "close")],
      note="gate on what the protection costs")

strat("Two conditions to get in",
      [L("sell","CE",pct(1.25)), L("sell","PE",pct(-1.25)), L("buy","PE",pct(-2.5))],
      {"cadence":"weekly","dte":5,"time":"09:18",
       "when":{"all":[{"combined_premium":{"gte":50}},{"spot":{"gte":21000}}]}},
      [R({"pnl_pts":{"gte":45}}, "close")],
      note="AND of a premium floor and a level filter")

strat("Either leg rich enough",
      [L("sell","CE",prem(90)), L("sell","PE",prem(90))],
      {"cadence":"weekly","dte":2,"time":"09:28",
       "when":{"any":[{"leg_mark":{"gte":100,"leg":0}},{"leg_mark":{"gte":100,"leg":1}}]}},
      [R({"spot_move_pct":{"lte":-1.5}}, "close")],
      note="OR across two legs at entry")

strat("Never for less than 30",
      [L("sell","CE",pct(1.35)), L("sell","PE",pct(-1.35))],
      {"cadence":"weekly","dte":7,"time":"09:37","when":{"not":{"combined_premium":{"lt":30}}}},
      [R({"adjustments_done":{"gte":1}}, "close")],
      note="a negated gate, and a rule that can never fire because nothing adjusts")


# ========================================================= 65-74  path dependence
strat("Tight trail", [L("sell","CE",pct(1.05)), L("sell","PE",pct(-1.05))],
      {"cadence":"weekly","dte":4,"time":"09:47"},
      [R({"drawdown_from_peak":{"gte":10}}, "close")],
      note="gives back ten points from the best mark and it is over")

strat("Trail only once it is winning",
      [L("sell","CE",dlt(0.18)), L("sell","PE",dlt(0.18))],
      {"cadence":"weekly","dte":5,"time":"09:54"},
      [R({"all":[{"pnl_pts":{"gte":40}},{"drawdown_from_peak":{"gte":15}}]}, "close")],
      note="the trail arms itself at +40 and not before")

strat("Get out on the bounce", [L("sell","PE",pct(-1.6)), L("buy","PE",pct(-2.4))],
      {"cadence":"weekly","dte":6,"time":"10:03"},
      [R({"runup_from_trough":{"gte":25}}, "close")],
      note="exits into recovery rather than into strength")

strat("Two repairs and then done",
      [L("sell","CE",pct(1.15)), L("sell","PE",pct(-1.15))],
      {"cadence":"weekly","dte":3,"time":"10:14"},
      [R({"spot_beyond_strike":{"gte":-15,"leg":1}}, {"roll":{"legs":[1],"to":pct(-1.8)}}, 5),
       R({"adjustments_done":{"gte":2}}, "close")],
      max_adj=6, note="the position counts its own repairs and quits after two")

strat("Profit ratchet",
      [L("sell","CE",pct(0.85)), L("sell","PE",pct(-0.85)),
       L("buy","CE",pct(1.7)), L("buy","PE",pct(-1.7))],
      {"cadence":"weekly","dte":5,"time":"10:26"},
      [R({"pnl_pct_of_credit":{"gte":0.30}}, {"close_legs":[2]}),
       R({"pnl_pct_of_credit":{"gte":0.45}}, {"close_legs":[3]}),
       R({"pnl_pct_of_credit":{"gte":0.80}}, "close")],
      max_adj=3, note="three tiers, each taking one more piece off")

strat("Re-centre after damage", [L("sell","CE",ATM), L("sell","PE",pct(-0.6))],
      {"cadence":"weekly","dte":4,"time":"10:41"},
      [R({"drawdown_from_peak":{"gte":35}},
         {"close_and_open":{"close":"all",
                            "open":[L("sell","CE",pct(1.3)), L("sell","PE",pct(-1.3))]}}),
       R({"runup_from_trough":{"gte":42}}, "close")],
      max_adj=1, note="takes the loss and re-establishes wider")

strat("Held for a fixed window", [L("buy","CE",pct(0.3)), L("sell","CE",pct(1.0),qty=2)],
      {"cadence":"weekly","dte":2,"time":"10:55"},
      [R({"minutes_held":{"between":[500,600]}}, "close")],
      note="a window rather than a threshold — closes INSIDE the band")

strat("Cut the losers on the last day", [L("sell","CE",prem(70)), L("sell","PE",prem(70))],
      {"cadence":"weekly","dte":3,"time":"11:09"},
      [R({"all":[{"dte":{"lte":1}},{"pnl_pts":{"lt":0}}]}, "close")],
      note="lets winners go to expiry, refuses to carry losers there")

strat("Eighty per cent given back", [L("sell","PE",dlt(0.28)), L("buy","PE",dlt(0.10))],
      {"cadence":"weekly","dte":5,"time":"11:21"},
      [R({"credit_kept_pct":{"lte":0.20}}, "close")],
      note="stop framed as how much of the credit survives")

strat("Squeeze the last 15%",
      [L("sell","CE",pct(1.45)), L("buy","CE",pct(2.2)),
       L("sell","PE",pct(-1.45)), L("buy","PE",pct(-2.2))],
      {"cadence":"weekly","dte":6,"time":"11:34"},
      [R({"pnl_pct_of_max":{"gte":0.85}}, "close")],
      note="holds for almost all of the theoretical maximum")


# ============================================================ 75-84  book rules
strat("Two losers and stand down", [L("sell","CE",pct(1.55)), L("sell","PE",pct(-1.55))],
      {"cadence":"weekly","dte":4,"time":"11:44"},
      [R({"pnl_pct_of_credit":{"lte":-1.0}}, "close")],
      portfolio={"stop_after_losses":2,"resume_after_days":14},
      note="the book stops, then comes back a fortnight later")

strat("Stop the book at -12%", [L("sell","CE",dlt(0.32)), L("buy","CE",dlt(0.12))],
      {"cadence":"weekly","dte":3,"time":"11:57"},
      [R({"pnl_pts":{"lte":-15}}, "close")],
      portfolio={"stop_after_drawdown_pct":12},
      note="an equity-curve stop, not a trade stop")

strat("Sit out the day after a loss", [L("sell","PE",pct(-0.55)), L("buy","PE",pct(-1.1))],
      {"cadence":"daily","max_dte":2,"time":"12:11"},
      [R({"pnl_pts":{"gte":8}}, "close")],
      exit={"time":"15:19"}, portfolio={"skip_after_loss":True},
      note="skips the very next cycle whenever one loses")

strat("First 25 trades only", [L("sell","CE",pct(1.9)), L("sell","PE",pct(-1.9))],
      {"cadence":"weekly","dte":2,"time":"12:29"},
      [R({"pnl_pct_of_credit":{"gte":0.90}}, "close")],
      portfolio={"max_trades":25}, note="a hard cap on how many trades the book takes")

strat("Take the year off at +40%", [L("sell","CE",ATM), L("buy","CE",pct(0.8)),
                                    L("sell","PE",ATM), L("buy","PE",pct(-0.8))],
      {"cadence":"weekly","dte":5,"time":"12:36"},
      [R({"pnl_pts":{"lte":-30}}, "close")],
      portfolio={"stop_after_profit_pct":40}, note="stops on SUCCESS, which nobody codes")

strat("Four strikes and a ceiling", [L("sell","CE",prem(55)), L("sell","PE",prem(55)),
                                      L("buy","CE",prem(12))],
      {"cadence":"weekly","dte":6,"time":"12:48"},
      [R({"spot_move_pct":{"gte":1.8}}, "close")],
      portfolio={"stop_after_losses":4,"max_trades":40},
      note="two independent book limits at once")

strat("Fragile book, patient restart", [L("sell","CE",pct(0.95),qty=2), L("buy","CE",pct(1.9),qty=2)],
      {"cadence":"weekly","dte":1,"time":"13:06"},
      [R({"leg_mark_mult":{"gte":3.0,"leg":0}}, "close")],
      portfolio={"stop_after_drawdown_pct":8,"resume_after_days":21},
      note="an 8% drawdown halts it for three weeks")

strat("Skip and stop", [L("sell","PE",pct(-2.2)), L("sell","CE",pct(2.6))],
      {"cadence":"weekly","dte":7,"time":"13:16"},
      [R({"pnl_rupees":{"gte":9000}}, "close")],
      portfolio={"skip_after_loss":True,"stop_after_losses":5},
      note="cools off after each loss AND quits after five")

strat("Cap the upside and the count", [L("buy","PE",pct(-0.4)), L("sell","PE",pct(-1.2),qty=2),
                                        L("buy","PE",pct(-2.4))],
      {"cadence":"weekly","dte":3,"time":"13:24"},
      [R({"drawdown_from_peak":{"gte":22}}, "close")],
      portfolio={"stop_after_profit_pct":25,"max_trades":60},
      note="stops at +25% or 60 trades, whichever lands first")

strat("Every book rule at once", [L("sell","CE",dlt(0.20)), L("sell","PE",dlt(0.20)),
                                   L("buy","CE",dlt(0.06)), L("buy","PE",dlt(0.06))],
      {"cadence":"weekly","dte":4,"time":"13:33"},
      [R({"pnl_pct_of_max":{"gte":0.70}}, "close")],
      portfolio={"stop_after_losses":3,"stop_after_drawdown_pct":20,"resume_after_days":45},
      note="three book rules interacting")


# ======================================================= 85-94  positions that change
strat("Scale in when it is working", [L("sell","CE",pct(1.28)), L("sell","PE",pct(-1.32))],
      {"cadence":"weekly","dte":5,"time":"13:41"},
      [R({"pnl_pct_of_credit":{"gte":0.40}},
         {"open":[L("sell","CE",pct(1.9)), L("sell","PE",pct(-1.9))]}),
       R({"pnl_pts":{"lte":-55}}, "close")],
      max_adj=2, note="adds a second tranche once the first is ahead")

strat("Re-centre at noon", [L("sell","CE",pct(0.65)), L("sell","PE",pct(-0.65))],
      {"cadence":"weekly","dte":4,"time":"13:48"},
      [R({"time":{"gte":"12:00"}},
         {"close_and_open":{"close":"all",
                            "open":[L("sell","CE",ATM), L("sell","PE",ATM)]}})],
      exit={"when":{"drawdown_from_peak":{"gte":27}}},
      max_adj=1, note="a scheduled re-strike, driven purely by the clock")

strat("Roll six times if it must", [L("sell","CE",prem(45))],
      {"cadence":"weekly","dte":7,"time":"14:02"},
      [R({"leg_mark_mult":{"gte":1.6,"leg":0}}, {"roll":{"legs":[0],"to":pct(1.4)}}, 6),
       R({"leg_mark":{"gte":210,"leg":0}}, "close")],
      max_adj=8, note="a single leg chased up the chain all week")

strat("Roll and cut the size", [L("sell","PE",pct(-1.0),qty=4)],
      {"cadence":"weekly","dte":3,"time":"14:09"},
      [R({"spot_beyond_strike":{"gte":-30,"leg":0}},
         {"roll":{"legs":[0],"to":pct(-2.0),"qty":2}}, 2)],
      exit={"when":{"spot_move_pts":{"lte":-330}}}, max_adj=3, note="the roll halves the position as it moves it")

strat("Strangle becomes a condor", [L("sell","CE",pct(1.45)), L("sell","PE",pct(-1.45))],
      {"cadence":"weekly","dte":6,"time":"14:16"},
      [R({"drawdown_from_peak":{"gte":18}},
         {"open":[L("buy","CE",pct(2.3)), L("buy","PE",pct(-2.3))]}),
       R({"pnl_pct_of_credit":{"gte":0.85}}, "close")],
      max_adj=2, note="defines its own risk only once risk shows up")

strat("Fly becomes a condor", [L("sell","CE",ATM), L("sell","PE",ATM),
                                L("buy","CE",pct(1.6)), L("buy","PE",pct(-1.6))],
      {"cadence":"weekly","dte":2,"time":"14:24"},
      [R({"spot_move_pct":{"gte":0.55}},
         {"close_and_open":{"close":[0,1],
                            "open":[L("sell","CE",pct(0.9)), L("sell","PE",pct(-0.9))]}}),
       R({"pnl_pts":{"gte":62}}, "close")],
      max_adj=1, note="keeps the wings, widens the body")

strat("Patch the delta", [L("sell","CE",pct(0.8)), L("buy","CE",pct(1.5))],
      {"cadence":"weekly","dte":4,"time":"14:36"},
      [R({"spot_move_pts":{"gte":250}}, {"open":[L("buy","CE",ATM)]}),
       R({"spot_move_pts":{"lte":-250}}, {"close_legs":[1]})],
      max_adj=2, note="two rules pulling in opposite directions on the same axis")

strat("Second tranche on the clock", [L("sell","PE",pct(-1.7))],
      {"cadence":"weekly","dte":5,"time":"14:43"},
      [R({"time":{"gte":"13:15"}}, {"open":[L("sell","PE",pct(-2.4))]}),
       R({"pnl_pts":{"lte":-40}}, "close")],
      max_adj=2, note="a timed add, unconditional on P&L")

strat("Open, trim, reopen", [L("sell","CE",pct(1.02)), L("sell","PE",pct(-0.98))],
      {"cadence":"weekly","dte":6,"time":"14:56"},
      [R({"pnl_pts":{"gte":25}}, {"close_legs":[0]}),
       R({"all":[{"pnl_pts":{"gte":25}},{"spot_move_pct":{"lte":-0.5}}]},
         {"open":[L("sell","CE",pct(1.6))]}),
       R({"pnl_pts":{"lte":-70}}, "close")],
      max_adj=3, note="takes a leg off, then puts a different one back on")

strat("Move everything in points", [L("sell","CE",pts(400)), L("sell","PE",pts(-400))],
      {"cadence":"weekly","dte":3,"time":"15:07"},
      [R({"spot_move_pts":{"between":[-120,120]}}, {"roll":{"legs":"all","to":pts(250)}})],
      exit={"when":{"minutes_held":{"gte":1750}}}, max_adj=1, note="strikes and triggers both in index points, never percent")


# =========================================================== 95-100  structural limits
strat("Twelve legs",
      [L("sell","CE",pct(0.5)), L("sell","PE",pct(-0.5)),
       L("buy","CE",pct(1.0)), L("buy","PE",pct(-1.0)),
       L("sell","CE",pct(1.5)), L("sell","PE",pct(-1.5)),
       L("buy","CE",pct(2.0)), L("buy","PE",pct(-2.0)),
       L("sell","CE",pct(2.5)), L("sell","PE",pct(-2.5)),
       L("buy","CE",pct(3.0)), L("buy","PE",pct(-3.0))],
      {"cadence":"weekly","dte":4,"time":"15:11"},
      [R({"pnl_pts":{"gte":30}}, "close")],
      note="the maximum position the protocol allows")

strat("Twenty-four rules", [L("sell","CE",pct(1.05),qty=1), L("sell","PE",pct(-0.95))],
      {"cadence":"weekly","dte":5,"time":"15:20"},
      [R({"pnl_pts":{"gte": 6 + 3 * i}}, {"close_legs":[i % 2]}) for i in range(12)]
      + [R({"pnl_pts":{"lte": -8 - 4 * i}}, "close") for i in range(12)],
      max_adj=12, note="the rule ceiling, evaluated on every minute of every leg")

strat("Fifty adjustments", [L("sell","CE",prem(20))],
      {"cadence":"daily","max_dte":4,"time":"09:21"},
      [R({"leg_mark_mult":{"gte":1.35,"leg":0}}, {"roll":{"legs":[0],"to":prem(20)}}, 50)],
      exit={"time":"15:24"}, max_adj=50,
      note="the termination backstop, deliberately driven to its cap")

strat("Coarse walk, five minutes", [L("sell","CE",pct(1.12)), L("sell","PE",pct(-1.12))],
      {"cadence":"daily","max_dte":3,"time":"09:31"},
      [R({"pnl_pct_of_credit":{"gte":0.42}}, "close")],
      exit={"time":"15:23"}, resolution=5,
      note="every fifth minute — the cheap setting, and it must SAY so")

strat("Coarse walk, fifteen minutes", [L("sell","CE",dlt(0.24)), L("sell","PE",dlt(0.24))],
      {"cadence":"daily","max_dte":5,"time":"09:39"},
      [R({"drawdown_from_peak":{"gte":16}}, "close")],
      exit={"time":"15:21"}, resolution=15,
      note="the coarsest setting the protocol offers")

strat("Four levels of nesting",
      [L("sell","CE",pct(1.6),qty=2), L("buy","CE",pct(2.4),qty=2), L("sell","PE",pct(-1.1))],
      {"cadence":"weekly","dte":6,"time":"09:50"},
      [R({"all":[
            {"any":[{"pnl_pts":{"gte":35}}, {"minutes_held":{"gte":2500}}]},
            {"not":{"all":[{"spot_move_pct":{"gte":1.2}},
                           {"drawdown_from_peak":{"lte":5}}]}},
            {"dte":{"gte":0}}]}, "close")],
      note="all / any / not / cmp, nested four deep")


# ==================================================================== the uniqueness test
import json


def _sig(d):
    return json.dumps(d, sort_keys=True, default=str)


def _close_conditions(spec):
    """The conditions that END the trade -- the 'way out' axis, separate from the
    management axis, because two strategies can be managed alike and exit differently."""
    out = []
    for r in spec.get("rules", []):
        t = r["then"]
        if t == "close" or (isinstance(t, dict) and "close_legs" in t):
            out.append(r["when"])
    return out


AXES = {
    "entry":      lambda s: _sig(s["entry"]),
    "position":   lambda s: _sig(s["legs"]),
    "management": lambda s: _sig([s.get("rules", []), s.get("max_adjustments")]),
    "exit":       lambda s: _sig([s.get("exit"), s.get("portfolio"),
                                  _close_conditions(s)]),
}


def collisions():
    """Any two strategies sharing a signature on any axis. Must be empty -- that is the
    whole premise of the exercise."""
    bad = []
    for axis, fn in AXES.items():
        seen = {}
        for spec, _ in S:
            k = fn(spec)
            if k in seen:
                bad.append((axis, seen[k], spec["name"]))
            seen[k] = spec["name"]
    return bad


if __name__ == "__main__":
    import sys, time, traceback
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from engine import strategy as strategy_mod, simulate, metrics, spec as spec_mod

    dupes = collisions()
    print(f"{len(S)} strategies")
    if dupes:
        for axis, a, b in dupes:
            print(f"  COLLISION on {axis}: {a!r} == {b!r}")
        sys.exit(1)
    print("no two share an entry, a position, a management scheme or an exit\n")

    window = spec_mod.window_for("free")
    rows, t_all = [], time.time()
    for i, (spec, note) in enumerate(S, 1):
        rec = {"n": i, "name": spec["name"], "note": note,
               "legs": len(spec["legs"]), "rules": len(spec.get("rules", []))}
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
                       reasons=m.get("exit_reasons"), notes=res.notes,
                       skipped=res.skipped, warnings=m.get("warnings"))
        except strategy_mod.StrategyError as e:
            rec.update(status="refused", error=str(e))
        except Exception as e:
            rec.update(status="CRASH", error=f"{type(e).__name__}: {e}",
                       tb=traceback.format_exc()[-1200:])
        rec["secs"] = round(time.time() - t0, 2)
        rows.append(rec)
        flag = {"ok": " ", "refused": "?", "CRASH": "!"}[rec["status"]]
        print(f"{flag}{i:>4}. {rec['name'][:38]:<38} {rec.get('shape','-')[:20]:<20} "
              f"{rec.get('trades',''):>4} tr {rec.get('actions',''):>4} act "
              f"{rec.get('net',''):>8} pts {rec['secs']:>6.2f}s "
              f"{rec.get('error','')[:70]}", flush=True)

    print(f"\ntotal {time.time() - t_all:.1f}s")
    json.dump(rows, open("/tmp/torture100.json", "w"), indent=1, default=str)
