"""Rich per-trade results: what happened, not just what it added up to.

WHY THIS EXISTS. v1 returned a ten-trade preview carrying nothing but a date and a rupee
figure, on the reasoning that aggregates are the product and prices are not. That is the
right instinct applied one step too far: without an entry price, an exit price and a
timestamp, a caller cannot check the arithmetic, cannot see WHERE a drawdown came from,
and cannot plot an equity curve -- so the honesty panel asks to be trusted rather than
verified. A result you cannot audit is a weaker product, not a safer one.

WHAT IS RELEASED, AND WHAT IS STILL NOT. Released: the prices of the contracts this
strategy actually traded, at the minutes it traded them. Not released, and there is no
endpoint for it: the chain. No caller can ask what any OTHER strike was worth, what the
same strike was worth at any other minute, or for a range, a scan or an export.

THE EXPOSURE, MEASURED RATHER THAN ASSERTED (free-tier window, 2025-07-01..2026-06-30):

    76,894,449   1-minute option rows in the served slice
       232,172   contract-days x 9 precomputed clock times
     2,089,548   price points reachable AT ALL through this surface -- 2.7 % of the table
       ~1,000    the most a single daily-cadence two-leg backtest can release
        ~13 h    of CPU quota to touch 1 % of the reachable surface, with no overlap

The overlap assumption is the loose one and it runs in our favour: strike selection is
driven by spot, so consecutive runs re-release mostly the same contracts. Reconstructing
the chain this way is slower than re-scraping it from the source, which is the only
security property that ever really held.

TWO CONTROLS, both here rather than in a comment:
  * per-leg prices are withheld below PRICE_DETAIL_MIN_TRADES, so a deliberately narrow
    run cannot be used as a price lookup -- the same floor that withholds ratios
  * every response counts what it released, and the count is metered per account
"""
import statistics

from .config import margin as margin_mod

# Matches metrics.MIN_TRADES_FOR_RATIOS deliberately. Below this a run is too narrow to
# be a strategy and too narrow to audit, which leaves only one thing to want it for.
PRICE_DETAIL_MIN_TRADES = 30

# A year of daily cadence is ~246 trades and the cap is above it on purpose: the common
# case should return everything, so a caller never has to slice a window to see it all --
# which is the behaviour that would defeat the floor above.
MAX_TRADES_RETURNED = 300

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _max_loss(t):
    legs = [{"action": l.get("action"), "option_type": l.get("option_type"),
             "strike": l.get("strike"), "qty": l.get("qty", 1),
             "premium_pts": l.get("entry_price", l.get("premium_pts", 0.0)) or 0.0}
            for l in (t.legs or [])]
    if not legs or not all(l["strike"] and l["action"] and l["option_type"] for l in legs):
        return None
    if not margin_mod.is_defined_risk(legs):
        return None
    return round(margin_mod.max_loss_points(legs), 2)


def build(result, max_trades=MAX_TRADES_RETURNED):
    """-> {equity_curve, breakdown, trades, trade_detail, price_points_released}."""
    trades = sorted(result.trades, key=lambda t: (t.entry_ts, str(t.expiry)))
    if not trades:
        return {"equity_curve": {"columns": list(EQUITY_COLUMNS), "rows": []},
                "breakdown": {}, "trades": [],
                "trade_detail": {"prices_included": False,
                                 "reason": "no trades"},
                "price_points_released": 0}

    with_prices = len(trades) >= PRICE_DETAIL_MIN_TRADES
    shown = trades[:max_trades]
    rows, points = [], 0
    for i, t in enumerate(shown, 1):
        row, n = _trade_row(i, t, with_prices)
        rows.append(row)
        points += n

    return {
        "equity_curve": _equity_curve(trades),
        "breakdown": _breakdown(trades),
        "trades": rows,
        "trade_detail": _detail_note(trades, shown, with_prices),
        "price_points_released": points,
    }


# ------------------------------------------------------------------ per trade

def _trade_row(i, t, with_prices):
    gross = t.entry_credit_pts + t.exit_value_pts
    row = {
        "n": i,
        "entry": t.entry_ts.strftime("%Y-%m-%d %H:%M"),
        "exit": t.exit_ts.strftime("%Y-%m-%d %H:%M"),
        "expiry": str(t.expiry),
        "dte_at_entry": t.dte,
        "exit_reason": t.exit_reason,
        "spot_at_entry": round(t.spot_entry, 2),
        # Points first, rupees second: points are the strategy, rupees are the strategy
        # times a lot size that changed part-way through this window.
        "gross_points": round(gross, 2),
        "slippage_points": round(t.slippage_pts, 2),
        "net_points": round(t.pnl_pts, 2),
        "margin_points": round(t.margin_pts, 2),
        # The ENTRY structure's worst case, or None when a short is uncovered and there
        # isn't one. Risk-based sizing needs this to exist and must refuse when it does
        # not, so the absence has to travel with the trade rather than be re-derived by
        # every consumer from a leg list it may not have been given.
        "max_loss_points": _max_loss(t),
        "return_on_margin": (round(t.pnl_pts / t.margin_pts, 4)
                             if t.margin_pts > 0 else None),
        "lot_size": t.lot_size,
        "charges_rupees": round(t.charges_rupees, 2),
        "pnl_rupees": round(t.pnl_rupees, 2),
        "holding_minutes": int((t.exit_ts - t.entry_ts).total_seconds() // 60),
    }
    # What the RULES did, if any fired. A trade that was rolled twice is not the trade
    # that was opened, and a row that does not say so is describing something else.
    if getattr(t, "adjustments", None):
        row["adjustments"] = list(t.adjustments)
    if not with_prices:
        # Strikes, sides and types WITHOUT prices. A strike is a rule output, not a quote
        # -- the same boundary the replay track already publishes -- and without them a
        # general leg list cannot be drawn at all.
        row["legs"] = [
            dict({"action": l["action"], "type": l["option_type"],
                  "strike": l["strike"], "qty": l.get("qty", 1)},
                 **({"opened_at": l["open_ts"].strftime("%Y-%m-%d %H:%M")}
                    if l.get("open_ts") is not None and l["open_ts"] != t.entry_ts
                    else {}),
                 **({"closed_by": l["close_reason"]} if l.get("close_reason") else {}))
            for l in t.legs]
        return row, 0

    released = 0
    legs = []
    for l in t.legs:
        leg = {"action": l["action"], "type": l["option_type"], "strike": l["strike"],
               "entry_price": round(l["premium_pts"], 2)}
        released += 1                      # a real print, from the served table
        if l.get("qty", 1) != 1:
            leg["qty"] = l["qty"]
        if l.get("open_ts") is not None and l["open_ts"] != t.entry_ts:
            leg["opened_at"] = l["open_ts"].strftime("%Y-%m-%d %H:%M")
        if l.get("close_reason"):
            leg["closed_by"] = l["close_reason"]
        # Per-leg exit price where the engine has one (the general simulator always does,
        # because it walks each leg); the aggregate table otherwise.
        if l.get("exit_price") is not None:
            leg["exit_price"] = round(float(l["exit_price"]), 2)
        elif t.exit_prices is not None:
            leg["exit_price"] = round(
                float(t.exit_prices[(l["option_type"], l["strike"])]), 2)
            # An EXPIRY exit price is max(0, settle - K) computed from the INDEX, not an
            # option quote, so it discloses nothing about the option table and is not
            # counted against the release budget. A TIME exit price is a real print and is.
            if t.exit_reason == "TIME" or l.get("close_reason") in ("ROLL", "ADJUST",
                                                                    "RULE", "TIME"):
                released += 1
        legs.append(leg)
    row["legs"] = legs
    row["entry_net_points"] = round(t.entry_credit_pts, 2)
    row["exit_net_points"] = round(t.exit_value_pts, 2)
    # THE EXIT REASON IS THE AUTHORITATIVE FACT, so it is read first. Checking
    # `exit_prices is None` ahead of it put "closed on a stop or target: there is no
    # per-leg price to report" on rows whose reason was EXPIRY and whose legs carried an
    # exit price of 0.00 -- a note contradicting the two fields either side of it. An
    # expiry is a settlement however the engine sourced the number; "no per-leg price" is
    # only claimable when the legs genuinely have none.
    if t.exit_reason == "EXPIRY":
        row["exit_price_note"] = (
            "settled, not traded: each leg is intrinsic value against NSE's final "
            "settlement price, the mean of the index over the last 30 minutes")
    elif not any(l.get("exit_price") is not None for l in legs):
        row["exit_price_note"] = (
            "closed on a stop or target: the engine resolves the COMBINED position value "
            "at the firing minute in SQL, so there is no per-leg price to report")
    return row, released


def _detail_note(trades, shown, with_prices):
    note = {"prices_included": with_prices,
            "trades_returned": len(shown), "trades_total": len(trades)}
    if not with_prices:
        note["reason"] = (
            f"per-leg prices are withheld below {PRICE_DETAIL_MIN_TRADES} trades. This run "
            f"has {len(trades)}. The same floor withholds Sharpe and profit factor, for "
            f"the same reason: a run this narrow describes a handful of contracts rather "
            f"than a strategy. Widen the period or use cadence 'daily'")
    if len(shown) < len(trades):
        note["truncation"] = (
            f"showing the first {len(shown)} of {len(trades)} trades in date order. "
            f"Every aggregate on this page -- P&L, drawdown, equity curve, breakdowns -- "
            f"covers ALL {len(trades)}; only this table is cut")
    return note


# ------------------------------------------------------------------ curve

EQUITY_COLUMNS = ["date", "pnl_rupees", "equity_rupees", "drawdown_rupees",
                  "margin_rupees", "days_held"]


def _equity_curve(trades):
    """Cumulative net P&L in trade order, with the running drawdown alongside it.

    Ordered by EXIT, not entry. On a weekly cadence positions overlap -- one opens before
    the previous settles -- and ordering an equity curve by entry then reports profit at a
    moment the account had not yet received it. The distinction is invisible on an intraday
    strategy and material on a swing one.

    COLUMNAR, not a list of objects. A year of daily cadence is ~246 points, and repeating
    five key names on every one of them would cost a third of the response -- then doubled,
    because an MCP tool result carries the payload twice for client compatibility. The same
    series as {columns, rows} reads the same to a model, which is worth more here than the
    marginal convenience of self-describing rows.

    WHY THE ENTRY DATE IS DERIVABLE. `days_held` carries it, as a small integer rather
    than a second date string: a consumer that wants the entry subtracts it, and one that
    wants the holding period -- which is most of them -- has it directly. Without it
    nothing downstream can tell "the strategy opened a position that day and it is still
    running" from "the strategy did nothing that day", and those are opposite facts.

    WHY MARGIN IS ON THE CURVE. Per-trade rows are capped and trimmed -- a standard
    response carries 25 of them -- but the curve carries every trade. Without a margin
    figure alongside each point, nothing downstream can size the WHOLE series: a capital
    view could only be computed over the released sample and would silently describe a
    different strategy. It is the one-lot blocked margin, a number this engine computes
    from its own calibration; it is not a price and it discloses nothing about the chain.

    Rupees are rounded to whole units. Paise on a cumulative P&L are noise carried at the
    cost of three characters per number per point.
    """
    rows = []
    equity = peak = 0.0
    for t in sorted(trades, key=lambda x: x.exit_ts):
        equity += t.pnl_rupees
        peak = max(peak, equity)
        rows.append([t.exit_ts.strftime("%Y-%m-%d"), round(t.pnl_rupees),
                     round(equity), round(equity - peak),
                     round(t.margin_pts * t.lot_size),
                     (t.exit_ts.date() - t.entry_ts.date()).days])
    return {"columns": list(EQUITY_COLUMNS), "rows": rows,
            "note": ("one row per trade, in exit order; equity_rupees is cumulative net "
                     "P&L, drawdown_rupees is the gap to the running peak, "
                     "margin_rupees is what ONE lot of that trade blocked, and days_held "
                     "is calendar days from entry to exit (0 = opened and closed the "
                     "same session), so the entry date is date minus days_held")}


# ------------------------------------------------------------------ breakdowns

def _group(trades, key):
    buckets = {}
    for t in trades:
        buckets.setdefault(key(t), []).append(t)
    return {k: _bucket(v) for k, v in sorted(buckets.items(), key=lambda kv: str(kv[0]))}


def _bucket(ts):
    pnl = [t.pnl_rupees for t in ts]
    return {"n_trades": len(ts), "pnl_rupees": round(sum(pnl), 2),
            "win_rate": round(sum(1 for p in pnl if p > 0) / len(ts), 3)}


def _breakdown(trades):
    wins, losses = _streaks(trades)
    holds = [int((t.exit_ts - t.entry_ts).total_seconds() // 60) for t in trades]
    return {
        "by_month": _group(trades, lambda t: t.exit_ts.strftime("%Y-%m")),
        "by_exit_reason": _group(trades, lambda t: t.exit_reason),
        "by_weekday_of_entry": _group(trades, lambda t: WEEKDAYS[t.entry_date.weekday()]),
        "by_dte_at_entry": _group(trades, lambda t: f"{t.dte}DTE"),
        "holding_minutes": {"median": int(statistics.median(holds)),
                            "min": min(holds), "max": max(holds)},
        "longest_win_streak": wins,
        "longest_loss_streak": losses,
        "best_trade_rupees": round(max(t.pnl_rupees for t in trades), 2),
        "worst_trade_rupees": round(min(t.pnl_rupees for t in trades), 2),
    }


def _streaks(trades):
    best_w = best_l = run_w = run_l = 0
    for t in sorted(trades, key=lambda x: x.exit_ts):
        if t.pnl_rupees > 0:
            run_w, run_l = run_w + 1, 0
        else:
            run_l, run_w = run_l + 1, 0
        best_w, best_l = max(best_w, run_w), max(best_l, run_l)
    return best_w, best_l


def rows_for_chart(result):
    """Every trade, WITHOUT per-leg prices, for plotting and position sizing.

    The release cap exists because per-leg PRICES are the asset. A row stripped of them --
    entry and exit timestamps, spot at entry, margin, lot size, net P&L, exit reason --
    discloses no option quote at all, so it is not bounded by the price budget and returns
    zero against the meter.

    This is what a chart marker and a capital curve actually need, and computing them from
    the first 300 trades instead would silently end the equity curve early: a 380-trade
    backtest sized on 300 of them reports five years of a seven-and-a-half-year strategy.
    """
    trades = sorted(result.trades, key=lambda t: (t.entry_ts, str(t.expiry)))
    out = []
    for i, t in enumerate(trades, 1):
        row, released = _trade_row(i, t, with_prices=False)
        assert released == 0, "a price-free row must release nothing"
        out.append(row)
    return out
