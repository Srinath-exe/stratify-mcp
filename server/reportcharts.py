"""Charts computed from a REAL payload, in the catalogue's house style.

The thirteen drawings in `context/visuals/charts.js` are seeded specimens -- they say so
themselves -- so they show what a chart looks like, not what a strategy did. Everything in
this module reads the payload and draws the actual result.

WHAT THIS MODULE CAN AND CANNOT DRAW. I inventoried the payload before writing any of it:

    available  equity_curve rows (date, pnl, equity, drawdown), every released trade with
               margin_points / charges_rupees / slippage_points / holding_minutes /
               dte_at_entry / spot_at_entry / return_on_margin / exit_reason / legs,
               breakdown by month, weekday, DTE and exit reason, honesty's cost_drag,
               walk_forward folds, score_breakdown and bootstrap interval

    NOT there  implied volatility, IV rank, greeks, expected move. The catalogue has
               drawings for the last two and they cannot be wired: the engine does not
               return an IV surface and deliberately does not release one. They stay
               specimens until the payload carries solved greeks per trade.

HOUSE STYLE. Same classes as the catalogue so the two sets are indistinguishable:
`.ln .ar .bg .bb .ba .bm .dg .db .tk .cap .lg .lb .lm .axis-zero .guide-dash`. No new
colour is introduced and no class is redefined. DESIGN.md 2.3 holds throughout -- signed
quantities take the P&L pair, counts take the accent.

SIZING. Authored at 1180x420 (wide) and 560x360 (half) rather than the catalogue's 720,
because the report renders them at native size in a wide card and the catalogue's
dimensions were chosen for a 70rem column. Bigger canvas, same 10.5px labels, so they are
readable without being scaled up.
"""
import datetime as dt
import html
import math
from collections import defaultdict


def _e(v):
    return html.escape("" if v is None else str(v))


def _money(v):
    """Short rupees, signed. Never rounds a loss toward zero (DESIGN.md 8.3)."""
    if v is None:
        return "—"
    s = "−" if v < 0 else ""
    a = abs(v)
    if a >= 1e7:
        return f"{s}₹{a / 1e7:.2f}Cr"
    if a >= 1e5:
        return f"{s}₹{a / 1e5:.2f}L"
    if a >= 1000:
        return f"{s}₹{a / 1000:.1f}k"
    return f"{s}₹{a:.0f}"


def _svg(w, h, body, label):
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" preserveAspectRatio="xMidYMid meet" '
            f'role="img" aria-label="{_e(label)}">{body}</svg>')


def _txt(s, x, y, cls="tk", anchor="start"):
    return (f'<text x="{x:.1f}" y="{y:.1f}" class="{cls}" '
            f'text-anchor="{anchor}">{_e(s)}</text>')


def _zero(x0, x1, y):
    return f'<line x1="{x0:.1f}" y1="{y:.1f}" x2="{x1:.1f}" y2="{y:.1f}" class="axis-zero"/>'


def _bars(items, w, h, label, fmt=_money, note=None):
    """A signed bar chart. The workhorse: year returns, DTE buckets, weekdays all use it.

    Zero is always drawn and always in scale, so a row of losses cannot be rendered as if
    it were a row of small wins -- which is what an auto-fitted axis would do.
    """
    if not items:
        return ""
    P = {"t": 34, "r": 24, "b": 46, "l": 74}
    vals = [v for _, v in items]
    lo, hi = min(min(vals), 0.0), max(max(vals), 0.0)
    span = (hi - lo) or 1.0
    plot_h = h - P["t"] - P["b"]
    y0 = P["t"] + (hi / span) * plot_h
    slot = (w - P["l"] - P["r"]) / len(items)
    bw = min(slot * 0.62, 84)
    out = [_zero(P["l"], w - P["r"], y0)]
    for i, (k, v) in enumerate(items):
        cx = P["l"] + slot * (i + 0.5)
        y = P["t"] + ((hi - max(v, 0.0)) / span) * plot_h
        bh = abs(v) / span * plot_h
        out.append(f'<rect x="{cx - bw / 2:.1f}" y="{y:.1f}" width="{bw:.1f}" '
                   f'height="{max(bh, 1):.1f}" class="{"bg" if v >= 0 else "bb"}" rx="3"/>')
        # The value sits outside the bar unless that would land it on the category label
        # underneath -- a downward bar reaching the axis put "-6.2k" straight through
        # "2026-02". Where there is no room outside, it goes INSIDE the bar's own end.
        end = y if v >= 0 else y + bh
        outside = (end - 7) if v >= 0 else (end + 15)
        floor_ = h - P["b"] - 2
        if v >= 0:
            ly, cls = ((outside, "lg") if outside > P["t"] + 6
                       else (end + 16, "cl"))
        else:
            ly, cls = ((outside, "lb") if outside < floor_ else (end - 8, "cl"))
        out.append(_txt(fmt(v), cx, ly, cls, "middle"))
        out.append(_txt(k, cx, h - P["b"] + 20, "tk", "middle"))
    out.append(_txt(_money(hi), P["l"] - 8, P["t"] + 4, "tk", "end"))
    out.append(_txt(_money(lo), P["l"] - 8, h - P["b"] + 4, "tk", "end"))
    if note:
        out.append(_txt(note, P["l"], P["t"] - 14, "cap"))
    return _svg(w, h, "".join(out), label)


# ------------------------------------------------------------------ year returns
def year_returns(payload, w=1180, h=420):
    """P&L by calendar year. The first thing anyone looks for and the report did not have.

    Built from the equity curve rather than from trades, so a year with no trades still
    appears as a zero rather than silently vanishing from the axis.
    """
    rows = ((payload.get("equity_curve") or {}).get("rows")) or []
    by_year = defaultdict(float)
    for r in rows:
        try:
            by_year[str(r[0])[:4]] += float(r[1] or 0)
        except (IndexError, TypeError, ValueError):
            continue
    if not by_year:
        return ""
    items = sorted(by_year.items())
    pos = sum(1 for _, v in items if v > 0)
    note = (f"{pos} of {len(items)} years positive · one lot, uncompounded")
    return _bars(items, w, h, "profit and loss by calendar year", note=note)


# --------------------------------------------------------------------- the cost creep
def cost_creep(payload, w=1180, h=420):
    """Cumulative gross edge against what was left after costs.

    THE MOST HONEST CHART IN THE REPORT. The waterfall shows the same subtraction once, at
    the end; this shows the gap opening trade by trade, which is what a cost problem
    actually looks like from the inside. A strategy whose two lines diverge steadily is
    paying a toll it never earns back, and no single-number cost ratio makes that as plain.
    """
    # Per-trade slippage and charges live only on the released rows, so unlike the curve
    # charts this one genuinely covers a SAMPLE. It says so rather than letting the shape
    # be read as the whole history -- the waterfall beside it carries the full totals.
    trades = payload.get("trades") or []
    pts = [t for t in trades if t.get("gross_points") is not None]
    if len(pts) < 8:
        return ""
    total = ((payload.get("trade_detail") or {}).get("trades_total") or len(pts))
    P = {"t": 40, "r": 120, "b": 40, "l": 76}
    lot = pts[0].get("lot_size") or 1
    gross = net = 0.0
    gs, ns = [], []
    for t in pts:
        gross += (t.get("gross_points") or 0) * lot
        net += (t.get("pnl_rupees") or 0)
        gs.append(gross)
        ns.append(net)
    lo = min(min(gs), min(ns), 0.0)
    hi = max(max(gs), max(ns), 0.0)
    span = (hi - lo) or 1.0
    n = len(gs)
    pw, ph = w - P["l"] - P["r"], h - P["t"] - P["b"]

    def xy(i, v):
        return (P["l"] + i / max(1, n - 1) * pw, P["t"] + (hi - v) / span * ph)

    def d(series):
        return "".join(("M" if i == 0 else "L") + f"{xy(i, v)[0]:.1f} {xy(i, v)[1]:.1f}"
                       for i, v in enumerate(series))

    gx, gy = xy(n - 1, gs[-1])
    nx, ny = xy(n - 1, ns[-1])
    band = (f'{d(gs)}L{nx:.1f} {ny:.1f}'
            + "".join("L" + f"{xy(i, v)[0]:.1f} {xy(i, v)[1]:.1f}"
                      for i in range(n - 1, -1, -1) for v in [ns[i]]) + "Z")
    out = [
        _zero(P["l"], w - P["r"], P["t"] + hi / span * ph),
        f'<path d="{band}" class="ar-b"/>',
        f'<path d="{d(gs)}" class="ln" fill="none"/>',
        f'<path d="{d(ns)}" class="ln-b" fill="none"/>',
        f'<circle cx="{gx:.1f}" cy="{gy:.1f}" r="4" class="da"/>',
        f'<circle cx="{nx:.1f}" cy="{ny:.1f}" r="4" class="db"/>',
        _txt(f"gross {_money(gs[-1])}", gx + 9, gy + 4, "la"),
        _txt(f"net {_money(ns[-1])}", nx + 9, ny + 4, "lb"),
        _txt("the shaded band is everything paid away in charges and slippage"
             + (f" \u00b7 the first {n} of {total} trades, the only ones carrying "
                f"per-trade costs" if total > n else ""),
             P["l"], P["t"] - 16, "cap"),
        _txt("trade 1", P["l"], h - 14, "tk"),
        _txt(f"trade {n}", w - P["r"], h - 14, "tk", "end"),
    ]
    return _svg(w, h, "".join(out), "cumulative gross edge against net result")


# ------------------------------------------------------------------ cost share by year
def cost_share(payload, w=560, h=360):
    """What share of each year's gross edge the costs took.

    Per year rather than overall, because a cost ratio moves with how often you traded and
    with the premium you were collecting -- and a single lifetime number hides a year where
    the strategy was working for its broker.
    """
    trades = payload.get("trades") or []
    g, c = defaultdict(float), defaultdict(float)
    for t in trades:
        y = str(t.get("entry") or "")[:4]
        if not y.isdigit():
            continue
        lot = t.get("lot_size") or 1
        g[y] += abs((t.get("gross_points") or 0) * lot)
        c[y] += (t.get("charges_rupees") or 0) + abs((t.get("slippage_points") or 0) * lot)
    items = [(y, (c[y] / g[y] * 100 if g[y] else 0.0)) for y in sorted(g)]
    if not items:
        return ""
    return _bars(items, w, h, "costs as a share of gross edge, by year",
                 fmt=lambda v: f"{v:.0f}%",
                 note="costs ÷ gross, per year — above 100% means costs exceeded the edge")


# ---------------------------------------------------------------------- holding time
def holding_time(payload, w=560, h=360):
    """How long a position was actually open. Bucketed, because the raw minute count spans
    four orders of magnitude between a same-session exit and a held-to-expiry cycle."""
    trades = payload.get("trades") or []
    mins = [t.get("holding_minutes") for t in trades if t.get("holding_minutes") is not None]
    if len(mins) < 8:
        return ""
    buckets = [("<1h", 0, 60), ("1-6h", 60, 360), ("same day", 360, 900),
               ("1-2d", 900, 2880), ("2-4d", 2880, 5760), (">4d", 5760, 10 ** 9)]
    counts = [(lbl, float(sum(1 for m in mins if lo <= m < hi)))
              for lbl, lo, hi in buckets]
    # A daily strategy holds every position for the same 270 minutes, so this collapses to
    # one bar with five zeros beside it -- which reads as a broken chart and says nothing
    # the rules card did not. Below two occupied buckets it is a stat, not a drawing.
    if sum(1 for _, v in counts if v) < 2:
        return ""
    counts = [(lbl, v) for lbl, v in counts if v] or counts
    P = {"t": 36, "r": 24, "b": 46, "l": 60}
    hi = max(v for _, v in counts) or 1
    slot = (w - P["l"] - P["r"]) / len(counts)
    ph = h - P["t"] - P["b"]
    out = []
    for i, (lbl, v) in enumerate(counts):
        cx = P["l"] + slot * (i + 0.5)
        bh = v / hi * ph
        out.append(f'<rect x="{cx - slot * 0.3:.1f}" y="{P["t"] + ph - bh:.1f}" '
                   f'width="{slot * 0.6:.1f}" height="{max(bh, 1):.1f}" class="ba" rx="3"/>')
        out.append(_txt(f"{int(v)}", cx, P["t"] + ph - bh - 7, "la", "middle"))
        out.append(_txt(lbl, cx, h - P["b"] + 20, "tk", "middle"))
    med = sorted(mins)[len(mins) // 2]
    out.append(_txt(f"median {med / 60:.1f} hours held", P["l"], P["t"] - 14, "cap"))
    return _svg(w, h, "".join(out), "distribution of holding time")


# -------------------------------------------------------------------- rolling win rate
def rolling_win_rate(payload, w=1180, h=380, window=30):
    """Win rate over a moving window of trades.

    A single lifetime win rate is an average over regimes the strategy may never see again.
    This shows whether the edge was steady or whether one stretch carried it -- and the
    window is trades, not days, so it is not distorted by how often the strategy traded.
    """
    # FROM THE CURVE. This read `trades` until an audit found it had never once rendered:
    # a standard response carries 25 rows and the window needs 60, so the chart silently
    # excluded itself from every report including the 234-trade ones.
    pnl = [x[1] for x in _curve(payload)]
    if len(pnl) < window * 2:
        return ""
    series = []
    for i in range(window, len(pnl) + 1):
        chunk = pnl[i - window:i]
        series.append(sum(1 for v in chunk if v > 0) / window * 100)
    P = {"t": 40, "r": 92, "b": 40, "l": 66}
    pw, ph = w - P["l"] - P["r"], h - P["t"] - P["b"]
    lo, hi = 0.0, 100.0
    n = len(series)

    def xy(i, v):
        return (P["l"] + i / max(1, n - 1) * pw, P["t"] + (hi - v) / (hi - lo) * ph)

    d = "".join(("M" if i == 0 else "L") + f"{xy(i, v)[0]:.1f} {xy(i, v)[1]:.1f}"
                for i, v in enumerate(series))
    overall = sum(1 for v in pnl if v > 0) / len(pnl) * 100
    oy = P["t"] + (hi - overall) / (hi - lo) * ph
    out = [
        f'<line x1="{P["l"]}" y1="{oy:.1f}" x2="{w - P["r"]}" y2="{oy:.1f}" class="guide-dash"/>',
        _txt(f"lifetime {overall:.0f}%", w - P["r"] + 8, oy + 4, "lm"),
        f'<path d="{d}" class="ln" fill="none"/>',
        _txt("100%", P["l"] - 8, P["t"] + 4, "tk", "end"),
        _txt("0%", P["l"] - 8, P["t"] + ph + 4, "tk", "end"),
        _txt(f"win rate over a rolling {window}-trade window", P["l"], P["t"] - 16, "cap"),
    ]
    return _svg(w, h, "".join(out), f"rolling {window}-trade win rate")


# ------------------------------------------------------------------- score breakdown
def score_breakdown(payload, w=560, h=360):
    """The health score, decomposed. A single 52/100 says nothing about WHICH check failed,
    and the five components are what a reader should actually argue with."""
    h_ = payload.get("honesty") or {}
    br = h_.get("score_breakdown") or {}
    rubric = {r.get("component"): r for r in (h_.get("rubric") or [])}
    if not br:
        return ""
    rows = [(k, float(v), float((rubric.get(k) or {}).get("max_points") or 0))
            for k, v in br.items()]
    P = {"t": 36, "r": 60, "b": 24, "l": 150}
    rh = (h - P["t"] - P["b"]) / max(1, len(rows))
    bw = w - P["l"] - P["r"]
    out = [_txt("each bar is one check, and the report shows its working below",
                P["l"] - 100, P["t"] - 14, "cap")]
    for i, (k, got, mx) in enumerate(rows):
        y = P["t"] + rh * i + rh * 0.22
        bh = rh * 0.44
        frac = (got / mx) if mx else 0.0
        # The track is an OUTLINE, not a fill. Drawn as a muted solid it made a check
        # scoring 0/25 look like a full bar in a slightly different shade.
        out.append(f'<rect x="{P["l"]}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" '
                   f'rx="3" style="fill:none;stroke:var(--faint);stroke-width:1;'
                   f'opacity:.45"/>')
        out.append(f'<rect x="{P["l"]}" y="{y:.1f}" width="{bw * frac:.1f}" '
                   f'height="{bh:.1f}" class="{"bg" if frac >= .5 else "bb"}" rx="3"/>')
        out.append(_txt(k.replace("_", " "), P["l"] - 12, y + bh * .78, "lm", "end"))
        out.append(_txt(f"{got:.0f}/{mx:.0f}", w - P["r"] + 8, y + bh * .78,
                        "lg" if frac >= .5 else "lb"))
    return _svg(w, h, "".join(out), "how the health score was reached")


# ------------------------------------------------------------- margin against outcome
def margin_vs_outcome(payload, w=560, h=360):
    """Capital blocked against what the trade returned on it.

    The question this answers is whether the strategy was paid for the capital it tied up,
    or whether its best trades were simply its largest ones.
    """
    # Margin travels on the curve now, so this is every trade rather than the first 25.
    pts = [(m, pnl / m * 100) for _, pnl, _, _, m, *_ in _curve(payload) if m]
    if len(pts) < 8:
        return ""
    P = {"t": 36, "r": 24, "b": 46, "l": 70}
    mx = [m for m, _ in pts]
    ry = [r for _, r in pts]
    x0, x1 = min(mx), max(mx)
    y0, y1 = min(min(ry), 0.0), max(max(ry), 0.0)
    xs, ys = (x1 - x0) or 1, (y1 - y0) or 1
    pw, ph = w - P["l"] - P["r"], h - P["t"] - P["b"]
    zy = P["t"] + (y1 - 0) / ys * ph
    out = [_zero(P["l"], w - P["r"], zy)]
    for m, r in pts:
        cx = P["l"] + (m - x0) / xs * pw
        cy = P["t"] + (y1 - r) / ys * ph
        out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{3 if len(pts) < 90 else 2}" '
                   f'class="{"pt-good" if r >= 0 else "pt-bad"}"/>')
    out += [
        _txt(_money(x0), P["l"], h - P["b"] + 20, "tk"),
        _txt(_money(x1), w - P["r"], h - P["b"] + 20, "tk", "end"),
        _txt(f"{y1:+.0f}%", P["l"] - 8, P["t"] + 4, "tk", "end"),
        _txt(f"{y0:+.0f}%", P["l"] - 8, P["t"] + ph + 4, "tk", "end"),
        _txt(f"{len(pts)} trades · margin blocked (x) against return on it (y)",
             P["l"], P["t"] - 14, "cap"),
    ]
    return _svg(w, h, "".join(out), "margin blocked against return on margin")


# ------------------------------------------------------------------- simple breakdowns
def by_bucket(payload, key, title, w=560, h=360):
    """P&L by DTE at entry, by weekday, or by exit reason — whichever the caller names.

    These come straight out of `breakdown`, already aggregated by the engine, so nothing is
    recomputed here and the report cannot disagree with the API about them.
    """
    b = (payload.get("breakdown") or {}).get(key) or {}
    items = []
    for k, v in b.items():
        val = v.get("pnl_rupees") if isinstance(v, dict) else v
        n = v.get("n_trades") if isinstance(v, dict) else None
        if val is None:
            continue
        items.append((f"{k}" + (f" ({n})" if n else ""), float(val)))
    if not items:
        return ""
    return _bars(items, w, h, title, note=title)



# ============================================================================ the curve
#
# EVERYTHING BELOW READS THE EQUITY CURVE FIRST. That is the whole lesson of the audit
# that produced this file's second half: `trades` is capped at 25 rows on a standard
# response and the bucket breakdowns routinely collapse to a single key -- every one of
# these strategies exits for exactly one reason, so "P&L by how the trade ended" is a
# chart with one bar on it. The curve carries every trade for every strategy, so a drawing
# built on it is dense at 26 trades and at 234. Where a chart can only come from the
# buckets it now has to prove it has something to say before it is drawn at all.


def _curve(payload):
    """[(date, pnl, equity, drawdown, margin)] -- margin is None on older results."""
    rows = ((payload.get("equity_curve") or {}).get("rows")) or []
    cols = ((payload.get("equity_curve") or {}).get("columns")) or []
    mi = cols.index("margin_rupees") if "margin_rupees" in cols else None
    out = []
    for r in rows:
        try:
            out.append((str(r[0]), float(r[1]), float(r[2]), float(r[3]),
                        float(r[mi]) if mi is not None and len(r) > mi else None))
        except (IndexError, TypeError, ValueError):
            continue
    return out


def _path(pts):
    return " ".join(("M" if i == 0 else "L") + f"{x:.1f} {y:.1f}"
                    for i, (x, y) in enumerate(pts))


def _smooth(pts, t=0.2):
    """A cubic path through the points, with control points CLAMPED to each segment.

    An unclamped spline overshoots a local extreme, and on an equity curve that draws
    money that was never made or lost -- a dip below the trough between two points is a
    drawdown the account never had. The clamp costs a little grace and buys the guarantee
    that no pixel of the line lies outside the data.
    """
    if len(pts) < 3:
        return _path(pts)
    d = [f"M{pts[0][0]:.2f} {pts[0][1]:.2f}"]
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        xp, yp = pts[i - 1] if i else pts[i]
        xn, yn = pts[i + 2] if i + 2 < len(pts) else pts[i + 1]
        lo, hi = min(y0, y1), max(y0, y1)
        c1x = min(max(x0 + (x1 - xp) * t, x0), x1)
        c2x = min(max(x1 - (xn - x0) * t, x0), x1)
        c1y = min(max(y0 + (y1 - yp) * t, lo), hi)
        c2y = min(max(y1 - (yn - y0) * t, lo), hi)
        d.append(f"C{c1x:.2f} {c1y:.2f} {c2x:.2f} {c2y:.2f} {x1:.2f} {y1:.2f}")
    return " ".join(d)


def _nice(lo, hi, n=4):
    """Round values to hang gridlines on. 0, 25k, 50k -- never 17,431."""
    span = hi - lo
    if span <= 0:
        return [lo]
    raw = span / n
    mag = 10 ** math.floor(math.log10(abs(raw))) if raw else 1
    step = next((mag * m for m in (1, 2, 2.5, 5, 10) if raw <= mag * m), mag * 10)
    out, v = [], math.ceil(lo / step) * step
    while v <= hi + step * 1e-6 and len(out) < 12:
        out.append(v)
        v += step
    return out


def _month_ticks(dates, want=6):
    """First index of each month, thinned to about `want` labels."""
    firsts, seen = [], set()
    for i, d in enumerate(dates):
        k = d[:7]
        if k not in seen:
            seen.add(k)
            firsts.append((i, d))
    if len(firsts) <= want:
        return firsts
    step = max(1, round(len(firsts) / want))
    return firsts[::step]


MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def equity_underwater(payload, w=1180, h=460):
    """Cumulative P&L above, time spent under water below, sharing one x-axis.

    A drawdown is not a separate fact from the return that recovered it, and putting them
    on two charts invites the reader to look at one. This was the single most important
    drawing in the report and until 2026-08-31 it was a SEEDED SPECIMEN -- an invented
    curve, on the page, above a real strategy's name.

    WHAT MAKES IT WORTH LOOKING AT, rather than merely correct. The high-water mark is
    drawn behind the equity, so every gap between the two lines IS a drawdown and the
    reader never has to hold two charts in their head. The deepest one is shaded across
    both panels and labelled with how long it lasted, because "how far down" and "for how
    long" are different fears. Gridlines hang on round numbers. The line is smoothed with
    clamped control points, so it flows without ever drawing a value the account did not
    have.
    """
    c = _curve(payload)
    if len(c) < 3:
        return ""
    eq = [x[2] for x in c]
    dd = [x[3] for x in c]
    dates = [x[0] for x in c]

    P = {"t": 46, "r": 96, "b": 40, "l": 84}
    dry = min(dd) >= -0.5
    split = 1.0 if dry else 0.68
    pw = w - P["l"] - P["r"]
    body = h - P["t"] - P["b"]
    eh = body * split - (0 if dry else 16)
    dh = body * (1 - split)
    dy0 = P["t"] + eh + 26

    lo, hi = min(min(eq), 0.0), max(max(eq), 0.0)
    pad = (hi - lo) * 0.08 or 1.0
    lo, hi = lo - pad, hi + pad
    span = hi - lo
    step = pw / max(len(c) - 1, 1)

    def X(i):
        return P["l"] + i * step

    def Y(v):
        return P["t"] + (hi - v) / span * eh

    up = eq[-1] >= 0
    col = "var(--good)" if up else "var(--crit)"
    uid = f"eq{abs(hash((dates[0], dates[-1], round(eq[-1])))) % 100000}"

    # The high-water mark. Every gap between this and the equity line is a drawdown.
    peak, run = [], eq[0]
    for v in eq:
        run = max(run, v)
        peak.append(run)

    # The worst one, and how long it took to get back (or that it never did).
    trough = dd.index(min(dd))
    start = trough
    while start > 0 and dd[start - 1] < -0.5:
        start -= 1
    end = trough
    while end < len(dd) - 1 and dd[end] < -0.5:
        end += 1
    recovered = dd[end] >= -0.5

    pts = [(X(i), Y(v)) for i, v in enumerate(eq)]
    line = _smooth(pts)
    out = [
        f'<defs><linearGradient id="{uid}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{col}" stop-opacity=".34"/>'
        f'<stop offset="70%" stop-color="{col}" stop-opacity=".06"/>'
        f'<stop offset="100%" stop-color="{col}" stop-opacity="0"/></linearGradient>'
        f'<linearGradient id="{uid}d" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="var(--crit)" stop-opacity=".05"/>'
        f'<stop offset="100%" stop-color="var(--crit)" stop-opacity=".42"/>'
        f'</linearGradient></defs>',
    ]

    # Gridlines first, so everything else sits on top of them.
    for v in _nice(lo, hi):
        y = Y(v)
        out.append(f'<line x1="{P["l"]:.1f}" y1="{y:.1f}" x2="{w - P["r"]:.1f}" '
                   f'y2="{y:.1f}" class="zero" opacity="{.55 if v else 1}"/>')
        out.append(_txt(_money(v), P["l"] - 10, y + 4, "tk", "end"))

    # The deepest drawdown, shaded across the whole plot.
    if not dry and end > start:
        out.append(f'<rect x="{X(start):.1f}" y="{P["t"]:.1f}" '
                   f'width="{max(X(end) - X(start), 2):.1f}" '
                   f'height="{(eh if dry else dy0 + dh - P["t"]):.1f}" '
                   f'fill="var(--crit)" opacity=".05"/>')

    out += [
        f'<path d="{line} L{pts[-1][0]:.2f} {Y(lo):.2f} L{P["l"]:.2f} {Y(lo):.2f} Z" '
        f'fill="url(#{uid})"/>',
        f'<path d="{_smooth([(X(i), Y(v)) for i, v in enumerate(peak)])}" '
        f'fill="none" stroke="var(--faint)" stroke-width="1" stroke-dasharray="4 4" '
        f'opacity=".65"/>',
        f'<path d="{line}" fill="none" stroke="{col}" stroke-width="2.2" '
        f'stroke-linejoin="round" stroke-linecap="round"/>',
        # The end of the line, named. It is the number the whole chart is about.
        f'<circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="4.5" fill="{col}"/>',
        f'<circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="9" fill="{col}" '
        f'opacity=".18"/>',
        _txt(_money(eq[-1]), pts[-1][0] + 14, pts[-1][1] + 5,
             "lg" if up else "lb"),
    ]

    if not dry:
        # The depth of the worst hole, marked between the high-water line and the curve.
        # Solid it read as a spike in the data rather than as an annotation of it.
        wy = Y(peak[trough])
        out.append(f'<line x1="{X(trough):.1f}" y1="{wy:.1f}" x2="{X(trough):.1f}" '
                   f'y2="{pts[trough][1]:.1f}" stroke="var(--crit)" stroke-width="1" '
                   f'stroke-dasharray="3 3" opacity=".5"/>')

        # Under water, drawn downward from its own zero so the panels cannot be confused.
        worst = min(min(dd), -1.0)
        dpts = [(X(i), dy0 + (v / worst) * dh) for i, v in enumerate(dd)]
        dline = _smooth(dpts)
        out += [
            f'<path d="{dline} L{dpts[-1][0]:.2f} {dy0:.2f} L{P["l"]:.2f} {dy0:.2f} Z" '
            f'fill="url(#{uid}d)"/>',
            f'<path d="{dline}" fill="none" stroke="var(--crit)" stroke-width="1.5" '
            f'opacity=".9"/>',
            f'<line x1="{P["l"]:.1f}" y1="{dy0:.1f}" x2="{w - P["r"]:.1f}" '
            f'y2="{dy0:.1f}" class="zero"/>',
            _txt("under water", P["l"] - 10, dy0 + 4, "tk", "end"),
            _txt(_money(worst), P["l"] - 10, dy0 + dh + 4, "tk", "end"),
            _txt(_money(min(dd)), X(trough) + 8, dy0 + dh - 6, "lb"),
        ]

    # Dates along the bottom, at month boundaries rather than at arbitrary intervals.
    for i, d in _month_ticks(dates):
        out.append(f'<line x1="{X(i):.1f}" y1="{h - P["b"] + 2:.1f}" x2="{X(i):.1f}" '
                   f'y2="{h - P["b"] + 7:.1f}" class="zero"/>')
        out.append(_txt(f"{MONTHS[int(d[5:7]) - 1]} {d[2:4]}", X(i), h - P["b"] + 22,
                        "tk", "middle"))

    days = 0
    try:
        a_ = dt.date(*map(int, dates[start].split("-")))
        b_ = dt.date(*map(int, dates[end].split("-")))
        days = (b_ - a_).days
    except (TypeError, ValueError):
        pass
    note = ("never under water — the running total never gave back a rupee, which over "
            f"{len(c)} trades is a fact about the window as much as the strategy" if dry
            else f"deepest hole {_money(min(dd))} from {dates[start]}, "
                 + (f"back to the high after {days} days" if recovered
                    else f"still {_money(dd[-1])} down {days} days later"))
    out.append(_txt(note, P["l"] - 10, P["t"] - 20, "cap"))
    out.append(_txt("high-water mark", w - P["r"] + 10, P["t"] - 20, "tk"))
    return _svg(w, h, "".join(out), "cumulative profit and loss with drawdown")


def trade_sequence(payload, w=1180, h=380):
    """Every trade in order, as a signed bar. Dense at any trade count.

    The distribution says what the trades were; this says WHEN they were, which is the
    question a reader actually has after seeing a drawdown on the curve above.
    """
    c = _curve(payload)
    if len(c) < 3:
        return ""
    P = {"t": 34, "r": 22, "b": 30, "l": 78}
    pw, ph = w - P["l"] - P["r"], h - P["t"] - P["b"]
    v = [x[1] for x in c]
    lo, hi = min(min(v), 0.0), max(max(v), 0.0)
    span = (hi - lo) or 1.0
    y0 = P["t"] + (hi / span) * ph
    slot = pw / len(v)
    bw = max(min(slot * 0.7, 26), 1.0)
    out = [_zero(P["l"], w - P["r"], y0)]
    for i, val in enumerate(v):
        cx = P["l"] + slot * (i + 0.5)
        y = P["t"] + ((hi - max(val, 0.0)) / span) * ph
        out.append(f'<rect x="{cx - bw / 2:.2f}" y="{y:.1f}" width="{bw:.2f}" '
                   f'height="{max(abs(val) / span * ph, 1):.1f}" '
                   f'class="{"bg" if val >= 0 else "bb"}"/>')
    wins = sum(1 for x in v if x > 0)
    out += [_txt(_money(hi), P["l"] - 8, P["t"] + 4, "tk", "end"),
            _txt(_money(lo), P["l"] - 8, P["t"] + ph + 4, "tk", "end"),
            _txt(f"{len(v)} trades in order · {wins} up, {len(v) - wins} down · "
                 f"best {_money(hi)}, worst {_money(lo)}", P["l"], P["t"] - 14, "cap"),
            _txt(c[0][0], P["l"], h - 8, "tk"),
            _txt(c[-1][0], w - P["r"], h - 8, "tk", "end")]
    return _svg(w, h, "".join(out), "profit and loss of every trade in order")


def _hist(vals, w, h, label, fmt, note, bins=23):
    """A signed histogram, zero always on a bin boundary so the two sides are comparable."""
    if len(vals) < 6:
        return ""
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return ""
    step = (hi - lo) / bins
    edges = [lo + i * step for i in range(bins + 1)]
    counts = [0] * bins
    for v in vals:
        i = min(int((v - lo) / step), bins - 1)
        counts[i] += 1
    P = {"t": 34, "r": 22, "b": 42, "l": 46}
    pw, ph = w - P["l"] - P["r"], h - P["t"] - P["b"]
    mx = max(counts) or 1
    slot = pw / bins
    out = []
    for i, n in enumerate(counts):
        mid = (edges[i] + edges[i + 1]) / 2
        bh = n / mx * ph
        out.append(f'<rect x="{P["l"] + slot * i + 1:.1f}" y="{P["t"] + ph - bh:.1f}" '
                   f'width="{max(slot - 2, 1):.1f}" height="{max(bh, 0.6):.1f}" '
                   f'class="{"bg" if mid >= 0 else "bb"}" rx="2"/>')
    zx = P["l"] + (0.0 - lo) / (hi - lo) * pw
    if lo < 0 < hi:
        out.append(f'<line x1="{zx:.1f}" y1="{P["t"] - 6:.1f}" x2="{zx:.1f}" '
                   f'y2="{P["t"] + ph:.1f}" class="dash"/>')
    out += [_txt(fmt(lo), P["l"], h - P["b"] + 20, "tk"),
            _txt(fmt(hi), w - P["r"], h - P["b"] + 20, "tk", "end"),
            _txt(str(mx), P["l"] - 8, P["t"] + 10, "tk", "end"),
            _txt(note, P["l"], P["t"] - 14, "cap")]
    return _svg(w, h, "".join(out), label)


def distribution(payload, w=1180, h=380):
    """Per-trade P&L, every trade. The left tail is the strategy."""
    v = [x[1] for x in _curve(payload)]
    if len(v) < 6:
        return ""
    v.sort()
    med = v[len(v) // 2]
    wins = [x for x in v if x > 0]
    losses = [x for x in v if x < 0]
    note = (f"median {_money(med)} · typical win {_money(sum(wins) / len(wins))} against "
            f"typical loss {_money(sum(losses) / len(losses))}"
            if wins and losses else f"median {_money(med)}")
    return _hist(v, w, h, "distribution of per-trade profit and loss", _money, note)


def rom_distribution(payload, w=560, h=360):
    """The same trades as a return on the margin each one blocked.

    Rupees say how big the position was; this says how good the trade was. On a book whose
    margin moves with the market they are different rankings.
    """
    v = [x[1] / x[4] for x in _curve(payload) if x[4]]
    if len(v) < 6:
        return ""
    v.sort()
    return _hist(v, w, h, "return on margin per trade",
                 lambda x: f"{x * 100:+.1f}%", f"median {v[len(v) // 2] * 100:+.2f}%",
                 bins=17)


def margin_timeline(payload, w=1180, h=360):
    """What the strategy had blocked, cycle by cycle, and how much it moved.

    Margin is not a constant. It follows the market, and a strategy whose requirement
    doubles in a stressed month needs capital it did not need when it was chosen.
    """
    c = [(d, m) for d, _, _, _, m in _curve(payload) if m]
    if len(c) < 3:
        return ""
    P = {"t": 34, "r": 22, "b": 30, "l": 84}
    pw, ph = w - P["l"] - P["r"], h - P["t"] - P["b"]
    vals = [m for _, m in c]
    hi = max(vals)
    step = pw / max(len(c) - 1, 1)
    pts = [(P["l"] + i * step, P["t"] + (1 - m / hi) * ph) for i, m in enumerate(vals)]
    med = sorted(vals)[len(vals) // 2]
    my = P["t"] + (1 - med / hi) * ph
    out = [f'<path d="{_path(pts)} L{pts[-1][0]:.1f} {P["t"] + ph:.1f} '
           f'L{P["l"]:.1f} {P["t"] + ph:.1f} Z" class="ar"/>',
           f'<path d="{_path(pts)}" class="ln"/>',
           f'<line x1="{P["l"]:.1f}" y1="{my:.1f}" x2="{w - P["r"]:.1f}" '
           f'y2="{my:.1f}" class="dash"/>',
           _txt(f"median {_money(med)}", w - P["r"], my - 7, "tk", "end"),
           _txt(_money(hi), P["l"] - 8, P["t"] + 4, "tk", "end"),
           _txt("0", P["l"] - 8, P["t"] + ph + 4, "tk", "end"),
           _txt(f"one lot · peak {_money(hi)} is {hi / med:.2f}x the median, so an "
                f"account sized on the median would have been short at the peak",
                P["l"], P["t"] - 14, "cap"),
           _txt(c[0][0], P["l"], h - 8, "tk"),
           _txt(c[-1][0], w - P["r"], h - 8, "tk", "end")]
    return _svg(w, h, "".join(out), "margin blocked over time")


# ========================================================================== the honesty


def folds(payload, w=560, h=360):
    """Walk-forward folds, each judged only on data after the one before it."""
    wf = ((payload.get("honesty") or {}).get("walk_forward")) or []
    if len(wf) < 2:
        return ""
    items = [(f"{f.get('from', '')[:7]}", float(f.get("pnl_rupees") or 0)) for f in wf]
    good = sum(1 for f in wf if f.get("profitable"))
    return _bars(items, w, h, "profit and loss per walk-forward fold",
                 note=f"{good} of {len(wf)} folds profitable")


def oos_split(payload, w=560, h=360):
    """In-sample against out-of-sample, on the mean return per trade.

    Rupees would make this chart a statement about how many trades fell in each half. The
    per-trade mean is the comparison the split was made to support.
    """
    oos = ((payload.get("honesty") or {}).get("out_of_sample")) or {}
    a, b = oos.get("in_sample") or {}, oos.get("out_of_sample") or {}
    if not a or not b:
        return ""
    ins, out = a.get("mean_return_on_margin"), b.get("mean_return_on_margin")
    if ins is None or out is None:
        return ""
    items = [(f"first 70%  ({a.get('n_trades')})", float(ins) * 100),
             (f"last 30%  ({b.get('n_trades')})", float(out) * 100)]
    held = "held up" if (out or 0) >= 0 and (out or 0) >= (ins or 0) * 0.5 else "did not"
    return _bars(items, w, h, "mean return on margin, in sample against out",
                 fmt=lambda v: f"{v:+.2f}%",
                 note=f"chronological split, never random · {held}")


def bootstrap(payload, w=560, h=360):
    """The 95% interval around return on margin, against zero and the observed mean.

    A point estimate on fifty trades is a number with a cloud around it, and the only
    useful question is whether the cloud clears zero. Drawn rather than printed because an
    interval that straddles zero should LOOK like one.
    """
    h_ = payload.get("honesty") or {}
    ci = h_.get("bootstrap_ci_95_return_on_margin")
    if not ci or len(ci) != 2 or ci[0] is None or ci[1] is None:
        return ""
    lo, hi = float(ci[0]) * 100, float(ci[1]) * 100
    obs = (payload.get("summary") or {}).get("mean_return_on_margin")
    obs = float(obs) * 100 if obs is not None else None

    P = {"t": 46, "r": 46, "b": 64, "l": 46}
    pw = w - P["l"] - P["r"]
    # Domain is the interval AND zero AND the observed mean, padded -- not a window
    # symmetric about zero, which left a wholly negative interval in the left third.
    marks = [lo, hi, 0.0] + ([obs] if obs is not None else [])
    d_lo, d_hi = min(marks), max(marks)
    pad = max((d_hi - d_lo) * 0.18, 0.04)
    d_lo, d_hi = d_lo - pad, d_hi + pad

    def x(v):
        return P["l"] + (v - d_lo) / (d_hi - d_lo) * pw

    y = P["t"] + (h - P["t"] - P["b"]) * 0.42
    clears = lo > 0 or hi < 0
    cls = "bg" if lo > 0 else "bb" if hi < 0 else "bm"
    axis_y = h - P["b"] + 6
    out = [
        f'<line x1="{P["l"]:.1f}" y1="{axis_y:.1f}" x2="{w - P["r"]:.1f}" '
        f'y2="{axis_y:.1f}" class="zero"/>',
        f'<line x1="{x(0):.1f}" y1="{P["t"] - 8:.1f}" x2="{x(0):.1f}" '
        f'y2="{axis_y + 6:.1f}" class="axis-zero"/>',
        f'<rect x="{x(lo):.1f}" y="{y - 16:.1f}" width="{max(x(hi) - x(lo), 3):.1f}" '
        f'height="32" class="{cls}" rx="7"/>',
        _txt(f"{lo:+.2f}%", x(lo), y - 24, "tk", "middle"),
        _txt(f"{hi:+.2f}%", x(hi), y + 34, "tk", "middle"),
    ]
    # Ticks, so the WIDTH of the interval is readable rather than merely shown.
    for i in range(5):
        v = d_lo + (d_hi - d_lo) * i / 4
        out.append(f'<line x1="{x(v):.1f}" y1="{axis_y:.1f}" x2="{x(v):.1f}" '
                   f'y2="{axis_y + 5:.1f}" class="zero"/>')
        out.append(_txt(f"{v:+.1f}%", x(v), axis_y + 22, "tk", "middle"))
    if obs is not None:
        out.append(f'<line x1="{x(obs):.1f}" y1="{y - 30:.1f}" x2="{x(obs):.1f}" '
                   f'y2="{y + 22:.1f}" class="dash"/>')
        out.append(_txt(f"observed {obs:+.2f}%", x(obs), y - 38, "lm", "middle"))
    out.append(_txt(
        "the interval clears zero, so the edge survives resampling" if clears
        else "the interval contains zero \u2014 on this many trades the edge is not "
             "distinguishable from luck",
        P["l"] - 14, P["t"] - 22, "cap"))
    out.append(_txt("mean return on one trade's margin", P["l"] - 14, h - 8, "tk"))
    return _svg(w, h, "".join(out), "bootstrap interval for return on margin")


def waterfall(payload, w=1180, h=380):
    """Gross edge, then what each cost took, then what was left. In points.

    Points rather than rupees on purpose: the lot size changed part-way through this
    window, so rupees mix a market fact with an exchange announcement.
    """
    cd = ((payload.get("honesty") or {}).get("cost_drag")) or {}
    g = cd.get("gross_points_before_costs")
    if g is None:
        return ""
    slip = float(cd.get("slippage_points") or 0)
    chg = float(cd.get("charges_points") or 0)
    net = float(cd.get("net_points_after_costs") or 0)
    g = float(g)
    steps = [("gross edge", g, 0.0, "ba"), ("slippage", -slip, g, "bb"),
             ("charges", -chg, g - slip, "bb"), ("net result", net, 0.0, "bg" if net >= 0 else "bb")]
    P = {"t": 40, "r": 24, "b": 46, "l": 78}
    pw, ph = w - P["l"] - P["r"], h - P["t"] - P["b"]
    tops = [g, g, g - slip, max(net, 0)]
    lo = min(0.0, net, g - slip - chg, g)
    hi = max(0.0, g, *tops)
    span = (hi - lo) or 1.0
    slot = pw / len(steps)
    bw = min(slot * 0.5, 120)
    def y(v):
        return P["t"] + (hi - v) / span * ph
    out = [_zero(P["l"], w - P["r"], y(0))]
    for i, (k, delta, base, cls) in enumerate(steps):
        cx = P["l"] + slot * (i + 0.5)
        top, bot = max(base, base + delta), min(base, base + delta)
        out.append(f'<rect x="{cx - bw / 2:.1f}" y="{y(top):.1f}" width="{bw:.1f}" '
                   f'height="{max(y(bot) - y(top), 1):.1f}" class="{cls}" rx="3"/>')
        out.append(_txt(f"{delta:+.1f}", cx, y(top) - 8,
                        "lg" if delta >= 0 else "lb", "middle"))
        out.append(_txt(k, cx, h - P["b"] + 20, "tk", "middle"))
    surv = cd.get("share_of_edge_surviving")
    note = (f"{surv * 100:.0f}% of the gross edge survived" if surv
            else "the gross edge was negative before costs were taken")
    out.append(_txt(note + " · points per lot", P["l"], P["t"] - 16, "cap"))
    return _svg(w, h, "".join(out), "where the gross edge went")


# ======================================================================== the position


def payoff(payload, w=1180, h=400):
    """The position's value at expiry across the index, drawn to scale.

    From the FIRST released trade's actual legs and actual fills, not from the spec: what
    the strategy asked for and what it got are different objects, and the report should
    show the one that traded.
    """
    trades = payload.get("trades") or []
    t = next((x for x in trades if (x.get("legs") or [])
              and all(l.get("strike") and l.get("entry_price") is not None
                      for l in x["legs"])), None)
    if not t:
        return ""
    legs = t["legs"]
    ks = [float(l["strike"]) for l in legs]
    spot = float(t.get("spot_at_entry") or (sum(ks) / len(ks)))
    lo_k, hi_k = min(min(ks), spot), max(max(ks), spot)
    pad = max((hi_k - lo_k) * 0.55, spot * 0.035)
    lo_s, hi_s = lo_k - pad, hi_k + pad

    def pnl(s):
        tot = 0.0
        for l in legs:
            k, q = float(l["strike"]), float(l.get("qty") or 1)
            prem = float(l["entry_price"])
            intr = max(0.0, s - k) if l.get("type") == "CE" else max(0.0, k - s)
            tot += q * ((prem - intr) if l.get("action") == "SELL" else (intr - prem))
        return tot

    N = 180
    xs = [lo_s + (hi_s - lo_s) * i / N for i in range(N + 1)]
    ys = [pnl(x) for x in xs]
    P = {"t": 40, "r": 26, "b": 46, "l": 78}
    pw, ph = w - P["l"] - P["r"], h - P["t"] - P["b"]
    ylo, yhi = min(min(ys), 0.0), max(max(ys), 0.0)
    span = (yhi - ylo) or 1.0
    def X(v):
        return P["l"] + (v - lo_s) / (hi_s - lo_s) * pw
    def Y(v):
        return P["t"] + (yhi - v) / span * ph
    pts = [(X(x), Y(y)) for x, y in zip(xs, ys)]
    zy = Y(0.0)
    out = [f'<path d="{_path(pts)} L{pts[-1][0]:.1f} {zy:.1f} L{pts[0][0]:.1f} '
           f'{zy:.1f} Z" class="ar"/>',
           f'<path d="{_path(pts)}" class="ln"/>', _zero(P["l"], w - P["r"], zy)]
    for l in legs:
        k = float(l["strike"])
        out.append(f'<line x1="{X(k):.1f}" y1="{P["t"] - 4:.1f}" x2="{X(k):.1f}" '
                   f'y2="{P["t"] + ph:.1f}" class="guide-dash dash"/>')
        out.append(_txt(f'{l.get("action", "")[0]}{l.get("type", "")} {k:.0f}',
                        X(k), P["t"] - 10, "tk", "middle"))
    out.append(f'<line x1="{X(spot):.1f}" y1="{P["t"]:.1f}" x2="{X(spot):.1f}" '
               f'y2="{P["t"] + ph:.1f}" class="axis-zero"/>')
    out.append(_txt(f"index {spot:,.0f} at entry", X(spot), h - P["b"] + 20,
                    "tk", "middle"))
    out += [_txt(f"{yhi:+.1f} pts", P["l"] - 8, P["t"] + 4, "tk", "end"),
            _txt(f"{ylo:+.1f} pts", P["l"] - 8, P["t"] + ph + 4, "tk", "end"),
            _txt(f"{lo_s:,.0f}", P["l"], h - P["b"] + 20, "tk"),
            _txt(f"{hi_s:,.0f}", w - P["r"], h - P["b"] + 20, "tk", "end"),
            _txt(f"trade {t.get('n')} at expiry · {len(legs)} legs · one lot, "
                 f"before charges", P["l"], P["t"] - 26, "cap")]
    return _svg(w, h, "".join(out), "payoff at expiry")


CATALOGUE = [
    ("equityUnderwater", "Cumulative profit and loss, with drawdown",
     equity_underwater, "wide"),
    ("tradeSequence", "Every trade in order", trade_sequence, "wide"),
    ("distribution", "Distribution of per-trade profit and loss", distribution, "wide"),
    ("romDistribution", "Return on the margin each trade blocked",
     rom_distribution, "half"),
    ("yearReturns", "Profit and loss by calendar year", year_returns, "wide"),
    ("folds", "Profit and loss per walk-forward fold", folds, "half"),
    ("oosSplit", "In sample against out of sample", oos_split, "half"),
    ("bootstrap", "The 95% interval around return on margin", bootstrap, "half"),
    ("rollingWin", "Rolling 30-trade win rate", rolling_win_rate, "wide"),
    ("waterfall", "Where the gross edge went", waterfall, "wide"),
    ("costCreep", "Gross edge against what survived costs", cost_creep, "wide"),
    ("costShare", "Costs as a share of gross, by year", cost_share, "half"),
    ("marginTimeline", "Margin blocked over time", margin_timeline, "wide"),
    ("marginOutcome", "Margin blocked against return on it", margin_vs_outcome, "half"),
    ("payoff", "The position at expiry, drawn to scale", payoff, "wide"),
    ("holdingTime", "How long positions were held", holding_time, "half"),
    ("scoreBreakdown", "How the health score was reached", score_breakdown, "half"),
]


def render_all(payload):
    """Every real-data chart this payload can support, keyed by id. A chart that has no
    data returns "" and is skipped by the caller -- a 51-trade backtest genuinely cannot
    draw a 30-trade rolling window, and an empty frame is worse than an absent one."""
    out = {}
    for cid, title, fn, width in CATALOGUE:
        try:
            svg = fn(payload, *( (1180, 420) if width == "wide" else (560, 360) ))
        except Exception as exc:                                   # noqa: BLE001
            svg = ""
            out.setdefault("_errors", {})[cid] = str(exc)
        if svg:
            out[cid] = {"title": title, "svg": svg, "width": width}
    # THE BUCKET CHARTS HAVE TO EARN THEIR PLACE. Every one of these strategies exits for
    # exactly one reason, so "P&L by how the trade ended" is a chart with a single bar on
    # it -- which looks like a rendering fault and tells the reader nothing they did not
    # already know from the rules card. A bucketed chart is drawn only where the buckets
    # actually differ; the one-bucket case is a STAT, and stats live elsewhere.
    for key, title in (("by_dte_at_entry", "Profit and loss by days to expiry at entry"),
                       ("by_weekday_of_entry", "Profit and loss by weekday of entry"),
                       ("by_exit_reason", "Profit and loss by how the trade ended")):
        if len((payload.get("breakdown") or {}).get(key) or {}) < 2:
            continue
        svg = by_bucket(payload, key, title)
        if svg:
            out[key] = {"title": title, "svg": svg, "width": "half"}
    return out
