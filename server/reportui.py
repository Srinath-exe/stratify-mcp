"""The backtest report, v2 — one real payload, in the inspo design language.

This is what `/r/{token}` serves. Same design system as the report lab, but driven by a
single stored result rather than by a tab switcher, and every number on it comes from that
result.

EVERY DRAWING ON THIS PAGE IS COMPUTED FROM THIS PAYLOAD. That was not true until
2026-08-31: 13 of the 23 charts were seeded specimens from the catalogue, badged
"specimen" but drawn beautifully, and they were most of the reason the page looked better
in the lab than it ever did with a real strategy on it. Six of them turned out to be
computable and were wired (the equity curve among them -- the single most important
drawing in the report was invented). The rest were removed: an excursion scatter needs
MAE/MFE the payload does not carry, an intraday P&L timeline needs a mark path, and greeks
and expected move need a solved IV surface the engine deliberately does not return.

A chart that cannot say anything is now dropped rather than framed empty, and a section
left with nothing goes with it. The badge is gone because there is no longer a distinction
to draw.

ONE LOT, ALWAYS. This page renders the stored payload, which the engine computes unsized.
The sized view lives behind `build_report`, and the two can disagree in SIGN through a deep
drawdown. The cover says so rather than leaving a reader to discover it.
"""
import html
import json

from . import (capitalview, design, reportcharts, reportlab, rulecard,  # noqa: F401
               sizing)


def _e(v):
    return html.escape("" if v is None else str(v))


info = design.info


def _rs(v):
    if v is None:
        return "—"
    s = "−" if v < 0 else ""
    a = abs(v)
    if a >= 1e7:
        return f"{s}₹{a / 1e7:.2f} Cr"
    if a >= 1e5:
        return f"{s}₹{a / 1e5:.2f} L"
    if a >= 1000:
        return f"{s}₹{a / 1000:.1f}k"
    return f"{s}₹{a:,.0f}"


def _pct(v, dp=1):
    return "—" if v is None else f"{'+' if v > 0 else ''}{v * 100:.{dp}f}%"


def _cls(v):
    return "flat" if v is None else ("up" if v > 0 else "down" if v < 0 else "flat")


def _title(spec, summary):
    st = (summary.get("structure") or spec.get("structure") or "").replace("_", " ")
    if st:
        return st.title()
    n = len(spec.get("legs") or [])
    return f"{n}-leg custom position" if n else "Custom strategy"


EXTRA_CSS = """
/* ── DESIGN.md §0, the part I got wrong first time ────────────────────────────
   "Saturated colour is used as a whole surface, never as an accent line. A card
   is ENTIRELY blue with black text on it. Colour is never a 2px border or a
   small icon tint." The first pass used the quad as hairline borders and 10px
   badges on a flat ground, which is exactly the failure that rule describes.
   Also: body weight 300 not 400, and section padding at 100-150px so content
   occupies about 45% of a viewport rather than filling it.               */

body{font-weight:300;
  /* Near-black canvas, not black -- with a little atmosphere in it. A perfectly
     flat #131313 is what made the page read as cheap next to the reference. */
  background:
    radial-gradient(1100px 700px at 82% -8%, rgba(96,102,238,.16), transparent 62%),
    radial-gradient(900px 600px at 6% 22%, rgba(141,221,141,.07), transparent 58%),
    var(--bg);
  background-attachment:fixed}

main{max-width:1260px;margin:0 auto;padding:0 24px 200px}

.rbrand{position:absolute;top:28px;left:0;display:inline-flex;align-items:center;gap:9px;
  font-family:var(--font-display);font-size:1.2rem;color:var(--fg);text-decoration:none}
.rbrand .rmark{width:28px;height:28px;display:block;background-size:contain;background-repeat:no-repeat}
/* ── cover ─────────────────────────────────────────────────────────────────── */
.cover{padding:120px 0 40px;position:relative}
.cover .eyebrow{color:var(--brand-green)}
.cover h1{font-family:var(--font-display);font-size:clamp(3rem,7vw,5.5rem);font-weight:400;
  line-height:.95;letter-spacing:-.02em;margin:22px 0 0;max-width:16ch}
.cover .said{max-width:54ch;margin:26px 0 0;color:var(--fg-muted);font-size:1.15rem;
  font-weight:300;line-height:1.5}
.cover .unit{max-width:56ch;margin:18px 0 0;color:var(--fg-subtle);font-size:.85rem}

/* The verdict is a whole SURFACE, not a tinted outline. Black text on saturated
   colour, carrying the moulded-plastic shadow that makes it read as an object. */
.verdict{display:inline-flex;align-items:center;gap:12px;margin-top:34px;
  padding:15px 30px 15px 64px;border-radius:var(--radius-pill);position:relative;
  font-family:var(--font-mono);font-size:.86rem;font-weight:500;color:#131313;
  box-shadow:2px 4px 8px 0 rgba(0,0,0,.5), inset -1px 2px 6px 0 rgba(255,255,255,.5)}
.verdict .tok{position:absolute;left:7px;top:50%;transform:translateY(-50%);
  width:44px;height:44px;border-radius:50%;background:#131313;color:#fff;
  display:grid;place-items:center;font-size:.9rem;
  box-shadow:inset 2px 4px 8px 0 rgba(0,0,0,.5)}
.v-good{background:var(--brand-green)}
.v-mid{background:var(--brand-yellow)}
.v-bad{background:var(--brand-pink)}

/* ── the headline number, as an object ─────────────────────────────────────── */
.hero-grid{display:grid;grid-template-columns:minmax(300px,1fr) 2fr;gap:20px;
  align-items:stretch;margin-top:64px}
.lozenge-stat{border-radius:var(--radius-lg);padding:36px 38px;color:#131313;
  display:flex;flex-direction:column;justify-content:space-between;
  box-shadow:2px 4px 8px 0 rgba(0,0,0,.5), inset -1px 2px 6px 0 rgba(255,255,255,.5)}
.lozenge-stat .k{font-family:var(--font-mono);font-size:.7rem;letter-spacing:.1em;
  text-transform:uppercase;opacity:.62}
.lozenge-stat .v{font-family:var(--font-mono);font-size:clamp(2.4rem,4.4vw,3.4rem);
  line-height:1;letter-spacing:-.02em;font-weight:500;margin-top:16px}
.lozenge-stat .s{font-size:.84rem;opacity:.66;margin-top:10px;font-weight:400}
.ls-up{background:var(--brand-green)} .ls-down{background:var(--brand-pink)}
.ls-flat{background:var(--surface-2);color:var(--fg)}

.facts{grid-template-columns:repeat(3,minmax(0,1fr));
  border-radius:var(--radius-lg)}

/* ── the info icon ─────────────────────────────────────────────────────────────
   Small, quiet, and the same everywhere. It is a SURFACE when active -- filled,
   dark text -- rather than a tinted outline, per DESIGN.md 0.7.               */
.info{width:18px;height:18px;flex:none;border-radius:50%;padding:0;
  border:1px solid var(--stroke);background:transparent;color:var(--fg-subtle);
  font-family:var(--font-mono);font-size:.62rem;line-height:1;cursor:help;
  display:inline-grid;place-items:center;vertical-align:middle;
  transition:background .18s var(--ease-out),color .18s var(--ease-out),
             border-color .18s var(--ease-out)}
.info:hover,.info:focus-visible,.info.on{background:var(--fg);color:var(--bg);
  border-color:var(--fg);outline:none}
/* A THUMB IS NOT A CURSOR. The icon reads best at 18px and a finger needs about 44, so the
   hit area is grown with a pseudo-element instead of the button -- the target gets bigger
   without the dot getting fatter or the row it sits in getting taller. */
.info::after{content:"";position:absolute;left:50%;top:50%;
  width:44px;height:44px;transform:translate(-50%,-50%)}
.info{position:relative}
.info-pop{position:fixed;z-index:90;max-width:min(360px,calc(100vw - 24px));
  background:#0A0A0A;
  border-radius:14px;padding:17px 19px;pointer-events:none;opacity:0;
  transform:translateY(6px);transition:opacity .16s var(--ease-out),
  transform .16s var(--ease-out);
  box-shadow:0 18px 44px rgba(0,0,0,.75), 0 0 0 1px var(--stroke)}
.info-pop.on{opacity:1;transform:none}
.info-pop b{display:block;font-family:var(--font-mono);font-size:.62rem;
  letter-spacing:.1em;text-transform:uppercase;color:var(--brand-green);
  margin-bottom:10px}
.info-pop p{margin:0;font-size:.86rem;font-weight:300;line-height:1.6;
  color:var(--fg-muted)}
@media print{.info,.info-pop{display:none}}

/* ── the rules strip: the first thing on the page ──────────────────────────────
   Before any number. A report that opens with a P&L figure asks to be trusted
   before the reader knows what produced it; opening with the rules means every
   figure below is read as a consequence of something already seen.
   A column per criterion, and a criterion with nothing in it says "none" rather
   than disappearing -- "this strategy has no stop" is one of the most important
   facts on the page and it cannot be conveyed by an absence.               */
.rules{border:1px solid var(--stroke);border-radius:var(--radius-lg);overflow:hidden;
  margin-top:44px;background:var(--stroke);display:flex;flex-direction:column;gap:1px}
/* The position gets its own full-width row. Six equal columns do not fit the measure,
   and a leg list is the one element here whose length is unbounded -- twelve legs in a
   215px column wrap every line. Horizontal chips take 1 or 12 equally well. */
.rpos{background:var(--surface-1);padding:24px 26px}
.rlegs{display:flex;flex-wrap:wrap;gap:9px;margin:0;padding:0}
.rlegs li{list-style:none;display:flex;align-items:center;gap:9px;padding:8px 15px 8px 8px;
  border-radius:var(--radius-pill);background:var(--surface-2);
  font-family:var(--font-mono);font-size:.8rem;white-space:nowrap}
.rlegs .x{color:var(--fg-subtle)}
.rgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(196px,1fr));gap:1px;
  background:var(--stroke)}
.rl{background:var(--surface-1);padding:24px 24px 26px;display:flex;flex-direction:column}
.rk{font-family:var(--font-mono);font-size:.62rem;letter-spacing:.1em;text-transform:uppercase;
  color:var(--fg-subtle);margin-bottom:14px}
.rv{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:13px}
.rv li{font-size:.88rem;font-weight:300;line-height:1.45;color:var(--fg)}
.rv li.none{color:var(--fg-subtle);font-family:var(--font-mono);font-size:.78rem;
  line-height:1.55}
.rv .hl{display:block;font-family:var(--font-mono);font-size:.95rem;font-weight:500;
  margin-bottom:4px;letter-spacing:-.01em}
.rl.tgt .hl{color:var(--pnl-up)} .rl.stp .hl{color:var(--pnl-down)}
/* A side is a small SURFACE, not a tinted word -- DESIGN.md 0.7. */
.side{display:inline-block;font-family:var(--font-mono);font-size:.6rem;letter-spacing:.06em;
  padding:4px 9px;border-radius:var(--radius-pill);font-weight:500;
  box-shadow:inset -1px 1px 3px 0 rgba(255,255,255,.35)}
.side.sell{background:var(--brand-yellow);color:#131313}
.side.buy{background:var(--brand-blue);color:#fff}
.cv-h{font-family:var(--font-display);font-size:clamp(1.7rem,2.8vw,2.3rem);font-weight:400;
  margin:80px 0 14px;letter-spacing:-.015em;display:flex;align-items:center;gap:14px}

/* ── sections ──────────────────────────────────────────────────────────────── */
section{padding-top:118px}
/* The account view opens straight under the rules strip, so it does not need the full
   section gap above it -- the strip is already a hard visual break. */
section.acct{padding-top:64px}
.sh{display:flex;align-items:center;gap:18px;margin-bottom:14px}
.sh .info{margin-left:-4px}
.sh .num{font-family:var(--font-mono);font-size:.76rem;letter-spacing:.06em;
  color:#131313;flex:none;width:44px;height:44px;border-radius:50%;
  display:grid;place-items:center;font-weight:500;
  box-shadow:2px 4px 8px 0 rgba(0,0,0,.5), inset -1px 2px 6px 0 rgba(255,255,255,.5)}
.q0 .num{background:var(--brand-green)} .q1 .num{background:var(--brand-yellow)}
.q2 .num{background:var(--brand-blue);color:#fff} .q3 .num{background:var(--brand-pink)}
.sh h2{font-family:var(--font-display);font-size:clamp(2rem,3.6vw,2.9rem);font-weight:400;
  margin:0;line-height:1;letter-spacing:-.015em}
.lede{color:var(--fg-muted);max-width:62ch;margin:0 0 40px 62px;font-size:1rem;
  font-weight:300;line-height:1.55}
.card{border-radius:var(--radius-lg);background:var(--surface-1);
  border:1px solid rgba(255,255,255,.07)}

/* -- tinted cards: colour as a whole surface, per DESIGN.md section 0.7 ------
   THE RULE THAT DECIDES WHICH CHARTS GET ONE. A saturated card can only carry
   UNSIGNED data. The P&L pair is semantic and must stay legible wherever it
   appears, and neither #0AE448 nor #FF5470 survives being drawn on brand green
   or brand yellow -- so a signed chart on a tinted card would either become
   unreadable or quietly stop using the semantic colours. Counts, rates and
   shares carry no sign, so they can live on colour; equity, P&L and anything
   split at zero stays on --surface-1 where the pair reads properly.

   Inside a tint the chart variables flip to dark-on-colour. That is the whole
   reason the bridge exists: one block re-points every class in charts.css. */
.card.tint{border:0}
.card.tint .ct{color:rgba(19,19,19,.62)}
.card.tint .badge.real{background:rgba(19,19,19,.14);color:#131313;
  border-color:rgba(19,19,19,.3)}
.card.tint{
  --ink:#131313; --muted:rgba(19,19,19,.74); --faint:rgba(19,19,19,.52);
  --accent:#131313; --surface:rgba(255,255,255,.92);
  --good:#0B6B2E; --crit:#8E1D33; --warn:#6B5A12;
}
.tint-green{background:var(--brand-green)}
.tint-yellow{background:var(--brand-yellow)}
.tint-pink{background:var(--brand-pink)}
.tint-blue{background:var(--brand-blue);
  --ink:#fff; --muted:rgba(255,255,255,.82); --faint:rgba(255,255,255,.6);
  --accent:#fff; --surface:rgba(0,0,0,.6)}
.tint-blue .ct{color:rgba(255,255,255,.68)}
.tint-blue .badge.real{background:rgba(255,255,255,.16);color:#fff;
  border-color:rgba(255,255,255,.32)}

/* ── evidence ──────────────────────────────────────────────────────────────── */
.sec-h{font-family:var(--font-display);font-size:clamp(2rem,3.6vw,2.9rem);font-weight:400;
  margin:150px 0 26px;letter-spacing:-.015em;display:flex;align-items:center;gap:16px}
.sec-h .info,.cv-h .info{transform:translateY(-.35em)}
.checks{border:1px solid var(--stroke);border-radius:var(--radius-lg);overflow:hidden;
  background:var(--surface-1)}
.chk{display:grid;grid-template-columns:210px 92px 1fr;gap:20px;padding:20px 26px;
  border-bottom:1px solid var(--stroke);align-items:baseline}
.chk:last-child{border-bottom:0}
.cn{font-family:var(--font-mono);font-size:.8rem;text-transform:capitalize}
.cv{font-family:var(--font-mono);font-size:.86rem;font-weight:500}
.cr{color:var(--fg-muted);font-size:.88rem;font-weight:300}
@media(max-width:760px){.chk{grid-template-columns:1fr;gap:5px}.lede{margin-left:0}}

/* ── replay: an entirely blue surface, per the rule ────────────────────────── */
.replay{margin-top:26px;padding:46px 52px;border-radius:var(--radius-lg);
  background:var(--brand-blue);color:#fff;display:flex;align-items:center;
  justify-content:space-between;gap:34px;flex-wrap:wrap;
  box-shadow:2px 4px 8px 0 rgba(0,0,0,.5), inset -1px 2px 6px 0 rgba(255,255,255,.35)}
.replay h3{font-family:var(--font-display);font-size:2.4rem;font-weight:400;margin:0 0 12px;
  line-height:1}
.replay p{margin:0;color:rgba(255,255,255,.78);max-width:48ch;font-size:.98rem;font-weight:300}
.pill{position:relative;display:inline-flex;align-items:center;padding:16px 32px 16px 66px;
  border-radius:33.75px;background:#131313;color:#fff;font-weight:500;
  border:0;text-decoration:none;cursor:pointer;white-space:nowrap;
  box-shadow:2px 4px 8px 0 rgba(0,0,0,.5), inset -1px 2px 6px 0 rgba(255,255,255,.25)}
.pill.off{background:rgba(0,0,0,.28);color:rgba(255,255,255,.55);cursor:not-allowed;
  box-shadow:inset 2px 4px 8px 0 rgba(0,0,0,.35)}
.pill .tok{position:absolute;left:9px;top:50%;transform:translateY(-50%);width:44px;height:44px;
  border-radius:50%;background:#fff;color:#131313;display:grid;place-items:center;
  box-shadow:inset 2px 4px 8px 0 rgba(0,0,0,.25)}
.pill.off .tok{background:rgba(255,255,255,.3);color:rgba(0,0,0,.5)}

.notes{margin-top:150px;padding-top:44px;border-top:1px solid var(--stroke)}
.notes h3{font-family:var(--font-mono);font-size:.72rem;letter-spacing:.1em;
  text-transform:uppercase;color:var(--fg-subtle);margin:0 0 18px}
.notes li{color:var(--fg-muted);margin-bottom:11px;font-size:.94rem;max-width:76ch;
  font-weight:300}
@media print{
  /* DESIGN.md 8.3 -- people will PDF this. Light ground, no motion, nothing clipped. */
  body{background:#fff;color:#131313}
  .card{background:#fff;border-color:#ccc;box-shadow:none;break-inside:avoid}
  .replay,.badge{display:none}
  .facts,.checks{border-color:#ccc} .f{background:#fff}
  section{padding-top:40px;break-inside:avoid}
  .plot{overflow:visible}
  .sh h2,.cover h1,.sec-h{color:#131313}
  .lede,.cr,.notes li,.cover .said{color:#333}
}
.foot{margin-top:120px;padding-top:32px;border-top:1px solid var(--stroke);
  font-family:var(--font-mono);font-size:.72rem;color:var(--fg-subtle);
  display:flex;justify-content:space-between;gap:20px;flex-wrap:wrap}
"""



def _rules(card):
    """The criteria strip: what this strategy holds, and every way it gets out.

    Each criterion is rendered even when empty. "This strategy has no stop" is one of the
    most important facts on the page and it cannot be conveyed by an absent column.
    """
    if card.get("error"):
        return (f'<div class="rules"><div class="rpos"><div class="rk">Strategy</div>'
                f'<p style="color:var(--fg-subtle);margin:0">{_e(card["error"])}</p>'
                f'</div></div>')

    legs = "".join(
        f'<li><span class="side {_e(l["side"])}">{_e(l["side"].upper())}</span>'
        f'<span>{"" if l["qty"] == 1 else str(l["qty"]) + "× "}{_e(l["type"])} '
        f'<span class="x">{_e(l["where"])}</span>'
        + (f' <span class="x">· {_e(l["expiry"])} expiry</span>'
           if l["expiry"] != "near" else "")
        + "</span></li>"
        for l in card["legs"]) or '<li><span class="x">not stated</span></li>'

    def col(label, items, cls="", empty="none", value=False):
        # The short form leads only where it is a THRESHOLD -- "70% of credit" above the
        # sentence that qualifies it. On entry and exit the short form is a compression of
        # the same sentence, and printing both reads as a stutter.
        body = "".join(
            (f'<li><b class="hl">{_e(i["short"])}</b>{_e(i["text"])}</li>' if value
             else f'<li>{_e(i["text"])}</li>')
            for i in items) or f'<li class="none">{_e(empty)}</li>'
        return (f'<div class="rl {cls}"><div class="rk">{_e(label)}</div>'
                f'<ul class="rv">{body}</ul></div>')

    adj = list(card["adjust"])
    if adj and card.get("max_adjustments"):
        adj = adj + [{"short": f"{card['max_adjustments']} at most",
                      "text": "the most this position will be adjusted before it is left "
                              "alone"}]
    # A column with nothing in it is not drawn. Three columns reading "no target", "no
    # stop" and "none" is a strip of absences taking a third of the width; the absences
    # are already stated in the second line of the gloss above ("No target, no stop, no
    # adjustment"), which is where a reader meets them anyway.
    cols = [col("Entry criteria", card["entry"]),
            col("Exit criteria", card["exit"] + card["other"])]
    if card["target"]:
        cols.append(col("Take profit", card["target"], "tgt", value=True))
    if card["stop"]:
        cols.append(col("Stop loss", card["stop"], "stp", value=True))
    if adj:
        cols.append(col("Adjustments", adj, "", value=True))
    return ('<div class="rules">'
            f'<div class="rpos"><div class="rk">Position</div>'
            f'<ul class="rlegs">{legs}</ul></div>'
            f'<div class="rgrid">{"".join(cols)}</div></div>')



def render(payload, backtest_id, replay_url=None, report_url=None,
           report_token=None, capital=None, deploy_pct=None, risk_pct=None):
    s = payload.get("summary") or {}
    h = payload.get("honesty") or {}
    spec = payload.get("spec") or {}
    period = s.get("period") or {}
    interp = payload.get("interpretation") or {}

    pnl = s.get("total_pnl_rupees")
    verdict = (h.get("verdict") or "").replace("_", " ")
    health = h.get("health_score")
    vclass = ("v-good" if (health or 0) >= 70 else
              "v-mid" if (health or 0) >= 40 else "v-bad")

    real = reportcharts.render_all(payload)
    real.pop("_errors", None)

    # Sections come from the lab so the two surfaces cannot drift apart. A chart is dropped
    # rather than framed empty when this payload cannot support it -- an 18-trade backtest
    # genuinely has no 30-trade rolling window, and a blank frame reads as a bug.
    card = rulecard.describe(spec)
    rules_html = _rules(card)
    capital_block = capitalview.render(payload, num="01",
                                       capital=capital, deploy=deploy_pct)

    secs = []
    if not (s.get("n_trades") or 0):
        secs.append(
            '<section class="q0"><div class="sh"><span class="num">01</span>'
            '<h2>Nothing to chart</h2>'
            + info("Nothing to chart",
                   "This strategy never opened a position in the window, so there is no "
                   "equity curve, no distribution, no cost and no account to model. That "
                   "is a result rather than a failure \u2014 the entry conditions were "
                   "simply never met. Widen the gate, or check it against what the market "
                   "actually did over the period.")
            + '</div></section>')
    for i, (key, title, lede, charts) in enumerate(
            [] if not (s.get("n_trades") or 0) else reportlab.SECTIONS, start=2):
        # Only charts this payload can actually draw. A chart returns "" when its data is
        # absent or too thin to say anything -- a 26-trade backtest has no 30-trade
        # rolling window, a daily strategy holds every position for the same 270 minutes,
        # and an empty frame reads as a bug rather than as an honest absence.
        drawable = [(cid, w) for cid, w in charts if real.get(cid)]

        # NO HOLES. The grid is two columns and a wide card spans both, so an odd number
        # of half-width cards leaves a visible gap at the end of a section -- which is
        # exactly what made these pages read as unfinished once the seeded charts came
        # out. The last half is promoted to full width instead.
        halves = [n for n, (_, w) in enumerate(drawable) if w != "wide"]
        if len(halves) % 2:
            cid, _ = drawable[halves[-1]]
            drawable[halves[-1]] = (cid, "wide")

        cards = []
        for cid, w in drawable:
            item = real[cid]
            tint = reportlab.TINTS.get(cid)
            cls = ("card" + (" wide" if w == "wide" else "")
                   + (f" tint {tint}" if tint else ""))
            cards.append(
                f'<div class="{cls}"><div class="ct"><span>{_e(item["title"])}</span>'
                f'</div><div class="plot">{item["svg"]}</div></div>')
        if not cards:
            continue
        secs.append(f'<section id="{_e(key)}" class="q{(i - 1) % 4}">'
                    f'<div class="sh"><span class="num">{i:02d}</span>'
                    f'<h2>{_e(title)}</h2>{info(title, lede)}</div>'
                    f'<div class="grid">{"".join(cards)}</div></section>')

    # Each check, with its result and what it means -- the same five the honesty panel
    # scores, rendered as the reader's first encounter with the result.
    rows = []
    for r in (h.get("rubric") or []):
        comp = r.get("component", "")
        got = (h.get("score_breakdown") or {}).get(comp)
        mx = r.get("max_points")
        ok = (got or 0) >= (mx or 0) * 0.5
        # "not scored" and "scored zero" are different facts and must not look the same.
        shown = "—" if got is None else f"{got}/{mx}"
        rows.append(
            f'<div class="chk"><div class="cn">{_e(comp.replace("_", " "))}</div>'
            f'<div class="cv {"up" if ok else "down" if got is not None else "flat"}">'
            f'{_e(shown)}</div>'
            f'<div class="cr">{_e(r.get("rule"))}</div></div>')
    checks = "".join(rows)

    notes = "".join(f"<li>{_e(x)}</li>" for x in
                    (s.get("notes") or []) + (s.get("warnings") or []))
    reading = "".join(f"<li>{_e(x)}</li>" for x in (interp.get("reading") or []))
    dont = "".join(f"<li>{_e(x)}</li>" for x in (interp.get("do_not_conclude") or []))

    evidence_info = info(
        "What the evidence supports",
        "Five checks, each scored before anyone looked at the profit: whether the result "
        "held up out of sample, whether it survived walk-forward, how much of the gross "
        "edge went to costs, how wide the bootstrap interval is, and how many variants "
        "were tried before this one. The verdict is the sum of them and says nothing "
        "about whether the return was large \u2014 a very profitable strategy with one "
        "year of evidence behind it scores badly here, and should.")
    replay_href = replay_url or (f"/replay/{report_token}" if report_token else None)
    replay = (
        f'<a class="pill" href="{_e(replay_href)}"><span class="tok">→</span>'
        f'Open the replay</a>' if replay_href else
        '<span class="pill off"><span class="tok">·</span>Replay — not yet public</span>')
    # DIRECTLY UNDER THE CALENDAR. A reader who has just clicked through single days is
    # already asking to watch the thing run; putting the invitation at the foot of the
    # page asked them to scroll past every chart first and meet it after the argument was
    # over. The day panel opens between the two, which is the right order: explore a day,
    # then go and watch it.
    replay_block = (
        '<div class="replay"><div><h3>Watch it trade</h3>'
        '<p>Step through every entry and exit on the index, day by day, at the speed you '
        'choose — the same engine, replayed.</p></div>'
        + replay + '</div>')

    _src = f" Source of record: {_e(report_url)}" if report_url else ""
    _tok = "✓" if (health or 0) >= 70 else ("!" if (health or 0) >= 40 else "×")
    meth = payload.get("methodology") or {}
    meth_line = (f'methodology v{meth.get("version")} · '
                 f'margin calibrated {str(meth.get("margin_calibrated_at") or "")[:10]}'
                 if meth else "methodology not stamped on this result")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>{_e(_title(spec, s))} · Stratify report</title>
<style>{_FONTS}{reportlab.STYLE}{reportlab.BRIDGE}{_CHART_CSS}{design.MARK_CSS}{EXTRA_CSS}
{capitalview.CSS}</style>
</head><body>
<main>
  <a class="rbrand" href="https://stratify.aeon-labs.site/" title="Stratify"><span class="rmark" style="background-image:url(data:image/png;base64,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)"></span>Stratify</a>
  <div class="cover">
    <div class="eyebrow label">NIFTY · {_e(period.get("from"))} → {_e(period.get("to"))}
      · {_e(card["shape"].replace("_", " ") if card.get("shape") else "")}</div>
    <h1>{_e(card.get("title") or _title(spec, s))}</h1>
    <p class="said">{_e((card.get("gloss") or ["", ""])[0])}</p>
    <p class="said" style="margin-top:12px">{_e((card.get("gloss") or ["", ""])[1])}</p>
  </div>

  {rules_html}

  {capital_block}

  {replay_block}

  <h2 class="sec-h">What the evidence supports {evidence_info}</h2>
  <span class="verdict {vclass}"><span class="tok">{_tok}</span>
    {_e(verdict or "no verdict")} · {health}/100</span>
  <div class="checks" style="margin-top:34px">{checks}</div>

  <h2 class="sec-h">The numbers</h2>

  {"".join(secs)}

  {f'<div class="notes"><h3>What this supports</h3><ul>{reading}</ul></div>' if reading else ""}
  {f'<div class="notes"><h3>What it does not</h3><ul>{dont}</ul></div>' if dont else ""}
  {f'<div class="notes"><h3>Caveats carried by this run</h3><ul>{notes}</ul></div>' if notes else ""}

  {design.MARK_HTML}
  <div class="foot"><span>{_e(backtest_id)}</span><span>{_e(meth_line)}</span>
    <span>Historical simulation. Not investment advice.{_src}</span></div>
</main>
<script>{_INFO_JS}</script>
</body></html>"""


# Loaded once at import: the catalogue's chart classes and drawings, copied verbatim.
def _load():
    """Chart classes, chart drawings, and the FONTS, all embedded.

    THE FONTS ARE INLINE BECAUSE THIS DOCUMENT MUST FETCH NOTHING. A test asserts it --
    no <link>, no <script src>, no url() -- and the assertion is protecting two things a
    webfont would quietly break. A report is meant to survive being downloaded and opened
    offline. And a stylesheet request to a third party hands that third party the IP of
    everyone who ever opens a Stratify report, including people the sender shared it with.

    The cost is 274 KB of base64 on a 58 KB document. That is the right trade for something
    people keep, and only the latin subsets are carried -- latin-ext and vietnamese are
    dead weight in a report that is English plus a rupee sign.
    """
    import pathlib
    here = pathlib.Path(__file__).resolve().parent
    css = here / "_chartcss.txt"
    fonts = here / "_fonts.css"
    return (css.read_text() if css.exists() else "",
            fonts.read_text() if fonts.exists() else "")


# charts.js is NOT embedded any more. It drew the seeded specimens into `data-chart`
# hosts, and there are no such hosts left -- carrying it was 200 KB of a document people
# download, to run code that had nothing to draw.
_CHART_CSS, _FONTS = _load()
_INFO_JS = "\n/* ---- info overlays ------------------------------------------------------------- */\n(function(){\n  var pop;\n  function show(b){\n    if(!pop){ pop = document.createElement('div'); pop.className='info-pop';\n              document.body.appendChild(pop) }\n    pop.textContent = '';\n    var t = document.createElement('b'); t.textContent = b.dataset.t || '';\n    var d = document.createElement('p'); d.textContent = b.dataset.b || '';\n    pop.appendChild(t); pop.appendChild(d);\n    pop.classList.add('on');\n    var r = b.getBoundingClientRect(), w = pop.offsetWidth, h = pop.offsetHeight;\n    var x = Math.min(r.left - 8, innerWidth - w - 14);\n    var y = r.top - h - 10;\n    if (y < 10) y = r.bottom + 10;                 /* not enough room above: go below */\n    pop.style.left = Math.max(12, x) + 'px';\n    pop.style.top = y + 'px';\n  }\n  function hide(){ if(pop) pop.classList.remove('on') }\n  function at(e){ return e.target && e.target.closest ? e.target.closest('.info') : null }\n  /* A PHONE HAS NO HOVER, and every one of these overlays was hover-only -- thirteen\n     icons that did nothing at all when tapped, on the surface most likely to be read on\n     a phone. Touch gets an explicit toggle, and a tap anywhere else closes it, because\n     there is no 'move the pointer away' on a touchscreen either. */\n  var touched = false, open = null;\n  document.addEventListener('mouseover', function(e){\n    if(touched) return;                              /* a tap also fires a synthetic\n                                                        mouseover; ignore it or the tap\n                                                        opens and the toggle shuts it */\n    var b = at(e); if(b) show(b) });\n  document.addEventListener('mouseout', function(e){ if(!touched && at(e)) hide() });\n  document.addEventListener('touchstart', function(){ touched = true }, {passive:true});\n  document.addEventListener('click', function(e){\n    var b = at(e);\n    if(b){ e.preventDefault();\n           if(open === b){ hide(); open = null }\n           else { show(b); open = b } }\n    else if(open){ hide(); open = null }\n  });\n  /* Keyboard reaches it too: the icon is a real button, so focus opens and blurs shut. */\n  document.addEventListener('focusin', function(e){ var b = at(e); if(b) show(b); else hide() });\n  document.addEventListener('keydown', function(e){ if(e.key === 'Escape'){ hide(); open = null } });\n  /* Closing on scroll is right for a hover tooltip and wrong for a tapped one: on a\n     phone the tap itself often nudges the page and the overlay vanished before it could\n     be read. Only dismiss on scroll when it was opened by a pointer. */\n  addEventListener('scroll', function(){ if(!open) hide() }, {passive:true});\n  addEventListener('resize', function(){ hide(); open = null });\n})();\n"
