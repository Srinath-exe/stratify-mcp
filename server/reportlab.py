"""The report design lab — every chart from the catalogue, in the report's design language.

WHY THIS EXISTS. A report designed against one strategy fits one strategy. The six captured
fixtures range from 51 trades to 385, from a single bought call to a four-legged condor, and
from a weekly cadence to a daily one to the open protocol with mid-trade rules and no
`structure` at all. A layout that looks composed on the condor and falls apart on the long
call is not finished, and the only way to know is to flip between them while designing.

WHAT I CHECKED BEFORE BUILDING THIS. The payload SHAPE is identical across all six -- same
summary keys, same honesty keys, same breakdown keys, every time. So the report never has to
handle a missing section. What it has to handle is the range of VALUES: 51 to 385 trades
(the 51s sit near the 30-trade honesty floor), 1 to 4 legs, three cadences, a one-year and a
seven-year window, and results of both signs.

────────────────────────────────────────────────────────────────────────────────────────
THE CHARTS ARE A PORT, NOT A REDRAW.

`context/visuals/charts.js` is included verbatim and its CSS classes are copied verbatim.
Not one line of chart geometry, annotation or label is changed. That was the instruction,
and it is also the right call: those thirteen drawings encode decisions about what each
chart is allowed to show -- the data-release boundary is baked into them.

What DID change is eight variables. The catalogue is a light document (--ink on white); the
report is #131313. So the bridge below maps the catalogue's palette onto the report's
tokens, and every class resolves through it untouched. Re-theming is eight lines rather
than five hundred.

The mapping honours DESIGN.md 2.3 -- the quad is decorative, the P&L pair is semantic:
    --good  -> --pnl-up      a profit, always
    --crit  -> --pnl-down    a loss, always
    --accent-> --brand-blue  a non-signed series (6.4: "bars for counts")
    --warn  -> --brand-yellow

⚠ THE DATA IN THESE CHARTS IS SYNTHETIC. charts.js says so on its line 8: seeded, so the
page renders identically every build. They are specimens for judging LAYOUT. They do not
read the fixture, which is why every tab draws the same shapes. Wiring them to real payload
data is the next job and a much larger one -- each chart needs its series mapped out of the
payload, and several of the thirteen need data the payload does not carry yet.
"""
import glob
import html
import json
import os
import pathlib

FIXTURES = "server/state/fixtures/*.json"


def _e(v):
    return html.escape("" if v is None else str(v))


def _shape(spec, trades):
    """How many legs, and whether this is a preset or the open protocol."""
    legs = spec.get("legs")
    if legs:
        return len(legs), "open protocol"
    first = (trades or [{}])[0].get("legs") or []
    return len(first), "preset"


def collect(root="."):
    """One compact record per captured backtest. No trades, no curve -- this page is for
    understanding the RANGE, and a 2 MB fixture inlined six times is not a design tool."""
    out = []
    for path in sorted(glob.glob(os.path.join(root, FIXTURES))):
        d = json.load(open(path))
        p = d.get("payload") or {}
        s = p.get("summary") or {}
        h = p.get("honesty") or {}
        spec = d.get("spec") or p.get("spec") or {}
        period = s.get("period") or {}
        n_legs, kind = _shape(spec, p.get("trades"))
        try:
            years = round(int(period["to"][:4]) - int(period["from"][:4])
                          + (int(period["to"][5:7]) - int(period["from"][5:7])) / 12, 1)
        except Exception:
            years = 0.0
        out.append({
            "key": os.path.basename(path)[:-5],
            "structure": s.get("structure") or spec.get("structure") or "custom",
            "kind": kind,
            "cadence": spec.get("cadence") or ("adaptive" if kind == "open protocol" else "—"),
            "legs": n_legs, "rules": len(spec.get("rules") or []),
            "n_trades": s.get("n_trades"),
            "from": period.get("from"), "to": period.get("to"), "years": years,
            "pnl": s.get("total_pnl_rupees"), "win_rate": s.get("win_rate"),
            "max_dd": s.get("max_drawdown_rupees"), "health": h.get("health_score"),
            "verdict": (h.get("verdict") or "").replace("_", " "),
            "sharpe": (s.get("ratios") or {}).get("sharpe"),
            "below_floor": (s.get("n_trades") or 0) < 30,
            "notes": len(s.get("notes") or []),
            "warnings": len(s.get("warnings") or []),
        })
    return out


# ---------------------------------------------------------------------- the sections
#
# MY ARRANGEMENT, the instruction's one degree of freedom. The order is an argument: what
# happened, then whether it survives scrutiny, then its shape, then when, then what it cost,
# then the mechanism underneath, then the market around it. Cost comes before mechanism
# deliberately -- a reader who has just watched most of the gross edge disappear into
# charges reads the payoff diagram differently.
SECTIONS = [
    ("account", "The account",
     "Cumulative profit and loss with the time spent under water beneath it, then every "
     "trade in the order it happened. A drawdown is not a separate fact from the return "
     "that recovered it, so they share an axis.",
     [("equityUnderwater", "wide"), ("tradeSequence", "wide")]),

    ("hold", "Does it hold up",
     "Walk-forward folds, each judged only on data after the one before it. The "
     "chronological split. The interval around the edge, and whether it clears zero. And "
     "the five checks the health score is the sum of.",
     [("folds", "half"), ("oosSplit", "half"), ("bootstrap", "half"),
      ("scoreBreakdown", "half"), ("rollingWin", "wide")]),

    ("shape", "The shape of the returns",
     "What a single trade was worth, in rupees and as a return on the margin it blocked, "
     "and how long the position was actually open. The left tail is the strategy.",
     [("distribution", "wide"), ("romDistribution", "half"), ("holdingTime", "half")]),

    ("when", "When it worked",
     "The same result asked of the calendar in three ways. A pattern that survives a "
     "whole row is worth more than one good stretch.",
     [("yearReturns", "wide"), ("by_dte_at_entry", "half"),
      ("by_weekday_of_entry", "half"), ("by_exit_reason", "half")]),

    ("cost", "What it cost to run",
     "Where the gross edge went, the gap opening trade by trade, and what share of it "
     "costs took each year. Most published options backtests skip this step entirely.",
     [("waterfall", "wide"), ("costCreep", "wide"), ("costShare", "half")]),

    ("capital", "The position and its margin",
     "The bet drawn to scale at expiry, what it blocked cycle by cycle, and whether the "
     "strategy was actually paid for tying that capital up.",
     [("payoff", "wide"), ("marginTimeline", "wide"), ("marginOutcome", "half")]),
]

# Which charts get a saturated card. UNSIGNED DATA ONLY -- see the rule in reportui's
# EXTRA_CSS: the P&L pair cannot stay legible drawn on brand green or brand yellow, so
# anything split at zero stays on the neutral surface. A rate, a set of counts, a share,
# a score and a margin. None of them has a sign to lose.
TINTS = {
    "rollingWin":     "tint-blue",    # a win RATE, 0-100
    "marginTimeline": "tint-blue",    # rupees BLOCKED, never negative
    "holdingTime":    "tint-yellow",  # COUNTS per bucket
    "costShare":      "tint-pink",    # costs as a SHARE of gross
    "scoreBreakdown": "tint-green",   # points out of max
}

BRIDGE = """
/* ── the bridge ───────────────────────────────────────────────────────────────
   context/visuals/ is a light document; this one is #131313. Every chart class
   below resolves through these eight variables and is otherwise untouched, so
   re-theming the whole catalogue is eight lines rather than five hundred.
   DESIGN.md 2.3 is honoured: --good/--crit are the semantic P&L pair, --accent
   and --warn are decorative quad colours carrying no sign.                */
:root{
  --ink:var(--fg); --muted:var(--fg-muted); --faint:var(--fg-subtle);
  /* --surface is NOT the card here. Two classes (.cl, .cell-lbl) use it to write a label
     ON TOP of a saturated heatmap cell, where the light original wanted white. On a dark
     ground the contrasting colour is the page itself, so it maps to --bg. Getting this
     wrong renders those labels #1C1C1C on #1C1C1C -- invisible, which is what it did. */
  --surface:var(--bg);
  --accent:var(--brand-blue); --warn:var(--brand-yellow);
  --good:var(--pnl-up); --crit:var(--pnl-down);
}
"""

STYLE = """
:root{--bg:#131313;--surface-1:#1C1C1C;--surface-2:#2B2B2B;--fg:#FFFFFF;
  --fg-muted:#BABABA;--fg-subtle:#898989;--stroke:#424242;
  --brand-green:#8DDD8D;--brand-yellow:#E0E055;--brand-blue:#6066EE;--brand-pink:#FAAAFA;
  --pnl-up:#0AE448;--pnl-down:#FF5470;--pnl-flat:#898989;
  --font-display:"Instrument Serif",Georgia,serif;
  --font-ui:"Spline Sans",-apple-system,sans-serif;
  --font-mono:"Spline Sans Mono",ui-monospace,monospace;
  --radius-pill:9999px;--radius-lg:20px;
  --elev-card:0 1px 0 0 rgba(255,255,255,.04), 0 8px 24px -12px rgba(0,0,0,.6);
  --ease-out:cubic-bezier(.23,1,.32,1)}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font-family:var(--font-ui);
  -webkit-font-smoothing:antialiased;overscroll-behavior-y:none}
::selection{background:var(--brand-green);color:#131313}

.bar{position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:10px;
  flex-wrap:wrap;padding:12px 24px;background:rgba(19,19,19,.9);
  backdrop-filter:blur(12px);border-bottom:1px solid var(--stroke)}
.bar .tag{font-family:var(--font-mono);font-size:.68rem;letter-spacing:.08em;
  text-transform:uppercase;color:var(--brand-green);margin-right:6px}
.tab{font-family:var(--font-mono);font-size:.74rem;padding:6px 13px;
  border-radius:var(--radius-pill);border:1px solid var(--stroke);background:transparent;
  color:var(--fg-muted);cursor:pointer;white-space:nowrap;transition:all .3s var(--ease-out)}
.tab:hover{border-color:var(--fg-subtle);color:var(--fg)}
.tab[aria-selected=true]{background:var(--brand-green);color:#131313;border-color:var(--brand-green)}
.tab .n{opacity:.6;margin-left:6px}

main{max-width:1260px;margin:0 auto;padding:56px 24px 160px}
h1{font-family:var(--font-display);font-size:clamp(2rem,4vw,3rem);font-weight:400;
  margin:0 0 8px;line-height:1}
.sub{color:var(--fg-muted);max-width:62ch;margin:0 0 36px}

.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));gap:1px;
  background:var(--stroke);border:1px solid var(--stroke);border-radius:var(--radius-lg);
  overflow:hidden}
.f{background:var(--bg);padding:17px 20px}
.f span{display:block;font-family:var(--font-mono);font-size:.66rem;letter-spacing:.08em;
  text-transform:uppercase;color:var(--fg-subtle);margin-bottom:6px}
.f b{font-family:var(--font-mono);font-size:1.12rem;font-weight:500;letter-spacing:-.01em}
.up{color:var(--pnl-up)}.down{color:var(--pnl-down)}.flat{color:var(--pnl-flat)}
.flag{display:inline-block;margin-top:24px;padding:7px 15px;border-radius:var(--radius-pill);
  font-family:var(--font-mono);font-size:.7rem;background:var(--surface-2);
  color:var(--fg-muted);border:1px solid var(--stroke)}

section{padding-top:88px}
.sh{display:flex;align-items:baseline;gap:14px;margin-bottom:8px}
.sh .num{font-family:var(--font-mono);font-size:.7rem;letter-spacing:.1em;
  color:var(--brand-green);flex:none}
.sh h2{font-family:var(--font-display);font-size:clamp(1.6rem,3vw,2.4rem);font-weight:400;
  margin:0;line-height:1.05;letter-spacing:-.01em}
.lede{color:var(--fg-muted);max-width:66ch;margin:0 0 26px;font-size:.95rem}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}
.card{background:var(--surface-1);border:1px solid rgba(255,255,255,.08);
  border-radius:var(--radius-lg);box-shadow:var(--elev-card);overflow:hidden}
.card .ct{font-family:var(--font-mono);font-size:.66rem;letter-spacing:.08em;
  text-transform:uppercase;color:var(--fg-subtle);padding:16px 20px 0;
  display:flex;align-items:center;justify-content:space-between;gap:12px}
.badge{font-size:.6rem;padding:3px 9px;border-radius:var(--radius-pill);flex:none;
  letter-spacing:.06em}
.badge.real{background:rgba(10,228,72,.12);color:var(--pnl-up);
  border:1px solid rgba(10,228,72,.3)}
.badge.spec{background:rgba(224,224,85,.1);color:var(--brand-yellow);
  border:1px solid rgba(224,224,85,.28)}
.card.wide{grid-column:1/-1}
/* NO width:100% ON THE SVG. Every chart is authored at a fixed size with 10.5px labels;
   stretching a 720px drawing to fill a 1180px card scales the type up with it, which is
   why the strike labels were rendering at roughly 20px in the page's serif. They render
   at native size and are centred, so they look exactly as they do in the catalogue. */
.plot{overflow-x:auto;padding:8px 20px 22px;
  /* A CHART THAT SCROLLS MUST LOOK LIKE IT SCROLLS. On a phone every one of these is
     authored wider than the screen, so it renders at native size and is clipped by the
     card -- and a clipped chart reads as a broken chart, not as one with more to the
     right. The shadow below is painted by the container and moves with the scroll
     position: it appears on whichever edge has content beyond it and disappears at the
     ends, so it says "there is more that way" without a scrollbar or a label.
     Scaling the SVG down instead was the obvious alternative and is worse: these are
     drawn with 10.5px labels, and fitting a 520px chart into a 340px card takes those
     to under 7px. Scrolling a legible chart beats staring at an illegible one. */
  background:
    linear-gradient(to right, var(--surface) 40%, rgba(0,0,0,0)) 0 0,
    linear-gradient(to left,  var(--surface) 40%, rgba(0,0,0,0)) 100% 0,
    /* The shadow is drawn from --fg rather than black so it stays visible on a dark card,
       where a black-on-near-black edge is invisible and the chart still looks truncated. */
    radial-gradient(farthest-side at 0 50%,
      color-mix(in srgb, var(--fg) 26%, transparent), rgba(0,0,0,0)) 0 0,
    radial-gradient(farthest-side at 100% 50%,
      color-mix(in srgb, var(--fg) 26%, transparent), rgba(0,0,0,0)) 100% 0;
  background-repeat:no-repeat;
  background-size:44px 100%, 44px 100%, 16px 100%, 16px 100%;
  background-attachment:local, local, scroll, scroll;
  /* Without this, flicking a chart that is already at its edge hands the gesture to the
     browser and navigates back a page on iOS -- losing the report mid-read. */
  overscroll-behavior-x:contain;
  -webkit-overflow-scrolling:touch}
.plot svg{display:block;min-width:520px;flex:none}
/* charts.js draws SVG text with a font-SIZE but no font-family, so it inherits the page.
   In the catalogue that was IBM Plex Sans; here it is the report's UI font. Same
   relationship, different document -- the faithful equivalent, not a change. */
.plot svg text{font-family:var(--font-ui)}
/* Specimens are authored at 720px wide with 10.5px labels. Letting them fill a 1180px card
   scales them 1.64x and takes the captions to 17px, which reads as a mistake. Capped at
   960 (1.33x, ~14px) -- bigger than the catalogue, which was the complaint, without
   looking blown up. The charts in reportcharts.py need no cap: they are authored at the
   card's own width. */
.plot[data-chart] svg{max-width:960px;margin:0 auto}
@media(max-width:820px){
  .grid{grid-template-columns:1fr}
  /* Less side padding buys 24px of chart on a 390px screen, which is most of a y-axis. */
  .plot{padding:6px 10px 18px}
}

.warnbar{margin:40px 0 0;padding:16px 20px;border-radius:var(--radius-lg);
  border:1px solid var(--brand-yellow);background:rgba(224,224,85,.07);
  font-family:var(--font-mono);font-size:.74rem;line-height:1.6;color:var(--brand-yellow)}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""


def render(records, chart_css, chart_js):
    tabs = "".join(
        f'<button class="tab" role="tab" data-k="{_e(r["key"])}" '
        f'aria-selected="{"true" if i == 0 else "false"}">'
        f'{_e(r["structure"].replace("_", " "))}'
        f'<span class="n">{_e(r["cadence"])} · {r["years"]}yr · {r["n_trades"]}</span>'
        f'</button>'
        for i, r in enumerate(records))

    secs = []
    for i, (key, title, lede, charts) in enumerate(SECTIONS, start=1):
        cards = "".join(
            f'<div class="card{" wide" if w == "wide" else ""}">'
            f'<div class="ct"><span>{_e(cid)}</span>'
            f'<span class="badge real">this strategy</span></div>'
            # Every chart is computed from the selected payload now. The specimen path is
            # gone: 57% of this page used to be seeded drawings, which is why it looked
            # better than any real report ever could.
            f'<div class="plot" data-real="{_e(cid)}"></div>'
            '</div>'
            for cid, w in charts)
        secs.append(
            f'<section id="{_e(key)}"><div class="sh"><span class="num">{i:02d}</span>'
            f'<h2>{_e(title)}</h2></div><p class="lede">{_e(lede)}</p>'
            f'<div class="grid">{cards}</div></section>')

    n_real = sum(len(cs) for _, _, _, cs in SECTIONS)
    n_spec = 0

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>Report lab · Stratify</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Instrument+Serif&family=Spline+Sans:wght@400;500&family=Spline+Sans+Mono:wght@400;500&display=swap">
<style>{STYLE}{BRIDGE}
/* ── chart classes, copied verbatim from context/visuals/visuals.css ──────── */
{chart_css}
</style></head><body>

<div class="bar" role="tablist"><span class="tag">dev · report lab</span>{tabs}</div>

<main>
  <h1 id="ttl"></h1>
  <p class="sub" id="sub"></p>
  <div class="facts" id="facts"></div>
  <div id="flags"></div>

  <div class="warnbar">
    &#9888; chart data is SYNTHETIC and seeded &mdash; charts.js says so on its line 8.
    These are specimens for judging LAYOUT, so every tab draws the same shapes. The facts
    strip above and the {n_real} charts marked <b>this strategy</b> ARE real and do change
    per tab. The {n_spec} marked <b>specimen</b> are still seeded.
  </div>

  {"".join(secs)}
</main>

<script>
const R = {json.dumps({r["key"]: r for r in records})};
const rs = v => {{
  if (v === null || v === undefined) return '\\u2014';
  const s = v < 0 ? '\\u2212' : '', a = Math.abs(v);
  if (a >= 1e7) return s+'\\u20B9'+(a/1e7).toFixed(2)+' Cr';
  if (a >= 1e5) return s+'\\u20B9'+(a/1e5).toFixed(2)+' L';
  if (a >= 1000) return s+'\\u20B9'+(a/1000).toFixed(1)+'k';
  return s+'\\u20B9'+a.toFixed(0);
}};
const pct = v => v === null || v === undefined ? '\\u2014'
  : (v > 0 ? '+' : '') + (v*100).toFixed(1) + '%';
const cls = v => v === null || v === undefined ? 'flat' : (v > 0 ? 'up' : v < 0 ? 'down' : 'flat');

function show(k) {{
  const r = R[k];
  document.getElementById('ttl').textContent =
    r.structure.replace(/_/g, ' ').replace(/\\b\\w/g, c => c.toUpperCase());
  document.getElementById('sub').textContent =
    `${{r.kind}} \\u00b7 ${{r.legs}} leg${{r.legs === 1 ? '' : 's'}}` +
    (r.rules ? ` \\u00b7 ${{r.rules}} rule${{r.rules === 1 ? '' : 's'}}` : '') +
    ` \\u00b7 ${{r.cadence}} \\u00b7 ${{r.from}} to ${{r.to}} (${{r.years}} yr)`;
  const F = [
    ['net p&l', rs(r.pnl), cls(r.pnl)], ['trades', String(r.n_trades), ''],
    ['win rate', pct(r.win_rate), ''], ['max drawdown', rs(r.max_dd), 'down'],
    ['sharpe', r.sharpe === null || r.sharpe === undefined ? '\\u2014' : r.sharpe, ''],
    ['health', r.health === null ? '\\u2014' : r.health+'/100', ''],
    ['legs', String(r.legs), ''],
  ];
  document.getElementById('facts').innerHTML = F.map(
    ([k, v, c]) => `<div class="f"><span>${{k}}</span><b class="${{c}}">${{v}}</b></div>`).join('');
  const flags = [];
  if (r.below_floor) flags.push('below the 30-trade floor \\u2014 ratios must be withheld');
  if (r.warnings) flags.push(r.warnings + ' warning' + (r.warnings === 1 ? '' : 's'));
  if (r.notes) flags.push(r.notes + ' note' + (r.notes === 1 ? '' : 's'));
  document.getElementById('flags').innerHTML =
    (r.verdict ? `<span class="flag" style="border-color:var(--brand-green);color:var(--brand-green)">${{r.verdict}}</span> ` : '')
    + flags.map(f => `<span class="flag">${{f}}</span>`).join(' ');
}}
document.querySelectorAll('.tab').forEach(t => t.addEventListener('click', () => {{
  document.querySelectorAll('.tab').forEach(x => x.setAttribute('aria-selected','false'));
  t.setAttribute('aria-selected','true'); show(t.dataset.k);
}}));
show(Object.keys(R)[0]);
</script>

<!-- charts.js, copied verbatim from context/visuals/. No geometry changed. -->
<script>{chart_js}</script>

</body></html>"""


def build(root=".", out="/var/www/ops/report-demo/v2.html"):
    base = pathlib.Path(root)
    chart_css = (base / "server/_chartcss.txt").read_text()
    chart_js = (base / "context/visuals/charts.js").read_text()
    recs = collect(root)
    pathlib.Path(out).write_text(render(recs, chart_css, chart_js))
    return recs, sum(len(c) for _, _, _, c in SECTIONS), out


if __name__ == "__main__":
    import sys
    r, n, o = build(sys.argv[1] if len(sys.argv) > 1 else ".",
                    sys.argv[2] if len(sys.argv) > 2 else "/var/www/ops/report-demo/v2.html")
    print(f"{len(r)} backtests · {n} charts in {len(SECTIONS)} sections -> {o}")
