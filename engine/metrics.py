"""Summary statistics, with the honesty floors built in rather than bolted on.

Two rules from DECISIONS.md §5 are enforced here because enforcing them anywhere else
means someone eventually reads a ratio that should not exist:

  C1. No Sharpe, Calmar or profit factor is emitted below 30 trades. Not a small one --
      none at all. A competitor's MCP printed Sharpe on a 2-trade sample; this is the fix.
  C2. A Sharpe above 4 is flagged as a probable computation or sample artefact rather
      than reported as a finding. Values of 64.91 and 13.59 were observed in the wild.

Returns are computed on MARGIN, not on notional or on an assumed account size. For an
options seller, return-on-margin is the number that decides whether a strategy is worth
trading, and it is the one most retail tools omit.
"""
import math
import statistics

MIN_TRADES_FOR_RATIOS = 30
SHARPE_SANITY_CEILING = 4.0
TRADING_DAYS_PER_YEAR = 246   # measured in this window, not assumed to be 252


def summarise(result):
    trades = result.trades
    n = len(trades)
    out = {
        "n_trades": n,
        "structure": result.spec.structure,
        "period": {"from": str(result.spec.date_from), "to": str(result.spec.date_to)},
        "n_distinct_contracts": result.n_contracts,
        "n_trading_days": result.n_trading_days,
        "warnings": list(result.warnings),
        "notes": list(result.notes),
    }
    if n == 0:
        out["status"] = "no_trades"
        return out

    pnl_pts = [t.pnl_pts for t in trades]
    pnl_rs = [t.pnl_rupees for t in trades]
    roms = [t.pnl_pts / t.margin_pts for t in trades if t.margin_pts > 0]
    wins = [p for p in pnl_rs if p > 0]
    losses = [p for p in pnl_rs if p <= 0]

    out.update({
        "total_pnl_rupees": round(sum(pnl_rs), 2),
        "total_pnl_points": round(sum(pnl_pts), 2),
        "total_charges_rupees": round(sum(t.charges_rupees for t in trades), 2),
        "total_slippage_points": round(sum(t.slippage_pts for t in trades), 2),
        "win_rate": round(len(wins) / n, 4),
        "avg_win_rupees": round(statistics.mean(wins), 2) if wins else 0.0,
        "avg_loss_rupees": round(statistics.mean(losses), 2) if losses else 0.0,
        "mean_return_on_margin": round(statistics.mean(roms), 5) if roms else None,
        # ORDERED BY EXIT, matching the equity curve in detail.py. It was ordered by
        # whatever order the engine happened to produce trades in -- entry order -- and on
        # a weekly cadence positions OVERLAP, so the two orderings gave two different
        # drawdowns for the same book. Measured gap on one credit-spread run: the headline
        # said 48,677 and the chart said 48,147. A user who plots the curve and reads the
        # summary must not see two different worst cases.
        "max_drawdown_rupees": round(_max_drawdown(
            [t.pnl_rupees for t in sorted(trades, key=lambda x: x.exit_ts)]), 2),
        "exit_reasons": _counts(t.exit_reason for t in trades),
        "avg_margin_points": round(statistics.mean(t.margin_pts for t in trades), 2),
        # PEAK margin is the capital the strategy actually had to have available. The mean
        # is what it used on a typical day and is the wrong number to size an account from.
        "peak_margin_points": round(max(t.margin_pts for t in trades), 2),
        "margin_basis": _margin_basis(trades),
        "charges_share_of_gross": _charges_share(trades),
    })

    if n < MIN_TRADES_FOR_RATIOS:
        out["ratios"] = None
        out["ratios_withheld"] = (
            f"insufficient_sample: {n} trades. No Sharpe, Calmar or profit factor is "
            f"reported below {MIN_TRADES_FOR_RATIOS} — at this sample size a ratio "
            f"describes the sample, not the strategy.")
        return out

    span_days = (max(t.exit_ts.date() for t in trades)
                 - min(t.entry_date for t in trades)).days
    out["ratios"] = _ratios(roms, pnl_rs, out["max_drawdown_rupees"],
                            out["total_pnl_rupees"], span_days)
    if out["ratios"]["sharpe"] is not None and out["ratios"]["sharpe"] > SHARPE_SANITY_CEILING:
        out["warnings"].append(
            f"Sharpe of {out['ratios']['sharpe']} is above the sanity ceiling of "
            f"{SHARPE_SANITY_CEILING}. Treat it as a probable computation or sample "
            f"artefact, not a finding.")
    return out


def _margin_basis(trades):
    """Every distinct margin basis in play, so return-on-margin never ships without saying
    what its denominator is. The note was computed per trade and then discarded, which left
    a naked strangle's optimistic ROM indistinguishable from a condor's exact one."""
    return sorted({t.margin_basis for t in trades if t.margin_basis})


def _charges_share(trades):
    """What fraction of the gross edge the charges took. Reported unconditionally because a
    strategy can be gross-profitable and net-losing, and that is a different finding from
    'no edge' -- it points at trading frequency rather than at the rule."""
    gross = sum(t.pnl_pts + t.slippage_pts for t in trades)
    charges_pts = sum(t.charges_rupees / t.lot_size for t in trades)
    out = {"gross_points": round(gross, 2), "charges_points": round(charges_pts, 2)}
    if gross <= 0:
        out["share"] = None
        out["note"] = "gross edge was not positive, so a share of it is not meaningful"
    else:
        out["share"] = round(charges_pts / gross, 4)
    return out


def _ratios(roms, pnl_rs, max_dd, total_pnl, span_days):
    gross_win = sum(p for p in pnl_rs if p > 0)
    gross_loss = -sum(p for p in pnl_rs if p < 0)
    sharpe = None
    if len(roms) > 1:
        sd = statistics.stdev(roms)
        if sd > 0:
            # Per-trade Sharpe annualised by the OBSERVED trade frequency -- trades
            # divided by the calendar span they actually cover -- not by an assumed 252
            # and not by a constant. Weekly cycles land near 52; a strategy that trades
            # twice a week must not inherit a weekly strategy's annualisation factor.
            per_trade = statistics.mean(roms) / sd
            trades_per_year = len(roms) * 365.25 / max(1.0, span_days)
            sharpe = round(per_trade * math.sqrt(trades_per_year), 3)
    return {
        "sharpe": sharpe,
        "sharpe_basis": (f"per-trade return-on-margin, annualised by observed frequency "
                         f"({len(roms)} trades over {span_days} days)"),
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
        "calmar": round(total_pnl / abs(max_dd), 3) if max_dd < 0 else None,
    }


def _max_drawdown(pnl_series):
    """Worst peak-to-trough on the running total. The SERIES MUST BE IN EXIT ORDER --
    equity arrives when a position closes, not when it opens, and ordering by entry credits
    profit to a moment the account had not received it."""
    peak = 0.0
    equity = 0.0
    worst = 0.0
    for p in pnl_series:
        equity += p
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def _counts(values):
    out = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out
