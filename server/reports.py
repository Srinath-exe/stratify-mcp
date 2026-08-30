"""Rendered report page.

Server-side HTML with no external requests: the page is one self-contained file, so it
loads behind a corporate proxy, prints, and survives being saved. It is theme-aware and
readable on a phone, because the most common way anyone will open one of these is a link
someone pasted into a chat.

The page shows the honesty panel FIRST and the P&L second. That ordering is the product:
a backtest that leads with a number invites the reader to stop there.
"""
import html
import json

CSS = """
:root{--bg:#fbfaf9;--fg:#1a1a19;--mut:#6b6b68;--line:#e5e2dd;--card:#fff;
--good:#1f7a4d;--bad:#a3352c;--warn:#8a6a1f;--accent:#2f5d8a}
@media(prefers-color-scheme:dark){:root{--bg:#151514;--fg:#e9e7e3;--mut:#9b9994;
--line:#2e2d2a;--card:#1d1d1b;--good:#4caf7d;--bad:#e0796d;--warn:#d0a94a;--accent:#7fb0dd}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:880px;margin:0 auto;padding:32px 20px 80px}
h1{font-size:22px;margin:0 0 4px} h2{font-size:15px;margin:32px 0 10px;
text-transform:uppercase;letter-spacing:.07em;color:var(--mut)}
.sub{color:var(--mut);font-size:13px;margin-bottom:24px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:16px 18px;margin-bottom:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.stat .k{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut)}
.stat .v{font-size:20px;font-variant-numeric:tabular-nums;margin-top:2px}
.good{color:var(--good)} .bad{color:var(--bad)} .warn{color:var(--warn)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);
font-variant-numeric:tabular-nums} th{color:var(--mut);font-weight:500}
td.n,th.n{text-align:right}
.scroll{overflow-x:auto}
.bar{height:6px;background:var(--line);border-radius:3px;overflow:hidden;margin-top:6px}
.bar i{display:block;height:100%;background:var(--accent)}
ul{margin:6px 0 0;padding-left:18px} li{margin:4px 0;color:var(--mut);font-size:13px}
.verdict{font-size:17px;font-weight:600;margin-bottom:2px}
code{background:var(--line);padding:1px 5px;border-radius:4px;font-size:12px}
.foot{color:var(--mut);font-size:12px;margin-top:40px;border-top:1px solid var(--line);
padding-top:14px}
svg{display:block;width:100%;height:auto}
.chart{overflow:hidden;border-radius:6px}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--mut);margin-top:8px}
.legend i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px;
vertical-align:baseline}
.tt{max-height:520px;overflow:auto}
.tt table{font-size:12px} .tt th{position:sticky;top:0;background:var(--card)}
"""


def _e(v):
    return html.escape(str(v))


def _money(v):
    return f"{'−' if v < 0 else ''}₹{abs(v):,.0f}"


def _duration(minutes):
    if minutes < 60:
        return f"{minutes}m"
    if minutes < 60 * 24:
        return f"{minutes // 60}h {minutes % 60:02d}m"
    return f"{minutes // (60 * 24)}d"


def _stat(k, v, cls=""):
    return f'<div class="stat"><div class="k">{_e(k)}</div><div class="v {cls}">{v}</div></div>'


# ------------------------------------------------------------------ charts
#
# Inline SVG, computed server-side. No script and no external request, because the page
# has to survive being saved, printed, and opened behind a proxy that blocks everything --
# and because a chart library would be the one thing on the page able to phone home.

def _path_points(values, w, h, pad):
    """Values -> screen coordinates, with the y axis always including zero. A curve that
    is scaled to its own min and max hides whether it ever went negative, which on an
    equity curve is the single thing a reader is looking for."""
    lo, hi = min(min(values), 0.0), max(max(values), 0.0)
    span = (hi - lo) or 1.0
    step = (w - 2 * pad) / max(1, len(values) - 1)
    pts = [(pad + i * step, pad + (hi - v) / span * (h - 2 * pad))
           for i, v in enumerate(values)]
    zero_y = pad + hi / span * (h - 2 * pad)
    return pts, zero_y, lo, hi


def _equity_chart(curve):
    if len(curve) < 2:
        return ""
    w, h, pad = 800.0, 230.0, 18.0
    # Columnar rows: [date, pnl_rupees, equity_rupees, drawdown_rupees].
    equity = [r[2] for r in curve]
    pts, zero_y, lo, hi = _path_points(equity, w, h, pad)
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = (f"{pts[0][0]:.1f},{zero_y:.1f} " + line +
            f" {pts[-1][0]:.1f},{zero_y:.1f}")
    up = equity[-1] >= 0
    col = "var(--good)" if up else "var(--bad)"
    # Peak line, so the drawdown on the chart is the same quantity the panel reports.
    peak, peaks = -1e30, []
    for v in equity:
        peak = max(peak, v)
        peaks.append(peak)
    ppts, _, _, _ = _path_points_with(peaks, equity, w, h, pad)
    peak_line = " ".join(f"{x:.1f},{y:.1f}" for x, y in ppts)
    return (
        f'<div class="chart"><svg viewBox="0 0 {w:.0f} {h:.0f}" role="img" '
        f'aria-label="Cumulative profit and loss across {len(curve)} trades">'
        f'<polygon points="{area}" fill="{col}" opacity="0.11"/>'
        f'<line x1="{pad}" y1="{zero_y:.1f}" x2="{w - pad}" y2="{zero_y:.1f}" '
        f'stroke="var(--line)" stroke-width="1"/>'
        f'<polyline points="{peak_line}" fill="none" stroke="var(--mut)" '
        f'stroke-width="1" stroke-dasharray="3 3" opacity="0.55"/>'
        f'<polyline points="{line}" fill="none" stroke="{col}" stroke-width="1.8" '
        f'stroke-linejoin="round"/></svg></div>'
        f'<div class="legend"><span><i style="background:{col}"></i>cumulative net P&amp;L'
        f'</span><span><i style="background:var(--mut)"></i>running peak — the gap is the '
        f'drawdown</span><span>{_e(curve[0][0])} → {_e(curve[-1][0])} · '
        f'{len(curve)} trades · peak {_money(max(equity))} · '
        f'trough {_money(min(equity))}</span></div>')


def _path_points_with(values, scale_from, w, h, pad):
    """Plot `values` on the axis computed for `scale_from`, so two series share one scale."""
    lo, hi = min(min(scale_from), 0.0), max(max(scale_from), 0.0)
    span = (hi - lo) or 1.0
    step = (w - 2 * pad) / max(1, len(values) - 1)
    return ([(pad + i * step, pad + (hi - v) / span * (h - 2 * pad))
             for i, v in enumerate(values)], None, lo, hi)


def _monthly_chart(by_month):
    if not by_month:
        return ""
    months = list(by_month)
    vals = [by_month[m]["pnl_rupees"] for m in months]
    w, h, pad = 800.0, 150.0, 18.0
    lo, hi = min(min(vals), 0.0), max(max(vals), 0.0)
    span = (hi - lo) or 1.0
    zero_y = pad + hi / span * (h - 2 * pad)
    slot = (w - 2 * pad) / len(vals)
    bw = max(3.0, slot * 0.62)
    bars = []
    for i, v in enumerate(vals):
        x = pad + i * slot + (slot - bw) / 2
        y = pad + (hi - max(v, 0.0)) / span * (h - 2 * pad)
        height = abs(v) / span * (h - 2 * pad)
        col = "var(--good)" if v > 0 else "var(--bad)"
        bars.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" '
                    f'height="{max(height, 0.7):.1f}" fill="{col}" rx="1.5"><title>'
                    f'{_e(months[i])}: {_money(v)}</title></rect>')
    labels = []
    every = max(1, len(months) // 12)
    for i, m in enumerate(months):
        if i % every:
            continue
        labels.append(f'<text x="{pad + i * slot + slot / 2:.1f}" y="{h - 3:.1f}" '
                      f'text-anchor="middle" font-size="9" fill="var(--mut)">'
                      f'{_e(m[2:])}</text>')
    return (f'<div class="chart"><svg viewBox="0 0 {w:.0f} {h:.0f}" role="img" '
            f'aria-label="Net profit and loss by month">'
            f'<line x1="{pad}" y1="{zero_y:.1f}" x2="{w - pad}" y2="{zero_y:.1f}" '
            f'stroke="var(--line)"/>{"".join(bars)}{"".join(labels)}</svg></div>')


def _trades_table(trades):
    if not trades:
        return ""
    has_legs = any("legs" in t for t in trades)
    head = ('<tr><th class="n">#</th><th>Entry</th><th>Exit</th><th>Exit on</th>'
            + ('<th>Legs</th>' if has_legs else '')
            + '<th class="n">Net pts</th><th class="n">P&amp;L</th></tr>')
    rows = []
    for t in trades:
        cls = "good" if t["pnl_rupees"] > 0 else "bad"
        legs = ""
        if has_legs:
            bits = []
            for l in t.get("legs", []):
                px = f'{l["entry_price"]:g}'
                if "exit_price" in l:
                    px += f' → {l["exit_price"]:g}'
                bits.append(f'{l["action"][0]} {l["strike"]}{l["type"]} '
                            f'<span style="color:var(--mut)">{px}</span>')
            legs = f'<td style="font-size:11px">{" · ".join(bits)}</td>'
        rows.append(
            f'<tr><td class="n">{t["n"]}</td><td>{_e(t["entry"])}</td>'
            f'<td>{_e(t["exit"])}</td><td>{_e(t["exit_reason"])}</td>{legs}'
            f'<td class="n">{t["net_points"]:.2f}</td>'
            f'<td class="n {cls}">{_money(t["pnl_rupees"])}</td></tr>')
    return f'<div class="tt"><table>{head}{"".join(rows)}</table></div>'


def render(payload, backtest_id):
    s = payload.get("summary", {})
    h = payload.get("honesty", {})
    spec = payload.get("spec", {})
    n = s.get("n_trades", 0)
    pnl = s.get("total_pnl_rupees", 0.0)

    parts = [f'<div class="wrap"><h1>{_e(s.get("structure", "backtest"))}</h1>',
             f'<div class="sub">{_e(backtest_id)} · '
             f'{_e(s.get("period", {}).get("from"))} to {_e(s.get("period", {}).get("to"))} · '
             f'{n} trades</div>']

    # Honesty first, deliberately.
    score = h.get("health_score", 0)
    parts.append('<h2>What the evidence supports</h2><div class="card">')
    parts.append(f'<div class="verdict">{_e(h.get("verdict", "n/a")).replace("_", " ")}</div>')
    parts.append(f'<div class="sub" style="margin:0">{_e(h.get("explanation", ""))}</div>')
    parts.append(f'<div class="bar"><i style="width:{max(0, min(100, score))}%"></i></div>')
    parts.append(f'<div class="k" style="margin-top:6px;color:var(--mut);font-size:12px">'
                 f'health {score} / 100</div>')
    if h.get("score_breakdown"):
        rubric = {c["component"]: c for c in h.get("rubric", [])}
        parts.append('<div class="scroll"><table><tr><th>Component</th><th class="n">Score</th>'
                     '<th>Rule</th></tr>')
        for comp, got in h["score_breakdown"].items():
            r = rubric.get(comp, {})
            parts.append(f'<tr><td>{_e(comp.replace("_", " "))}</td>'
                         f'<td class="n">{got} / {r.get("max_points", "")}</td>'
                         f'<td style="color:var(--mut)">{_e(r.get("rule", ""))}</td></tr>')
        parts.append('</table></div>')
    parts.append('</div>')

    oos = h.get("out_of_sample")
    if oos:
        held = oos["held_up"]
        parts.append('<div class="card"><div class="k">Out of sample</div>'
                     f'<div class="sub" style="margin:4px 0 10px">{_e(oos["method"])}</div>'
                     '<div class="grid">')
        parts.append(_stat("In-sample ROM", f'{oos["in_sample"]["mean_return_on_margin"]:.2%}'))
        parts.append(_stat("Held-out ROM", f'{oos["out_of_sample"]["mean_return_on_margin"]:.2%}',
                           "good" if held else "bad"))
        parts.append(_stat("Held-out trades", oos["out_of_sample"]["n_trades"]))
        parts.append('</div>')
        if oos.get("caveat"):
            parts.append(f'<ul><li>{_e(oos["caveat"])}</li></ul>')
        parts.append('</div>')

    mc = h.get("multiple_comparisons")
    if mc:
        parts.append('<div class="card"><div class="k">Multiple comparisons</div>'
                     f'<div class="sub" style="margin:4px 0 0">{_e(mc["reading"])}</div>'
                     f'<ul><li>{mc["variants_tested_last_24h"]} variants in 24h · '
                     f'scope: {_e(mc["scope"])}</li>'
                     f'<li>expected max Sharpe under the null: '
                     f'{_e(mc["expected_max_sharpe_under_null"])} · basis: {_e(mc["basis"])}'
                     f'</li></ul></div>')

    wf = h.get("walk_forward")
    if wf:
        parts.append('<div class="card"><div class="k">Walk forward</div>'
                     '<div class="scroll"><table><tr><th>Fold</th><th>From</th><th>To</th>'
                     '<th class="n">Trades</th><th class="n">P&amp;L</th></tr>')
        for f in wf:
            cls = "good" if f["profitable"] else "bad"
            parts.append(f'<tr><td>{f["fold"]}</td><td>{_e(f["from"])}</td>'
                         f'<td>{_e(f["to"])}</td><td class="n">{f["n_trades"]}</td>'
                         f'<td class="n {cls}">{_money(f["pnl_rupees"])}</td></tr>')
        parts.append('</table></div></div>')

    # Then the numbers.
    parts.append('<h2>Result</h2><div class="card"><div class="grid">')
    parts.append(_stat("Net P&L", _money(pnl), "good" if pnl > 0 else "bad"))
    parts.append(_stat("Win rate", f'{s.get("win_rate", 0):.1%}'))
    parts.append(_stat("Mean return on margin", f'{(s.get("mean_return_on_margin") or 0):.2%}'))
    parts.append(_stat("Max drawdown", _money(s.get("max_drawdown_rupees", 0)), "bad"))
    parts.append(_stat("Charges", _money(-abs(s.get("total_charges_rupees", 0))), "warn"))
    parts.append(_stat("Slippage", f'{s.get("total_slippage_points", 0):.1f} pts', "warn"))
    parts.append('</div></div>')

    curve = (payload.get("equity_curve") or {}).get("rows") or []
    if len(curve) > 1:
        parts.append('<h2>Equity curve</h2><div class="card">')
        parts.append(_equity_chart(curve))
        parts.append('</div>')

    by_month = (payload.get("breakdown") or {}).get("by_month") or {}
    if by_month:
        parts.append('<h2>By month</h2><div class="card">')
        parts.append(_monthly_chart(by_month))
        parts.append('</div>')

    ratios = s.get("ratios")
    if ratios:
        parts.append('<div class="card"><div class="grid">')
        parts.append(_stat("Sharpe", ratios.get("sharpe")))
        parts.append(_stat("Profit factor", ratios.get("profit_factor")))
        parts.append(_stat("Calmar", ratios.get("calmar")))
        parts.append('</div>'
                     f'<ul><li>{_e(ratios.get("sharpe_basis", ""))}</li></ul></div>')
    elif s.get("ratios_withheld"):
        parts.append(f'<div class="card warn">{_e(s["ratios_withheld"])}</div>')

    cd = h.get("cost_drag")
    if cd:
        share = cd.get("share_of_edge_surviving")
        parts.append('<div class="card"><div class="k">Where the edge went</div>'
                     '<div class="grid" style="margin-top:8px">')
        parts.append(_stat("Gross", f'{cd["gross_points_before_costs"]:.0f} pts'))
        parts.append(_stat("Slippage", f'−{cd["slippage_points"]:.0f} pts', "warn"))
        parts.append(_stat("Charges", f'−{cd["charges_points"]:.0f} pts', "warn"))
        parts.append(_stat("Net", f'{cd["net_points_after_costs"]:.0f} pts',
                           "good" if cd["net_points_after_costs"] > 0 else "bad"))
        parts.append('</div>')
        if share is not None:
            parts.append(f'<ul><li>{share:.0%} of the gross edge survives real costs</li></ul>')
        parts.append('</div>')

    bd = payload.get("breakdown") or {}
    if bd:
        parts.append('<h2>Where the result came from</h2>')
        for title, key in (("Exit reason", "by_exit_reason"),
                           ("Weekday of entry", "by_weekday_of_entry"),
                           ("Days to expiry at entry", "by_dte_at_entry")):
            table = bd.get(key) or {}
            if not table:
                continue
            parts.append(f'<div class="card"><div class="k">{_e(title)}</div>'
                         '<div class="scroll"><table><tr><th></th><th class="n">Trades</th>'
                         '<th class="n">Win rate</th><th class="n">P&amp;L</th></tr>')
            for label, b in table.items():
                cls = "good" if b["pnl_rupees"] > 0 else "bad"
                parts.append(f'<tr><td>{_e(label)}</td><td class="n">{b["n_trades"]}</td>'
                             f'<td class="n">{b["win_rate"]:.1%}</td>'
                             f'<td class="n {cls}">{_money(b["pnl_rupees"])}</td></tr>')
            parts.append('</table></div></div>')
        hm = bd.get("holding_minutes")
        if hm:
            parts.append('<div class="card"><div class="grid">')
            parts.append(_stat("Median hold", _duration(hm["median"])))
            parts.append(_stat("Longest win streak", bd.get("longest_win_streak")))
            parts.append(_stat("Longest loss streak", bd.get("longest_loss_streak"), "bad"))
            parts.append(_stat("Best trade", _money(bd.get("best_trade_rupees", 0)), "good"))
            parts.append(_stat("Worst trade", _money(bd.get("worst_trade_rupees", 0)), "bad"))
            parts.append('</div></div>')

    trades = payload.get("trades") or []
    if trades:
        td = payload.get("trade_detail") or {}
        parts.append(f'<h2>Every trade</h2><div class="card">{_trades_table(trades)}')
        if td.get("truncation"):
            parts.append(f'<ul><li>{_e(td["truncation"])}</li></ul>')
        if not td.get("prices_included", True):
            parts.append(f'<ul><li>{_e(td.get("reason", ""))}</li></ul>')
        parts.append('</div>')

    warnings = s.get("warnings") or []
    notes = s.get("notes") or []
    if warnings or notes:
        parts.append('<h2>Caveats</h2><div class="card"><ul>')
        for w in warnings + notes:
            parts.append(f'<li>{_e(w)}</li>')
        parts.append('</ul></div>')

    parts.append('<h2>Specification</h2><div class="card"><div class="scroll">'
                 f'<pre style="margin:0;font-size:12px">{_e(json.dumps(spec, indent=1))}'
                 '</pre></div></div>')
    parts.append('<div class="foot">Educational backtesting. Not investment advice, not a '
                 'recommendation, and not a prediction. Past results describe the past.'
                 '</div></div>')

    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{_e(s.get("structure", "backtest"))} — Stratify</title>'
            f'<style>{CSS}</style></head><body>{"".join(parts)}</body></html>')
