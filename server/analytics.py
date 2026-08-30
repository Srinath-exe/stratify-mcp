"""Everything a trader wants to know that the summary does not say.

WHY THIS MODULE EXISTS. A backtest summary reports what a strategy MADE. That is the
least interesting question about it. The ones that decide whether a person trades it are:

    is the edge real, or is it the best of many things I tried
    where did the money actually come from -- was it three trades, or one year
    how long was I under water, not just how deep
    what does the loss tail look like next to the win body
    does it only work in one volatility regime
    what did it cost to run, and how much capital did it really need

None of those are in `summarise()`, and every one of them is computable from data we
already hold: the price-free trade rows and the daily index candles. Nothing here needs a
database, an option price, or a second backtest -- which is why the report can be a pure
function and re-render in fifty milliseconds.

WHAT IS NOT HERE, DELIBERATELY. Intra-trade heat (MAE/MFE) needs the minute-by-minute
premium of the position, which lives in the replay track and is not available for every
strategy. Rather than compute it sometimes and silently omit it other times -- which is
how a reader concludes a strategy never went against them -- the report points at the
replay page for the path a trade took and does not half-report it here.

ORDERING. Everything is computed in EXIT order. Equity arrives when a position closes,
not when it opens; on any cadence where positions overlap, entry order credits profit to
a moment the account had not received it. metrics.py enforces the same convention, and the
two must agree or the headline drawdown and the chart disagree by a few hundred rupees
and nobody can tell which is wrong.
"""
import datetime as dt
import math
import statistics

from . import sizing

VOL_WINDOW = 20                 # trading days of close-to-close, the standard short window
TRADING_DAYS = 252
ROLLING_DAYS = 365              # a rolling YEAR, in calendar days, not 252 trade rows
# Annualising a two-day window raises a daily return to the power of 182 and the
# result is either meaningless or an OverflowError. Below this span there is no CAGR.
MIN_DAYS_FOR_CAGR = 30
HIST_BINS = 21                  # odd, so a bin is centred on zero
TOP_N = 10                      # how many trades the concentration read-out names


# ------------------------------------------------------------------------ dates

def _d(s):
    """'YYYY-MM-DD...' -> date. Every timestamp in a trade row starts with the date."""
    return dt.date(int(s[0:4]), int(s[5:7]), int(s[8:10]))


def _days(a, b):
    return (b - a).days


# --------------------------------------------------------------------- the pass

def build(trades, candles, capital=sizing.DEFAULT_CAPITAL, deploy=sizing.DEFAULT_DEPLOY,
          compounding=True):
    """-> every derived series the report draws, from price-free rows and index candles."""
    usable = [t for t in trades
              if t.get("pnl_rupees") is not None and t.get("margin_points")
              and t.get("lot_size") and t.get("exit")]
    usable.sort(key=lambda t: (t["exit"], t.get("entry", "")))
    if not usable:
        return {"empty": True}

    sized = sizing.apply(usable, capital=capital, deploy=deploy, compounding=compounding)
    # 1:1 after the pre-filter above -- sizing.apply only skips rows it cannot size, and
    # those are exactly the ones already removed. Zipping an unfiltered list silently
    # shifts every trade against its own equity point.
    curve = sized["curve"]
    assert len(curve) == len(usable), "sizing curve must stay aligned to its trades"
    pts = [dict(c, trade=t) for c, t in zip(curve, usable)]

    equity = _equity_series(pts, capital)
    vol = _realised_vol(candles)
    sma = _sma(candles, 50)

    return {
        "empty": False,
        "capital": capital,
        "deploy": deploy,
        "sized": {k: v for k, v in sized.items() if k != "curve"},
        "equity": equity,
        "underwater": _underwater(equity),
        "episodes": _episodes(equity),
        "monthly": _monthly(equity, capital),
        "annual": _annual(equity, pts, capital),
        "rolling": _rolling(equity),
        "distribution": _distribution(usable),
        "concentration": _concentration(pts, usable, capital, deploy, compounding),
        "regimes": _regimes(pts, vol, sma),
        "benchmark": _benchmark(candles, equity, capital),
        "costs": _costs(usable),
        "streaks": _streaks(pts),
        "margin": _margin(pts, capital),
        "exposure": _exposure(usable, candles),
        "scatter": _scatter(pts, vol),
        "quality": _quality(usable, pts, capital),
        "buckets": _buckets(pts),
        "moves": _moves(pts, candles),
    }


# ------------------------------------------------------------------ equity core

def _equity_series(pts, capital):
    """[{date, equity, pnl, lots, margin, n}] -- one point per closed trade, plus the
    starting point so a curve begins at the capital rather than at the first outcome."""
    out = [{"date": pts[0]["date"], "equity": capital, "pnl": 0.0, "lots": 0,
            "margin": 0.0, "n": 0, "seed": True}]
    for i, p in enumerate(pts, 1):
        out.append({"date": p["date"], "equity": p["equity"], "pnl": p["pnl"],
                    "lots": p["lots"], "margin": p.get("margin", 0.0), "n": i,
                    "trade": p["trade"]})
    return out


def _underwater(equity):
    """Drawdown as a fraction of the running peak, per point. The percentage matters more
    than the rupee figure: a 60,000 loss means one thing on 10 lakh and another on a
    crore, and the reader is deciding on their own account, not on ours."""
    peak, out = equity[0]["equity"], []
    for p in equity:
        peak = max(peak, p["equity"])
        out.append({"date": p["date"], "dd": (p["equity"] - peak) / peak if peak else 0.0,
                    "rs": p["equity"] - peak})
    return out


def _episodes(equity):
    """Every peak-to-recovery drawdown, with its DURATION as well as its depth.

    Duration is the half almost every retail report drops, and it is the half that ends
    accounts. A 22 % drawdown recovered in five weeks is a bad quarter. The same 22 %
    taking nineteen months is the reason the strategy gets abandoned two weeks before it
    would have come back -- so the longest episode is reported next to the deepest, and
    they are usually not the same episode.
    """
    eps, peak, start = [], equity[0]["equity"], equity[0]
    trough = None
    for p in equity:
        if p["equity"] >= peak:
            if trough is not None:
                eps.append(_episode(start, trough, p, peak, recovered=True))
                trough = None
            peak, start = p["equity"], p
        elif trough is None or p["equity"] < trough["equity"]:
            trough = p
    if trough is not None:
        eps.append(_episode(start, trough, equity[-1], peak, recovered=False))
    eps.sort(key=lambda e: e["depth"])
    return eps


def _episode(start, trough, end, peak, recovered):
    a, b, c = _d(start["date"]), _d(trough["date"]), _d(end["date"])
    return {
        "from": start["date"], "trough": trough["date"], "to": end["date"],
        "depth": (trough["equity"] - peak) / peak if peak else 0.0,
        "depth_rs": trough["equity"] - peak,
        "to_trough_days": _days(a, b), "recover_days": _days(b, c),
        "total_days": _days(a, c), "recovered": recovered,
    }


# --------------------------------------------------------------- calendar views

def _step(equity):
    """(sorted dates, equity at each) for as-of lookups. Equity is a step function: it
    changes only when a trade closes and holds its value in between."""
    return [_d(p["date"]) for p in equity], [p["equity"] for p in equity]


def _asof(dates, vals, day, seed):
    """Equity as at the END of `day`. Before the first close, the account is untouched."""
    lo, hi = 0, len(dates)
    while lo < hi:
        mid = (lo + hi) // 2
        if dates[mid] <= day:
            lo = mid + 1
        else:
            hi = mid
    return vals[lo - 1] if lo else seed


def _month_ends(first, last):
    y, m, out = first.year, first.month, []
    while (y, m) <= (last.year, last.month):
        nxt = dt.date(y + (m == 12), 1 if m == 12 else m + 1, 1)
        out.append((f"{y:04d}-{m:02d}", nxt - dt.timedelta(days=1)))
        y, m = nxt.year, nxt.month
    return out


def _monthly(equity, capital):
    """Return per calendar month, as a percentage of the equity that started it.

    Compounded rather than additive: on a running-equity model the same rupee gain is a
    different month depending on what the account was worth when it happened, and adding
    percentages of different bases produces a year figure that matches nothing.
    """
    dates, vals = _step(equity)
    first, last = dates[0], dates[-1]
    prev, cells = capital, []
    for label, end in _month_ends(first, last):
        eq = _asof(dates, vals, end, capital)
        cells.append({"month": label, "year": int(label[:4]), "m": int(label[5:]),
                      "ret": (eq / prev - 1) if prev else 0.0, "rs": eq - prev,
                      "equity": eq})
        prev = eq
    rets = [c["ret"] for c in cells]
    pos = [r for r in rets if r > 0]
    return {
        "cells": cells,
        "best": max(cells, key=lambda c: c["ret"]) if cells else None,
        "worst": min(cells, key=lambda c: c["ret"]) if cells else None,
        "positive": len(pos), "total": len(cells),
        "hit_rate": len(pos) / len(cells) if cells else 0.0,
        "stdev": statistics.stdev(rets) if len(rets) > 1 else 0.0,
    }


def _annual(equity, pts, capital):
    """Per calendar year: return, trades, hit rate, and the worst drawdown INSIDE the year.

    The year is the unit a person actually judges a strategy in -- nobody says "it was
    fine over the 2019-2026 window" while watching it lose for fourteen months. A
    seven-year total that came from one year is a different product from one that earned
    steadily, and only this table separates them.
    """
    dates, vals = _step(equity)
    years = sorted({d.year for d in dates})
    by_year = {}
    for p in pts:
        by_year.setdefault(_d(p["date"]).year, []).append(p)
    out, prev = [], capital
    for y in years:
        end = _asof(dates, vals, dt.date(y, 12, 31), capital)
        rows = by_year.get(y, [])
        wins = sum(1 for r in rows if r["pnl"] > 0)
        inner = [p for p in equity if _d(p["date"]).year == y]
        peak, worst = prev, 0.0
        for p in inner:
            peak = max(peak, p["equity"])
            worst = min(worst, (p["equity"] - peak) / peak if peak else 0.0)
        out.append({"year": y, "start": prev, "end": end,
                    "ret": (end / prev - 1) if prev else 0.0, "rs": end - prev,
                    "n": len(rows), "wins": wins,
                    "hit": wins / len(rows) if rows else 0.0,
                    "dd": worst,
                    "best": max((r["pnl"] for r in rows), default=0.0),
                    "worst": min((r["pnl"] for r in rows), default=0.0)})
        prev = end
    return out


def _rolling(equity, window=ROLLING_DAYS):
    """Trailing twelve-month return at every close, once a full year exists behind it.

    This is the plot that answers "was it still working recently". An equity curve that
    rises for seven years hides a strategy that stopped working in year five, because the
    slope of an accumulating series is hard to read by eye; a rolling return crossing zero
    is not.
    """
    dates, vals = _step(equity)
    out = []
    for i, p in enumerate(equity):
        d = dates[i]
        back = d - dt.timedelta(days=window)
        if back < dates[0]:
            continue
        was = _asof(dates, vals, back, vals[0])
        if was:
            out.append({"date": p["date"], "ret": p["equity"] / was - 1})
    return out


# ------------------------------------------------------------------ distribution

def _distribution(trades):
    """The per-trade return-on-margin distribution, in bins, with the shape statistics.

    ON MARGIN, NOT IN RUPEES. Return-on-margin is size-independent, so this histogram
    describes the strategy rather than the position size someone chose to draw it at -- and
    it is directly comparable between a condor risking 150 points and a strangle risking
    600.

    SKEW IS THE POINT. A premium seller wins often and small and loses rarely and large;
    the mean and the win rate both flatter it, and only the shape shows the trade that
    takes a year of gains back. The report prints the worst loss as a MULTIPLE of the
    average win for exactly that reason.
    """
    roms = [rom(t) for t in trades]
    roms = [r for r in roms if r is not None]
    if not roms:
        return None
    lo, hi = min(roms), max(roms)
    span = (hi - lo) or 1.0
    pad = span * 0.02
    lo, hi = lo - pad, hi + pad
    w = (hi - lo) / HIST_BINS
    bins = [{"lo": lo + i * w, "hi": lo + (i + 1) * w, "n": 0, "pnl": 0.0}
            for i in range(HIST_BINS)]
    for t in trades:
        r = rom(t)
        if r is None:
            continue
        i = min(HIST_BINS - 1, max(0, int((r - lo) / w)))
        bins[i]["n"] += 1
        bins[i]["pnl"] += t["pnl_rupees"]
    s = sorted(roms)
    wins = [r for r in roms if r > 0]
    losses = [r for r in roms if r <= 0]
    mean = statistics.mean(roms)
    sd = statistics.stdev(roms) if len(roms) > 1 else 0.0
    avg_win = statistics.mean(wins) if wins else 0.0
    avg_loss = statistics.mean(losses) if losses else 0.0
    return {
        "bins": bins, "n": len(roms), "mean": mean, "median": statistics.median(roms),
        "stdev": sd, "skew": _moment(roms, mean, sd, 3), "kurtosis": _moment(roms, mean, sd, 4),
        "p05": _q(s, 0.05), "p25": _q(s, 0.25), "p75": _q(s, 0.75), "p95": _q(s, 0.95),
        "best": s[-1], "worst": s[0],
        "avg_win": avg_win, "avg_loss": avg_loss,
        "payoff": (avg_win / abs(avg_loss)) if avg_loss else None,
        "win_rate": len(wins) / len(roms),
        # The number that decides whether a high win rate is worth anything.
        "worst_over_avg_win": (abs(s[0]) / avg_win) if avg_win > 0 else None,
        "tail_ratio": (_q(s, 0.95) / abs(_q(s, 0.05))) if _q(s, 0.05) < 0 else None,
        "expectancy": mean,
        "zero_at": (0.0 - lo) / (hi - lo),
    }


def rom(t):
    """Return on margin, derived if the engine did not ship it. A row that carries net
    points and a margin has said everything the ratio needs; refusing to compute it there
    drops the whole risk section over a naming difference."""
    r = t.get("return_on_margin")
    if r is not None:
        return r
    net, mg = t.get("net_points"), t.get("margin_points")
    return (net / mg) if (net is not None and mg) else None


def _moment(xs, mean, sd, k):
    if sd <= 0 or len(xs) < 3:
        return 0.0
    m = sum((x - mean) ** k for x in xs) / len(xs) / sd ** k
    return m - 3.0 if k == 4 else m         # excess kurtosis: normal reads 0, not 3


def _q(sorted_xs, p):
    if not sorted_xs:
        return 0.0
    i = p * (len(sorted_xs) - 1)
    lo = int(math.floor(i))
    hi = min(lo + 1, len(sorted_xs) - 1)
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (i - lo)


# ----------------------------------------------------------------- concentration

def _concentration(pts, usable, capital, deploy, compounding):
    """How much of the result came from how few trades -- and what is left without them.

    THE MOST UNCOMFORTABLE PANEL IN THE REPORT, and the reason to build it. A strategy
    whose entire seven-year profit is ten trades is a lottery ticket with a backtest
    attached: the next seven years contain a different ten trades, or none. Removing the
    best few and re-running the same capital model is the cheapest way to see whether an
    edge is broad or is one event wearing a strategy's clothes.
    """
    ranked = sorted(pts, key=lambda p: p["pnl"], reverse=True)
    gross_win = sum(p["pnl"] for p in pts if p["pnl"] > 0) or 1.0
    gross_loss = -sum(p["pnl"] for p in pts if p["pnl"] < 0) or 1.0
    net = sum(p["pnl"] for p in pts)
    cum, share = 0.0, []
    for i, p in enumerate(ranked, 1):
        cum += max(p["pnl"], 0.0)
        share.append(cum / gross_win)

    def without(drop_best, drop_worst):
        """Re-SIZE the remaining trades, do not just subtract their rupees.

        Subtracting is the tempting shortcut and it is wrong under compounding: every
        trade after the removed one was sized off an account that no longer had that
        gain in it, so the honest counterfactual re-runs the capital model on the
        surviving trades rather than editing the total afterwards.
        """
        drop = {id(p["trade"]) for p in ranked[:drop_best]}
        if drop_worst:
            drop |= {id(p["trade"]) for p in ranked[len(ranked) - drop_worst:]}
        keep = [t for t in usable if id(t) not in drop]
        if not keep:
            return capital
        return sizing.apply(keep, capital=capital, deploy=deploy,
                            compounding=compounding)["ending_capital"]

    return {
        "share_curve": share,
        "top5_of_profit": share[4] if len(share) > 4 else (share[-1] if share else 0.0),
        "top10_of_profit": share[min(TOP_N, len(share)) - 1] if share else 0.0,
        "top10_of_net": (sum(p["pnl"] for p in ranked[:TOP_N]) / net) if net else None,
        "net": net, "gross_win": gross_win, "gross_loss": gross_loss,
        "best": ranked[:5],
        "worst": list(reversed(ranked[-5:])),
        "ex_best5": without(5, 0), "ex_worst5": without(0, 5),
        "ex_best5_pct": (without(5, 0) / capital - 1),
        "ex_worst5_pct": (without(0, 5) / capital - 1),
        "n": len(pts),
    }


# ---------------------------------------------------------------------- regimes

def _realised_vol(candles, window=VOL_WINDOW):
    """{date: annualised close-to-close vol}. The market's state, not the strategy's."""
    out, rets = {}, []
    prev = None
    for c in candles:
        px = c.get("close")
        if not px or px <= 0:
            continue
        if prev:
            rets.append(math.log(px / prev))
        prev = px
        if len(rets) >= window:
            w = rets[-window:]
            out[c["time"]] = statistics.stdev(w) * math.sqrt(TRADING_DAYS)
    return out


def _sma(candles, window):
    out, buf = {}, []
    for c in candles:
        buf.append(c.get("close") or 0.0)
        if len(buf) > window:
            buf.pop(0)
        if len(buf) == window:
            out[c["time"]] = (sum(buf) / window, c.get("close"))
    return out


def _lookup(table, day):
    """Nearest value at or before `day` -- a trade can enter on a date the index series
    does not carry (a holiday boundary), and dropping those trades would quietly change
    the population the regime table describes."""
    k = day if isinstance(day, str) else day.isoformat()
    if k in table:
        return table[k]
    keys = sorted(kk for kk in table if kk <= k)
    return table[keys[-1]] if keys else None


def _regimes(pts, vol, sma):
    """The same trades, sorted by what the MARKET was doing when they were taken.

    Buckets are cut on the VOLATILITY SERIES, not on the trades. Cutting terciles on the
    trades themselves guarantees three equal groups whatever the market did, and hides the
    case that matters: a strategy that took ninety per cent of its trades in calm and has
    almost no evidence about anything else.
    """
    vals = sorted(vol.values())
    if len(vals) < 10:
        return None
    t1, t2 = _q(vals, 1 / 3), _q(vals, 2 / 3)
    names = (("calm", f"below {t1 * 100:.0f}%"),
             ("normal", f"{t1 * 100:.0f}–{t2 * 100:.0f}%"),
             ("stressed", f"above {t2 * 100:.0f}%"))
    buckets = {n: {"name": n, "band": b, "n": 0, "pnl": 0.0, "wins": 0, "rom": []}
               for n, b in names}
    trend = {"above": {"name": "index above its 50-day", "n": 0, "pnl": 0.0, "wins": 0,
                       "rom": []},
             "below": {"name": "index below its 50-day", "n": 0, "pnl": 0.0, "wins": 0,
                       "rom": []}}
    for p in pts:
        day = (p["trade"].get("entry") or p["date"])[:10]
        v = _lookup(vol, day)
        if v is not None:
            b = buckets["calm" if v < t1 else "normal" if v < t2 else "stressed"]
            _acc(b, p)
        s = _lookup(sma, day)
        if s and s[1]:
            _acc(trend["above" if s[1] >= s[0] else "below"], p)
    return {"vol": [_fin(buckets[n]) for n, _ in names],
            "trend": [_fin(trend["above"]), _fin(trend["below"])],
            "cuts": [t1, t2], "series": vol}


def _acc(b, p):
    b["n"] += 1
    b["pnl"] += p["pnl"]
    b["wins"] += p["pnl"] > 0
    r = rom(p["trade"])
    if r is not None:
        b["rom"].append(r)


def _fin(b):
    b = dict(b)
    b["hit"] = b["wins"] / b["n"] if b["n"] else 0.0
    b["mean_rom"] = statistics.mean(b["rom"]) if b["rom"] else 0.0
    b.pop("rom")
    return b


# -------------------------------------------------------------------- benchmark

def _benchmark(candles, equity, capital):
    """The index itself, bought and held over the same window, on the same capital.

    NOT AN APOLOGY AND NOT A BOAST. The strategy commits a fraction of capital as margin
    and holds for days; buy-and-hold commits all of it for seven years. They are not the
    same risk and the comparison is not a fair fight in either direction -- which is
    exactly why the number belongs on the page rather than in the reader's head, where it
    is being made anyway and without the caveat.
    """
    if not candles:
        return None
    a, b = _d(equity[0]["date"]), _d(equity[-1]["date"])
    rows = [c for c in candles if a <= _d(c["time"]) <= b and c.get("close")]
    if len(rows) < 2:
        return None
    base = rows[0]["close"]
    series = [{"date": c["time"], "equity": capital * c["close"] / base} for c in rows]
    peak, dd = capital, 0.0
    for s in series:
        peak = max(peak, s["equity"])
        dd = min(dd, (s["equity"] - peak) / peak)
    span = _days(a, b)
    end = series[-1]["equity"]
    return {"series": series, "end": end, "ret": end / capital - 1,
            "cagr": _cagr(end, capital, span),
            "dd": dd, "from": rows[0]["time"], "to": rows[-1]["time"]}


def _cagr(end, start, span_days):
    if end <= 0 or start <= 0 or span_days < MIN_DAYS_FOR_CAGR:
        return None
    return (end / start) ** (365.25 / span_days) - 1


# ------------------------------------------------------------------------ costs

def _costs(trades):
    """Gross edge, then what took it. In POINTS, which is the unit the rule earns in.

    Charges are converted to points by dividing by the lot size, so a rupee cost and a
    points edge are on one axis. Most published options backtests never draw this because
    most of them do not model costs at all -- and a strategy that is gross-profitable and
    net-losing is a finding about trade frequency, not about the rule.
    """
    # .get throughout: a missing cost field should cost the reader one panel, not the
    # whole document. A report that raises because one number is absent is strictly worse
    # than one that says the number is absent.
    net = sum(t.get("net_points") or 0.0 for t in trades)
    slip = sum(t.get("slippage_points") or 0.0 for t in trades)
    chg = sum((t.get("charges_rupees") or 0.0) / t["lot_size"]
              for t in trades if t.get("lot_size"))
    gross = net + slip + chg
    n = len(trades)
    return {"gross": gross, "slippage": slip, "charges": chg, "net": net,
            "surviving": (net / gross) if gross > 0 else None,
            "per_trade": {"gross": gross / n, "slippage": slip / n, "charges": chg / n,
                          "net": net / n},
            "charges_rupees": sum(t.get("charges_rupees") or 0.0 for t in trades),
            "breakeven_points": (slip + chg) / n}


# ---------------------------------------------------------------------- streaks

def _streaks(pts):
    """Runs of wins and losses, with what each run did to the account.

    A count on its own ("longest losing streak: 5") is trivia. Five losses that cost
    3 % is a bad fortnight; five that cost 19 % is the moment the strategy gets turned
    off -- so each run carries its rupee damage and its dates.
    """
    runs, cur = [], None
    for p in pts:
        win = p["pnl"] > 0
        if cur is None or cur["win"] != win:
            cur = {"win": win, "n": 0, "pnl": 0.0, "from": p["date"], "to": p["date"]}
            runs.append(cur)
        cur["n"] += 1
        cur["pnl"] += p["pnl"]
        cur["to"] = p["date"]
    wins = [r for r in runs if r["win"]]
    losses = [r for r in runs if not r["win"]]
    return {"runs": runs,
            "longest_win": max(wins, key=lambda r: r["n"]) if wins else None,
            "longest_loss": max(losses, key=lambda r: r["n"]) if losses else None,
            "costliest_loss": min(losses, key=lambda r: r["pnl"]) if losses else None,
            "n_runs": len(runs)}


def _margin(pts, capital):
    """What fraction of the account was actually committed, trade by trade.

    Peak, not mean. The mean is what the strategy used on a normal day; the peak is the
    capital the account had to HAVE, and the gap between them is where a margin call
    lives. A model that sizes off the average is a model that gets liquidated once.
    """
    used = [(p.get("margin") or 0.0) for p in pts if p["lots"]]
    eq = [p["equity"] for p in pts if p["lots"]]
    frac = [m / e for m, e in zip(used, eq) if e > 0]
    per_lot = [(p["trade"]["margin_points"] * p["trade"]["lot_size"]) for p in pts]
    return {"peak_rs": max(used) if used else 0.0,
            "median_rs": statistics.median(used) if used else 0.0,
            "peak_pct": max(frac) if frac else 0.0,
            "median_pct": statistics.median(frac) if frac else 0.0,
            "per_lot_median": statistics.median(per_lot) if per_lot else 0.0,
            "per_lot_min": min(per_lot) if per_lot else 0.0,
            "per_lot_max": max(per_lot) if per_lot else 0.0,
            "series": [{"date": p["date"], "pct": (p.get("margin") or 0.0) / p["equity"]
                        if p["equity"] else 0.0} for p in pts]}


def _exposure(trades, candles):
    """How much of the available market time the strategy was actually in a position.

    Capital that sits idle six days a week is capital doing nothing, and a return computed
    on it flatters a strategy that is barely ever on. It also cuts the other way -- low
    exposure with a real edge is the good case, because the same account can run several.
    """
    held = sum(t.get("holding_minutes") or 0 for t in trades)
    sessions = len({c["time"] for c in candles}) or 1
    return {"held_minutes": held, "sessions": sessions,
            "share": held / (sessions * 375.0),
            "median_hold": statistics.median([t.get("holding_minutes") or 0
                                              for t in trades])}


def _scatter(pts, vol):
    """Every trade as one point: when, how much it returned on its margin, how big it was.

    THIS REPLACES THE TRADE TABLE. Three hundred and eighty-five rows of numbers is a
    dataset, not a report -- nobody reads it, and the two trades that matter are invisible
    inside it. The same trades as a scatter show the outliers, the clustering, and any
    drift in the edge over time, in one look, and a reader can still click a point and get
    the row.
    """
    out = []
    for i, p in enumerate(pts, 1):
        t = p["trade"]
        day = (t.get("entry") or p["date"])[:10]
        out.append({"n": t.get("n", i), "date": p["date"], "entry": t.get("entry"),
                    "rom": rom(t) or 0.0,
                    "pnl": p["pnl"], "lots": p["lots"],
                    "margin": p.get("margin") or 0.0,
                    "dte": t.get("dte_at_entry"), "reason": t.get("exit_reason", ""),
                    "spot": t.get("spot_at_entry"),
                    "hold": t.get("holding_minutes"),
                    "vol": _lookup(vol, day)})
    return out


def _moves(pts, candles):
    """Where the index actually went, per trade, as a fraction of where it started.

    THE MISSING PICTURE. Every other panel describes the RESULT; this one describes the
    market event the strategy was betting on. Drawn behind a payoff diagram it answers the
    only question that really explains a premium seller -- how often did the index stay
    inside the strikes, and how far outside did it go the times it did not -- which no
    table of win rates can say, because a win rate cannot distinguish "finished just
    inside" from "never came close".

    Exit spot is the index CLOSE on the exit day. Expiry settles on the mean of the last
    thirty minutes, so the two differ by a few points; that is invisible at the scale of a
    distribution measured in whole per cent, and the alternative is a second query.
    """
    close = {c["time"]: c.get("close") for c in candles if c.get("close")}
    days = sorted(close)
    out = []
    for p in pts:
        t = p["trade"]
        entry = t.get("spot_at_entry")
        if not entry:
            continue
        day = (t.get("exit") or p["date"])[:10]
        px = close.get(day)
        if px is None:
            prior = [d for d in days if d <= day]
            px = close[prior[-1]] if prior else None
        if px:
            out.append({"move": px / entry - 1, "pnl": p["pnl"], "entry": entry,
                        "exit": px, "n": t.get("n")})
    if not out:
        return None
    vals = sorted(m["move"] for m in out)
    span = max(abs(vals[0]), abs(vals[-1])) or 0.01
    lo, hi = -span * 1.05, span * 1.05
    nb = 41
    w = (hi - lo) / nb
    bins = [{"lo": lo + i * w, "hi": lo + (i + 1) * w, "n": 0, "pnl": 0.0}
            for i in range(nb)]
    for m in out:
        i = min(nb - 1, max(0, int((m["move"] - lo) / w)))
        bins[i]["n"] += 1
        bins[i]["pnl"] += m["pnl"]
    return {"bins": bins, "n": len(out), "lo": lo, "hi": hi,
            "median_abs": statistics.median(abs(v) for v in vals),
            "p95_abs": _q(sorted(abs(v) for v in vals), 0.95),
            "worst": vals[0], "best": vals[-1],
            "values": [round(m["move"], 5) for m in out]}


def _buckets(pts):
    """Sized P&L by exit reason, by weekday, by days-to-expiry at entry.

    The engine already breaks these down on ONE lot. At one lot every trade counts the
    same, which is the wrong weighting for a question about where money came from: a
    trade taken at nine lots moved the account nine times as far.
    """
    def group(key):
        g = {}
        for p in pts:
            k = key(p)
            if k is None:
                continue
            b = g.setdefault(k, {"key": k, "n": 0, "pnl": 0.0, "wins": 0, "rom": []})
            _acc(b, p)
        return [_fin(b) for _, b in sorted(g.items(), key=lambda kv: str(kv[0]))]

    wd = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    return {
        "reason": group(lambda p: p["trade"].get("exit_reason")),
        "weekday": group(lambda p: wd[_d(p["trade"]["entry"]).weekday()]
                         if p["trade"].get("entry") else None),
        "dte": group(lambda p: (f'{p["trade"]["dte_at_entry"]} DTE'
                                if p["trade"].get("dte_at_entry") is not None else None)),
    }


# ---------------------------------------------------------------------- quality

def _quality(trades, pts, capital):
    """The ratios a professional reads first, and two most reports omit.

    ULCER INDEX -- the root-mean-square of the drawdown series -- is reported alongside
    the maximum because the max is a single instant and the ulcer is the whole experience.
    Two strategies with an identical worst drawdown, one of which spent four years in it,
    score very differently and should.

    KELLY is here with a warning attached rather than left out. It is the growth-optimal
    fraction under the measured win rate and payoff, it is famously too aggressive to
    trade, and half of it is the usual practical answer -- but a reader deploying 25 % of
    capital against a Kelly of 6 % is making a mistake the rest of the page will not
    show them.
    """
    pnl = [p["pnl"] for p in pts]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x <= 0]
    w = len(wins) / len(pnl) if pnl else 0.0
    avg_w = statistics.mean(wins) if wins else 0.0
    avg_l = abs(statistics.mean(losses)) if losses else 0.0
    payoff = (avg_w / avg_l) if avg_l else None
    kelly = (w - (1 - w) / payoff) if payoff else None

    eq = _equity_series(pts, capital)
    uw = _underwater(eq)
    ulcer = math.sqrt(sum(u["dd"] ** 2 for u in uw) / len(uw)) if uw else 0.0
    dd = min(u["dd"] for u in uw) if uw else 0.0
    end = eq[-1]["equity"]
    span = _days(_d(eq[0]["date"]), _d(eq[-1]["date"]))
    yrs = max(span / 365.25, 1e-9)
    cagr = _cagr(end, capital, span)
    return {
        "expectancy_rs": statistics.mean(pnl) if pnl else 0.0,
        "payoff": payoff, "kelly": kelly, "half_kelly": kelly / 2 if kelly else None,
        "ulcer": ulcer,
        "mar": (cagr / abs(dd)) if (cagr and dd) else None,
        "cagr": cagr, "max_dd": dd,
        "profit_factor": (sum(wins) / abs(sum(losses))) if losses and sum(losses) else None,
        "win_rate": w, "avg_win": avg_w, "avg_loss": avg_l,
        "years": yrs,
        # Longest stretch the account spent below a previous high, in calendar days.
        "max_underwater_days": max((e["total_days"] for e in _episodes(eq)), default=0),
    }
