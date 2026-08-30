"""The honesty panel: what the evidence supports, and where it stops.

Every number here exists because a real backtesting tool got it wrong. The rules are
DECISIONS.md section 5, and they are enforced in code rather than left to a reviewer:

  * No ratio below 30 trades (metrics.py enforces this; the panel inherits it).
  * Out-of-sample is a CHRONOLOGICAL 70/30 split. Never random -- a random split over
    overlapping option cycles leaks the future into the past.
  * Walk-forward is 3 folds, each trained on everything before it.
  * Multiple comparisons are counted over a rolling 24 hours and deflated for. A search
    over 200 parameter combinations will throw up a beautiful Sharpe by construction;
    the deflated Sharpe is what says whether it survives having been searched for.
  * The health rubric is PUBLISHED. An opaque score reads as marketing.

Nothing here says a strategy is good. It reports evidence and the limits of that evidence.
"""
import math
import os
import sqlite3
import statistics
from pathlib import Path

import numpy as np

from . import metrics

EULER_MASCHERONI = 0.5772156649015329
BOOTSTRAP_SAMPLES = 2000
BOOTSTRAP_SEED = 20260101       # fixed: the same backtest must give the same interval
OOS_FRACTION = 0.30
WALK_FORWARD_FOLDS = 3
VARIANT_WINDOW_HOURS = 24

VARIANT_LOG = Path(os.getenv(
    "STRATIFY_VARIANT_LOG",
    Path(__file__).resolve().parent / "state" / "variants.sqlite"))

HEALTH_RUBRIC = [
    ("sample_size",        25, "30+ trades scores full; below 30 scores zero and no "
                               "ratios are reported at all"),
    ("out_of_sample",      25, "the held-out 30 % must stay profitable and keep at least "
                               "half the in-sample return-on-margin"),
    ("walk_forward",       20, "one point per profitable fold, of 3"),
    ("cost_sensitivity",   15, "how much of the gross edge survives charges and slippage"),
    ("multiple_testing",   15, "deflated Sharpe against the variants tried in the last "
                               "24 hours"),
]


# ---------------------------------------------------------------- variant log

def _connect():
    VARIANT_LOG.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(VARIANT_LOG)
    con.execute("""CREATE TABLE IF NOT EXISTS variants (
        ts REAL NOT NULL, api_key_id TEXT NOT NULL, family TEXT NOT NULL,
        spec_hash TEXT NOT NULL, sharpe REAL)""")
    con.execute("CREATE INDEX IF NOT EXISTS ix_variants ON variants(api_key_id, family, ts)")
    return con


def record_variant(api_key_id, family, spec_hash, sharpe, now):
    con = _connect()
    with con:
        con.execute("INSERT INTO variants VALUES (?,?,?,?,?)",
                    (now, api_key_id, family, spec_hash, sharpe))
    con.close()


def recent_variants(api_key_id, family, now):
    """(n_distinct_variants, [sharpes]) inside the rolling window.

    Counted per (key, structure) rather than globally: penalising a user for someone
    else's search would be arbitrary, and counting across unrelated structures would
    penalise breadth rather than the thing that actually inflates a result -- trying many
    variants of the SAME idea.
    """
    con = _connect()
    rows = con.execute(
        "SELECT spec_hash, sharpe FROM variants WHERE api_key_id=? AND family=? AND ts>=?",
        (api_key_id, family, now - VARIANT_WINDOW_HOURS * 3600)).fetchall()
    con.close()
    # Deduplicated by spec: re-running the same backtest is one variant, and must not
    # widen the spread of trial Sharpes either.
    by_spec = {}
    for spec_hash, sharpe in rows:
        by_spec[spec_hash] = sharpe
    return len(by_spec), [v for v in by_spec.values() if v is not None]


# ---------------------------------------------------------------- statistics

def _phi(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _phi_inv(p):
    """Inverse normal CDF, Acklam's rational approximation. Accurate to ~1e-9, which is
    far beyond what a Sharpe threshold needs."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0,1)")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q, r = p - 0.5, (p - 0.5) ** 2
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def _skew_kurt(xs):
    n = len(xs)
    if n < 4:
        return 0.0, 3.0
    m = statistics.mean(xs)
    sd = statistics.pstdev(xs)
    if sd == 0:
        return 0.0, 3.0
    skew = sum((x - m) ** 3 for x in xs) / (n * sd ** 3)
    kurt = sum((x - m) ** 4 for x in xs) / (n * sd ** 4)
    return skew, kurt


def deflated_sharpe(returns, observed_sharpe, n_trials, trial_sharpes=None):
    """Bailey & Lopez de Prado's Deflated Sharpe Ratio: the probability the observed
    Sharpe is real once you account for having searched `n_trials` times.

    Returns (probability, expected_max_sharpe_under_null, basis). A single trial is not
    exempt -- one backtest is still one draw -- but the deflation is negligible there,
    which is the honest answer.
    """
    n = len(returns)
    if n < 4 or n_trials < 1:
        return None, None, "not enough observations to deflate"

    if trial_sharpes and len(trial_sharpes) > 1:
        var_sr = statistics.variance(trial_sharpes)
        basis = f"variance of {len(trial_sharpes)} logged trial Sharpes"
    else:
        # Without a spread of observed trials, fall back to the sampling variance of a
        # single Sharpe. Say so, rather than letting an assumption pass as a measurement.
        var_sr = 1.0 / max(1, n - 1)
        basis = "sampling variance (too few logged trials to measure the spread)"

    if n_trials == 1:
        sr0 = 0.0
    else:
        e1 = _phi_inv(1.0 - 1.0 / n_trials)
        e2 = _phi_inv(1.0 - 1.0 / (n_trials * math.e))
        sr0 = math.sqrt(var_sr) * ((1 - EULER_MASCHERONI) * e1 + EULER_MASCHERONI * e2)

    skew, kurt = _skew_kurt(returns)
    denom = 1.0 - skew * observed_sharpe + (kurt - 1.0) / 4.0 * observed_sharpe ** 2
    if denom <= 0:
        return None, round(sr0, 4), "return distribution too skewed to deflate reliably"
    z = (observed_sharpe - sr0) * math.sqrt(n - 1) / math.sqrt(denom)
    return round(_phi(z), 4), round(sr0, 4), basis


def bootstrap_ci(values, confidence=0.95):
    """Percentile bootstrap on the mean. Seeded, so the same backtest always returns the
    same interval -- a confidence interval that moves between identical runs is noise
    presented as rigour.

    Vectorised deliberately. The loop version called random.randrange 2000 x n times and
    was, on its own, about 40 % of the CPU cost of an entire request -- more than the
    backtest it was describing. Same estimator, same seed, one matrix.
    """
    n = len(values)
    if n < 2:
        return None
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    arr = np.asarray(values, dtype=np.float64)
    idx = rng.integers(0, n, size=(BOOTSTRAP_SAMPLES, n))
    means = np.sort(arr[idx].mean(axis=1))
    lo = means[int((1 - confidence) / 2 * BOOTSTRAP_SAMPLES)]
    hi = means[int((1 + confidence) / 2 * BOOTSTRAP_SAMPLES) - 1]
    return [round(float(lo), 5), round(float(hi), 5)]


# ---------------------------------------------------------------- panel

def _roms(trades):
    return [t.pnl_pts / t.margin_pts for t in trades if t.margin_pts > 0]


def _chronological_split(trades):
    ordered = sorted(trades, key=lambda t: (t.entry_date, t.expiry))
    cut = int(len(ordered) * (1 - OOS_FRACTION))
    return ordered[:cut], ordered[cut:]


def _walk_forward(trades, folds=WALK_FORWARD_FOLDS):
    ordered = sorted(trades, key=lambda t: (t.entry_date, t.expiry))
    if len(ordered) < folds * 2:
        return None
    size = len(ordered) // folds
    out = []
    for i in range(folds):
        start = i * size
        end = len(ordered) if i == folds - 1 else (i + 1) * size
        chunk = ordered[start:end]
        out.append({
            "fold": i + 1,
            "from": str(chunk[0].entry_date), "to": str(chunk[-1].entry_date),
            "n_trades": len(chunk),
            "pnl_rupees": round(sum(t.pnl_rupees for t in chunk), 2),
            "profitable": sum(t.pnl_rupees for t in chunk) > 0,
        })
    return out


def panel(result, summary, api_key_id="anonymous", now=None, log_variant=True):
    """The full evidence panel for one backtest."""
    import hashlib
    import time as _time
    now = now if now is not None else _time.time()
    trades = result.trades
    spec = result.spec
    out = {"rubric": [{"component": c, "max_points": p, "rule": r}
                      for c, p, r in HEALTH_RUBRIC]}

    if len(trades) < metrics.MIN_TRADES_FOR_RATIOS:
        out["verdict"] = "insufficient_evidence"
        out["health_score"] = 0
        out["explanation"] = (
            f"{len(trades)} trades is not enough to say anything. No ratio is reported "
            f"below {metrics.MIN_TRADES_FOR_RATIOS}, and no amount of favourable P&L "
            f"changes that.")
        return out

    roms = _roms(trades)
    ins, oos = _chronological_split(trades)
    ins_rom = statistics.mean(_roms(ins)) if _roms(ins) else 0.0
    oos_rom = statistics.mean(_roms(oos)) if _roms(oos) else 0.0
    oos_pnl = sum(t.pnl_rupees for t in oos)

    out["out_of_sample"] = {
        "method": "chronological 70/30 — never random, which would leak across cycles",
        "in_sample": {"n_trades": len(ins), "mean_return_on_margin": round(ins_rom, 5),
                      "pnl_rupees": round(sum(t.pnl_rupees for t in ins), 2)},
        "out_of_sample": {"n_trades": len(oos), "mean_return_on_margin": round(oos_rom, 5),
                          "pnl_rupees": round(oos_pnl, 2)},
        "held_up": bool(oos_pnl > 0 and oos_rom >= 0.5 * ins_rom),
    }
    if len(oos) < metrics.MIN_TRADES_FOR_RATIOS:
        out["out_of_sample"]["caveat"] = (
            f"the held-out slice is only {len(oos)} trades, so it is directional evidence "
            f"rather than a test")

    out["walk_forward"] = _walk_forward(trades)
    out["bootstrap_ci_95_return_on_margin"] = bootstrap_ci(roms)

    gross = sum(t.pnl_pts + t.slippage_pts for t in trades)
    net_pts = sum(t.pnl_pts for t in trades)
    charges_pts = sum(t.charges_rupees / t.lot_size for t in trades)
    out["cost_drag"] = {
        "gross_points_before_costs": round(gross, 2),
        "slippage_points": round(sum(t.slippage_pts for t in trades), 2),
        "charges_points": round(charges_pts, 2),
        "net_points_after_costs": round(net_pts - charges_pts, 2),
        "share_of_edge_surviving": (round((net_pts - charges_pts) / gross, 4)
                                    if gross > 0 else None),
    }

    family = f"{spec.structure}"
    spec_hash = hashlib.sha256(
        repr((spec.structure, sorted(spec.params.items()), spec.entry_time,
              spec.gate, spec.bias, spec.date_from, spec.date_to)).encode()).hexdigest()[:16]
    n_variants, trial_sharpes = recent_variants(api_key_id, family, now)
    observed = (summary.get("ratios") or {}).get("sharpe")
    if log_variant:
        record_variant(api_key_id, family, spec_hash, observed, now)
        # Re-read rather than incrementing: re-running an identical spec is not a new
        # variant, and must not deflate the result as though the user had searched wider.
        n_variants, trial_sharpes = recent_variants(api_key_id, family, now)

    dsr, sr0, basis = (None, None, "no Sharpe to deflate")
    if observed is not None:
        dsr, sr0, basis = deflated_sharpe(roms, observed, max(1, n_variants), trial_sharpes)
    out["multiple_comparisons"] = {
        "variants_tested_last_24h": n_variants,
        "scope": f"this API key, structure {family!r}",
        "observed_sharpe": observed,
        "expected_max_sharpe_under_null": sr0,
        "deflated_sharpe_probability": dsr,
        "basis": basis,
        "reading": _dsr_reading(dsr, n_variants),
    }

    out["health_score"], out["score_breakdown"] = _score(out, len(trades))
    out["verdict"] = _verdict(out)
    out["explanation"] = (
        "This is evidence and its limits, not a recommendation. A high score means the "
        "result survived the checks applied here; it does not mean the strategy will "
        "make money.")
    return out


def _dsr_reading(dsr, n_variants):
    if dsr is None:
        return "not computed"
    if n_variants <= 1:
        return (f"{dsr:.0%} probability the Sharpe is real. Only one variant has been "
                f"tried, so almost nothing is deflated away — this is close to the raw "
                f"number, not a stronger claim than it")
    if dsr >= 0.95:
        return f"{dsr:.0%} — survives having been searched for across {n_variants} variants"
    if dsr >= 0.5:
        return f"{dsr:.0%} — weakened by the {n_variants} variants tried; treat with care"
    return (f"{dsr:.0%} — a result this good is what {n_variants} variants of the same "
            f"idea would be expected to produce by chance alone")


def _score(panel_out, n_trades):
    parts = {}
    parts["sample_size"] = 25 if n_trades >= metrics.MIN_TRADES_FOR_RATIOS else 0
    oos = panel_out.get("out_of_sample") or {}
    parts["out_of_sample"] = 25 if oos.get("held_up") else 0
    wf = panel_out.get("walk_forward") or []
    parts["walk_forward"] = int(round(20 * sum(1 for f in wf if f["profitable"]) / len(wf))) if wf else 0
    share = (panel_out.get("cost_drag") or {}).get("share_of_edge_surviving")
    parts["cost_sensitivity"] = int(round(15 * max(0.0, min(1.0, share)))) if share else 0
    dsr = (panel_out.get("multiple_comparisons") or {}).get("deflated_sharpe_probability")
    parts["multiple_testing"] = int(round(15 * dsr)) if dsr else 0
    return sum(parts.values()), parts


def _verdict(panel_out):
    """Deliberately never 'good'. These describe the evidence, not the strategy."""
    score = panel_out["health_score"]
    if score >= 75:
        return "survived_every_check_applied"
    if score >= 50:
        return "mixed_evidence"
    if score >= 25:
        return "weak_evidence"
    return "does_not_survive_scrutiny"
