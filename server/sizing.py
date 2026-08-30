"""Turning one-lot results into a capital story.

The engine produces ONE-LOT, UNSIZED trades on purpose: a trade is a fact about the market,
and how many of them you take is a decision about you. Sizing is therefore a VIEW over the
same trades rather than a different backtest -- change the capital and nothing is re-run.

WHY THIS MATTERS MORE THAN IT LOOKS. "This strategy made 31 lakh" is meaningless without
saying on what, and the difference between sizing off median margin and sizing off peak
margin is the difference between a business and a margin call. So the model here is
deliberately conservative in one specific way: it sizes each trade against the capital
AVAILABLE AT THAT MOMENT, after every prior loss, never against the starting figure.
"""
import math

DEFAULT_CAPITAL = 1_000_000        # Rs 10 lakh
DEFAULT_DEPLOY = 0.10              # a tenth of capital as margin on any one trade
MIN_LOTS = 1


class SizingError(ValueError):
    """The requested sizing rule cannot be applied to these trades, and says why."""


def apply(trades, capital=DEFAULT_CAPITAL, deploy=DEFAULT_DEPLOY, compounding=True,
          risk_pct=None):
    """Replay the trades at a position size, in order. Returns the sized series + stats.

    TWO WAYS TO SIZE, and they answer different questions.

    By MARGIN (the default): lots = floor(available capital * deploy / margin for one lot).
    "How much of my account am I willing to tie up."

    By RISK (`risk_pct`): lots = floor(available capital * risk_pct / worst case per lot).
    "How much am I willing to LOSE on this trade" -- the way most people actually describe
    their sizing, and a different number entirely, because margin and worst-case loss are
    not proportional to each other across structures.

    RISK SIZING NEEDS A WORST CASE TO EXIST. A short strangle does not have one: its loss
    is unbounded, so "risk 2% per trade" is not a size, it is a wish. Rather than invent a
    proxy -- a stop that may not fire at the price it names, or a historical worst loss,
    which is look-ahead wearing a hat -- this refuses, names the structure, and points at
    margin sizing. Refusing to size something is honest; sizing it off a number that means
    nothing is what produces a backtest that ruins somebody.

    Even where it applies, the worst case is the ENTRY structure's. A strategy that rolls
    can end up in a position wider than the one it opened, so a trade's realised loss can
    exceed the budget it was sized against. Stated in `basis` rather than smoothed over.
    """
    equity, peak, curve = capital, capital, []
    taken = skipped = 0
    max_dd = max_dd_pct = 0.0
    lots_used = []
    if risk_pct is not None:
        _require_bounded_risk(trades, risk_pct)
    for t in trades:
        pnl_1, mp, ls = t.get("pnl_rupees"), t.get("margin_points"), t.get("lot_size")
        if pnl_1 is None or not mp or not ls:
            continue
        margin_per_lot = float(mp) * float(ls)
        base = equity if compounding else capital
        if risk_pct is None:
            allowance = base * deploy
            lots = int(math.floor(allowance / margin_per_lot)) if margin_per_lot > 0 else 0
        else:
            risk_per_lot = float(t["max_loss_points"]) * float(ls)
            lots = (int(math.floor(base * risk_pct / risk_per_lot))
                    if risk_per_lot > 0 else 0)
            # Risk decides the size; the account still has to be able to BLOCK it. Without
            # this a defined-risk position with a tiny worst case sizes to hundreds of
            # lots and quietly assumes margin nobody had.
            if margin_per_lot > 0:
                lots = min(lots, int(math.floor(base / margin_per_lot)))
        if lots < MIN_LOTS:
            skipped += 1
            curve.append({"date": (t.get("exit") or "")[:10], "lots": 0,
                          "pnl": 0.0, "equity": round(equity, 2),
                          "drawdown": round(equity - peak, 2), "skipped": True})
            continue
        pnl = float(pnl_1) * lots
        equity += pnl
        peak = max(peak, equity)
        dd = equity - peak
        max_dd = min(max_dd, dd)
        max_dd_pct = min(max_dd_pct, dd / peak if peak else 0.0)
        taken += 1
        lots_used.append(lots)
        curve.append({"date": (t.get("exit") or "")[:10], "lots": lots,
                      "pnl": round(pnl, 2), "equity": round(equity, 2),
                      "drawdown": round(dd, 2), "margin": round(margin_per_lot * lots, 2)})

    years = _years(trades)
    total = equity - capital
    # A window shorter than a month has no annual growth rate: raising its return to the
    # power of 365/days either means nothing or overflows outright. Reported as absent.
    cagr = (((equity / capital) ** (1 / years) - 1)
            if years >= 30 / 365.25 and equity > 0 else None)
    return {
        "starting_capital": capital,
        "ending_capital": round(equity, 2),
        "net_pnl": round(total, 2),
        "return_pct": round(total / capital * 100, 2) if capital else None,
        "cagr_pct": round(cagr * 100, 2) if cagr is not None else None,
        "years": round(years, 2),
        "deploy_pct": round(deploy * 100, 1) if risk_pct is None else None,
        "risk_pct": round(risk_pct * 100, 2) if risk_pct is not None else None,
        "compounding": compounding,
        "max_drawdown_rupees": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd_pct * 100, 2),
        "trades_taken": taken,
        "trades_skipped_insufficient_capital": skipped,
        "lots_min": min(lots_used) if lots_used else 0,
        "lots_max": max(lots_used) if lots_used else 0,
        "lots_median": sorted(lots_used)[len(lots_used) // 2] if lots_used else 0,
        "curve": curve,
        "basis": (
            (f"Rs {capital:,.0f} starting capital, up to {deploy * 100:.0f}% of "
             f"{'running' if compounding else 'starting'} capital as margin on any one "
             f"trade. Lots are whole numbers, so the size steps rather than scales "
             f"smoothly, and a trade needing more margin than the allowance is skipped "
             f"rather than taken undersized.")
            if risk_pct is None else
            (f"Rs {capital:,.0f} starting capital, risking at most {risk_pct * 100:.2f}% "
             f"of {'running' if compounding else 'starting'} capital per trade against "
             f"the ENTRY structure's maximum loss, capped by the margin the account could "
             f"actually block. A trade that is adjusted can finish wider than it opened, "
             f"so a realised loss may exceed the budget it was sized against.")),
    }


def _years(trades):
    dates = [t.get("entry", "")[:10] for t in trades if t.get("entry")]
    dates += [t.get("exit", "")[:10] for t in trades if t.get("exit")]
    dates = sorted(d for d in dates if d)
    if len(dates) < 2:
        return 0.0
    from datetime import date
    a = date(*map(int, dates[0].split("-")))
    b = date(*map(int, dates[-1].split("-")))
    return max((b - a).days / 365.25, 1e-9)


# The ladder shown in every full report. It spans "barely committed" to "over-levered"
# because the point is the SHAPE, not any one row.
SENSITIVITY = (0.02, 0.05, 0.10, 0.15, 0.25, 0.40)


def sensitivity(trades, capital=DEFAULT_CAPITAL, levels=SENSITIVITY):
    """The same trades at several position sizes.

    This is the most useful table in the report and the one almost no backtester shows. A
    strategy does not have "a return" -- it has a return per unit of leverage, and for a
    premium seller the relationship is not linear but humped: too small and costs dominate,
    too large and a drawdown you would otherwise have ridden out compounds against you,
    because after losing half your capital every subsequent position is half the size.

    On a real seven-year condor these levels run from +2.4% to -85% on identical trades.
    Presenting a single number without this is presenting the author's leverage preference
    as if it were a property of the strategy.
    """
    return [dict(apply(trades, capital=capital, deploy=d), curve=None, deploy=d)
            for d in levels]


def _require_bounded_risk(trades, risk_pct):
    """Risk sizing is only meaningful where the worst case is a number."""
    if not 0 < risk_pct <= 1:
        raise SizingError("risk_pct must be between 0 and 100 per cent")
    priced = [t for t in trades if t.get("pnl_rupees") is not None]
    unbounded = [t for t in priced if not t.get("max_loss_points")]
    if unbounded and len(unbounded) == len(priced):
        raise SizingError(
            "this strategy has no bounded worst case -- every trade carries an uncovered "
            "short, so the most it can lose is not a number and 'risk 2% per trade' has "
            "nothing to size against. Size it by margin instead (deploy_pct), or add a "
            "long leg on each side to define the risk.")
    if unbounded:
        raise SizingError(
            f"{len(unbounded)} of {len(priced)} trades carry an uncovered short, so they "
            f"have no worst case to risk a fixed fraction of. Size by margin "
            f"(deploy_pct), or define the risk on every cycle.")
