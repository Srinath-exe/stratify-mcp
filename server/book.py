"""The strategy book: which results are worth keeping, and how they are ranked.

WHY THIS IS A SEPARATE MODULE. "Good" is a judgement, not a computation. Putting it in
store.py would bury it next to SQL, and putting it in tools.py would scatter it across a
handler. It lives here so it can be read, argued with and changed in one place.

THE BAR IS NOT "MADE MONEY". A backtest that made money is the single weakest evidence
this service produces, because it is the thing every parameter sweep maximises by
construction. The bar below is deliberately about whether the result held up OUTSIDE the
data it was chosen on:

  * at least 30 trades           -- the floor below which no ratio is reported at all
  * positive net P&L             -- necessary, and nowhere near sufficient
  * out-of-sample held up        -- the chronological final 30 % stayed profitable
  * majority of folds profitable -- it worked in more than one regime
  * health score >= 50           -- the panel's own composite

RANKED BY CONSISTENCY, NOT SIZE. The default ordering is worst walk-forward fold first,
then median fold. A strategy that made a little in all three folds is a better candidate
than one that made a lot in one and lost in the other two, and every P&L-first ranking puts
the second on top. Sorting by P&L is available; it is not the default.

WHAT THIS IS NOT. An entry in the book is a hypothesis that survived more checks than the
others, on one year of one index. It is not a recommendation, and nothing here decides
what anybody should trade.
"""
import json
import statistics
from engine import methodology

MIN_TRADES = 30
MIN_HEALTH = 50


def evaluate(summary, panel):
    """-> (qualifies: bool, checks: dict). The checks are recorded either way, so a caller
    can see WHICH bar a near-miss failed rather than being told only that it failed."""
    n = summary.get("n_trades") or 0
    pnl = summary.get("total_pnl_rupees") or 0.0
    health = (panel or {}).get("health_score") or 0
    oos = ((panel or {}).get("out_of_sample") or {}).get("held_up")
    folds = [f for f in ((panel or {}).get("walk_forward") or []) if isinstance(f, dict)]
    n_folds = len(folds)
    n_good = sum(1 for f in folds if f.get("profitable"))

    checks = {
        "enough_trades": {"pass": n >= MIN_TRADES,
                          "detail": f"{n} trades, floor is {MIN_TRADES}"},
        "profitable": {"pass": pnl > 0, "detail": f"net {pnl:,.0f} rupees"},
        "out_of_sample_held_up": {
            "pass": bool(oos),
            "detail": ("the chronological final 30 % stayed profitable" if oos
                       else "the held-out final 30 % did not hold up")},
        "majority_of_folds_profitable": {
            "pass": n_folds > 0 and n_good * 2 > n_folds,
            "detail": f"{n_good} of {n_folds} walk-forward folds profitable"},
        "health_score": {"pass": health >= MIN_HEALTH,
                         "detail": f"{health} / 100, floor is {MIN_HEALTH}"},
    }
    return all(c["pass"] for c in checks.values()), checks


def fold_stats(panel):
    """Worst and median fold P&L -- the two numbers the book is ranked on. A strategy with
    no folds gets None rather than 0, because "no evidence" and "broke even" are different
    and sorting them together is how the first quietly becomes the second."""
    folds = [f.get("pnl_rupees") for f in ((panel or {}).get("walk_forward") or [])
             if isinstance(f, dict) and isinstance(f.get("pnl_rupees"), (int, float))]
    if not folds:
        return None, None
    return round(min(folds), 2), round(statistics.median(folds), 2)


def entry_for(account_id, backtest_id, spec_hash, raw_spec, summary, panel, checks):
    """A book row, flattened from the result. Only summary statistics are stored -- no
    trades and no prices. The full result already lives in `results` and is reachable by
    backtest_id, so duplicating it here would grow the book by ~300 KB an entry to hold a
    second copy of something that has its own retention rules."""
    worst, median = fold_stats(panel)
    folds = [f for f in ((panel or {}).get("walk_forward") or []) if isinstance(f, dict)]
    ratios = summary.get("ratios") or {}
    mc = (panel or {}).get("multiple_comparisons") or {}
    return {
        "account_id": account_id,
        "backtest_id": backtest_id,
        "spec_hash": spec_hash,
        # Already inside spec_hash, so it can never merge two methodologies into one row.
        # Stored separately so a row can be shown and filtered without parsing the spec.
        "methodology_version": methodology.VERSION,
        "structure": summary.get("structure") or raw_spec.get("structure") or "",
        "cadence": raw_spec.get("cadence") or "weekly",
        "spec_json": json.dumps(raw_spec, default=str),
        "n_trades": summary.get("n_trades"),
        "pnl_rupees": summary.get("total_pnl_rupees"),
        "mean_rom": summary.get("mean_return_on_margin"),
        "sharpe": ratios.get("sharpe"),
        "profit_factor": ratios.get("profit_factor"),
        "max_drawdown_rupees": summary.get("max_drawdown_rupees"),
        "peak_margin_points": summary.get("peak_margin_points"),
        "health_score": (panel or {}).get("health_score"),
        "verdict": (panel or {}).get("verdict"),
        "deflated_sharpe": mc.get("deflated_sharpe_probability"),
        "oos_held_up": int(bool(((panel or {}).get("out_of_sample") or {}).get("held_up"))),
        "folds_profitable": sum(1 for f in folds if f.get("profitable")),
        "folds_total": len(folds),
        "worst_fold_rupees": worst,
        "median_fold_rupees": median,
        "qualified_json": json.dumps(checks),
    }


BAR_DESCRIPTION = {
    "what_qualifies": [
        f"at least {MIN_TRADES} trades — the floor below which no ratio is reported",
        "positive net P&L after real charges and slippage",
        "the held-out final 30 % stayed profitable",
        "a majority of walk-forward folds were profitable",
        f"health score at least {MIN_HEALTH} / 100",
    ],
    "why_not_just_pnl": (
        "P&L is what a parameter sweep maximises by construction, so it is the weakest "
        "evidence this service produces. Every other check asks whether the result held "
        "up outside the data it was chosen on."),
    "default_ranking": (
        "worst walk-forward fold first, then median fold — consistency, not size. A "
        "strategy that made a little in every fold is a better candidate than one that "
        "made a lot in one and lost in the others."),
    "what_an_entry_is_not": (
        "a recommendation. It is a hypothesis that survived more checks than the others, "
        "on one index over the served window."),
}
