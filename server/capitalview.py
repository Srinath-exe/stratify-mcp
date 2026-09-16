"""What this strategy would have done to an account, and when.

THE PROBLEM THIS SOLVES. The engine produces ONE-LOT results, which is the honest unit --
a trade is a fact about the market, and how many you take is a fact about you. But nobody
reads their own account in one-lot points. So this is the other half: the same trades,
sized against a capital figure the reader chooses, recomputed live rather than baked in.

IT MIRRORS sizing.apply() EXACTLY, IN JAVASCRIPT. Two implementations of the same model is
a real risk, and the mitigation is that the JS loop is a line-for-line port of the Python
one -- same order, same `base = equity if compounding else capital`, same floor to whole
lots, same skip when the allowance will not cover one lot. A test runs both over the same
payload and asserts they agree. Where they ever disagree, the Python is right.

WHY IT SIZES OVER THE CURVE, NOT OVER `trades`. A standard response carries 25 trade rows
and the curve carries every trade. Sizing the sample would silently describe a different,
shorter strategy -- which is why `margin_rupees` was added to the curve. Where a stored
payload predates that column the view falls back to the median released margin and SAYS SO
on the page, because a capital figure computed off an estimate must not look like one
computed off the real thing.

THE CALENDAR. Shaded by the day's return as a fraction of the equity standing at the start
of that day, not of the starting capital -- late losses in a compounded account are smaller
in per-cent terms than they look in rupees, and the shading has to agree with the number in
the tooltip. Four steps a side, cut at quartiles of the window's own absolute returns, so
the scale adapts to the strategy rather than to a fixed rupee threshold that would render
one strategy uniformly pale and another uniformly saturated.
"""
import datetime as dt
import html
import json

from .design import info

DEFAULT_CAPITAL = 1_000_000
# Capped at Rs 10 lakh. This is the free tier's ladder, and offering a crore of capital
# against a one-year NIFTY window invites a reader to read a number nothing here supports.
CAPITAL_PRESETS = (200_000, 500_000, 1_000_000)
DEFAULT_DEPLOY = 10          # per cent, and now a free-form input rather than a ladder
# What the opening fit walks when the default per-trade share cannot hold one lot.
FIT_LADDER = (2, 5, 10, 15, 20, 25, 30, 40, 50, 60, 75, 100)
REBASE = ("trade", "week", "month", "quarter", "never")
REBASE_LABELS = (("trade", "Every trade"), ("week", "Weekly"), ("month", "Monthly"),
                 ("quarter", "Quarterly"), ("never", "Never — fixed size"))

# The share of trades that must carry a margin figure before an account view means
# anything. Below it the page falls back to one lot and says why.
SIZABLE_FLOOR = 0.8


def _e(v):
    return html.escape("" if v is None else str(v))


def series(payload, capital=None, deploy=None):
    """The compact per-trade series the browser sizes, plus the released detail.

    -> {"t": [[date, pnl_1lot, margin_1lot, detail_index], ...], "d": [detail...], ...}

    Exit-ordered, matching the equity curve: an account receives a profit when the position
    settles, not when it was opened, and on a weekly cadence those are different weeks.
    """
    curve = (payload.get("equity_curve") or {}).get("rows") or []
    cols = (payload.get("equity_curve") or {}).get("columns") or []
    trades = payload.get("trades") or []
    mi = cols.index("margin_rupees") if "margin_rupees" in cols else None
    hi = cols.index("days_held") if "days_held" in cols else None

    # Released rows carry leg prices; the curve does not know which of its points they
    # are. Match on (exit date, whole-rupee P&L) -- the curve rounds, so compare rounded.
    by_key = {}
    for i, t in enumerate(trades):
        key = ((t.get("exit") or "")[:10], int(round(t.get("pnl_rupees") or 0)))
        by_key.setdefault(key, []).append(i)

    med = _median([float(t["margin_points"]) * float(t["lot_size"])
                   for t in trades
                   if t.get("margin_points") and t.get("lot_size")])

    # A day the strategy OPENED something is not a day it did nothing, and until now the
    # calendar could not tell them apart -- the curve is exit-ordered, so an entry whose
    # position was still running showed as an empty cell. `days_held` gives the entry date
    # back for every trade, not just the released ones.
    opened = {}
    for i, r in enumerate(curve):
        if hi is None or len(r) <= hi or r[hi] is None:
            continue
        try:
            d = dt.date(*map(int, str(r[0]).split("-"))) - dt.timedelta(days=int(r[hi]))
        except (TypeError, ValueError):
            continue
        opened.setdefault(str(d), []).append(i)

    out, used = [], set()
    for r in curve:
        date, pnl = r[0], float(r[1])
        margin = float(r[mi]) if mi is not None and len(r) > mi else (med or 0.0)
        idx = -1
        for cand in by_key.get((date, int(round(pnl))), []):
            if cand not in used:
                used.add(cand)
                idx = cand
                break
        out.append([date, round(pnl, 2), round(margin, 2), idx])

    detail = [_detail(t) for t in trades]
    n_sizable = sum(1 for r in out if r[2] > 0)
    lots = {int(t["lot_size"]) for t in trades if t.get("lot_size")}
    first = min([(t.get("entry") or "")[:10] for t in trades if t.get("entry")]
                or [out[0][0] if out else ""])
    last = out[-1][0] if out else ""
    # The grid covers the requested window where one is known, so months the strategy sat
    # out are visible as empty rather than absent. `years` stays trade-based, because CAGR
    # has to measure the same span sizing._years() does.
    period = (payload.get("summary") or {}).get("period") or {}
    grid_from = period.get("from") or first
    grid_to = period.get("to") or last
    return {
        "t": out,
        "d": detail,
        # The same span sizing._years() measures: earliest entry to latest exit. Released
        # trades are the first N BY ENTRY, so trades[0] carries the global first entry
        # even when the rest were trimmed.
        "years": _span(first, last),
        "months": [list(x) for x in months_between(grid_from or last, grid_to or last)],
        "exact": mi is not None,
        "medianMargin": round(med, 2) if med else None,
        "lot": (sorted(lots)[-1] if lots else None),
        # SEEDS, NOT SETTINGS. build_report(format='full') can name a capital and a
        # deployment, and these are where they land -- as the view's opening position.
        # Every control in the page stays live, so a reader who was handed a report at
        # Rs 10 lakh can still ask what Rs 2 lakh would have done without a new report.
        "capital": int(capital or DEFAULT_CAPITAL),
        "presets": sorted(set(list(CAPITAL_PRESETS) + ([int(capital)] if capital else []))),
        "deploy": float(deploy if deploy else DEFAULT_DEPLOY),
        "ladder": list(FIT_LADDER),
        "rebase": [list(x) for x in REBASE_LABELS],
        "opened": opened,
        "released": len(trades),
        "total": len(out),
        # A position the margin model returns zero for cannot be sized against capital at
        # all. That is not "skipped for want of money" and must not be reported as it --
        # sizing.apply() drops such trades from the series entirely, and so does this.
        #
        # A MAJORITY, NOT MERELY ONE. The calendar spread priced exactly one of its 49
        # trades and zero for the rest; on an "any" test it entered the sized view and
        # quietly described an account that took a single trade all year. The account
        # view is only meaningful when it covers substantially the whole book.
        # The measures that describe the TRADES rather than the account. They used to sit
        # in a second block below with its own copy of the P&L, which read as the same
        # section twice; they belong beside the account figures they qualify.
        "stat": _stats(payload),
        "sizable": n_sizable,
        "mode": "sized" if n_sizable >= SIZABLE_FLOOR * len(out) else "onelot",
    }


def _stats(payload):
    s = payload.get("summary") or {}
    r = s.get("ratios") or {}
    return {"win": s.get("win_rate"), "sharpe": r.get("sharpe"),
            "pf": r.get("profit_factor"), "onelot": s.get("total_pnl_rupees"),
            "n": s.get("n_trades"),
            "health": (payload.get("honesty") or {}).get("health_score")}


def _detail(t):
    """One released trade, trimmed to what the day panel shows. Nothing is added here that
    is not already in the payload -- this is a projection, not a second disclosure."""
    return {
        "in": t.get("entry"), "out": t.get("exit"), "why": t.get("exit_reason"),
        "spot": t.get("spot_at_entry"), "exp": t.get("expiry"),
        "dte": t.get("dte_at_entry"), "held": t.get("holding_minutes"),
        "net": t.get("net_points"), "gross": t.get("gross_points"),
        "slip": t.get("slippage_points"), "chg": t.get("charges_rupees"),
        "mpts": t.get("margin_points"), "rom": t.get("return_on_margin"),
        "lot": t.get("lot_size"),
        "legs": [{"a": l.get("action"), "t": l.get("type"), "k": l.get("strike"),
                  "in": l.get("entry_price"), "out": l.get("exit_price"),
                  "by": l.get("closed_by"), "q": l.get("qty")}
                 for l in (t.get("legs") or [])],
        "note": t.get("exit_price_note"),
        "adj": t.get("adjustments"),
    }


def _span(a, b):
    try:
        d0 = dt.date(*map(int, a.split("-")))
        d1 = dt.date(*map(int, b.split("-")))
    except (AttributeError, TypeError, ValueError):
        return 0.0
    return max((d1 - d0).days / 365.25, 1e-9)


def _median(xs):
    xs = sorted(x for x in xs if x)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def months_between(a, b):
    """Every calendar month the window touches, as (year, month)."""
    try:
        y0, m0 = int(a[:4]), int(a[5:7])
        y1, m1 = int(b[:4]), int(b[5:7])
    except (TypeError, ValueError):
        return []
    out = []
    while (y0, m0) <= (y1, m1):
        out.append((y0, m0))
        y0, m0 = (y0 + 1, 1) if m0 == 12 else (y0, m0 + 1)
    return out


# --------------------------------------------------------------------------- style

# The day ramp, mixed toward the page ground rather than laid over it with alpha. Four
# steps a side. Translucent low steps went muddy against the atmospheric background and
# the two ramps stopped being distinguishable at the pale end, which is exactly where a
# calendar has to be read.
CSS = """
.acct{--up1:#184826;--up2:#147630;--up3:#0FA83B;--up4:#0AE448;
    --dn1:#4E282E;--dn2:#823542;--dn3:#BB4357;--dn4:#FF5470;
    /* A THIRD STATE, and deliberately not on the P&L ramp. A day the strategy opened a
       position that had not yet settled has no result to be green or red about, so it
       takes a hue that carries no sign -- light purple, well away from both ends. */
    --open:#B3A5F0}

/* ── the controls ──────────────────────────────────────────────────────────── */
.cv-cfg{display:grid;grid-template-columns:repeat(auto-fit,minmax(228px,1fr));
  background:var(--surface-1);border:1px solid rgba(255,255,255,.07);
  border-radius:var(--radius-lg);overflow:hidden}
.cv-cfg>div{padding:22px 24px;border-right:1px solid var(--stroke)}
.cv-cfg>div:last-child{border-right:0}
.cv-k{font-family:var(--font-mono);font-size:.62rem;letter-spacing:.1em;
  text-transform:uppercase;color:var(--fg-subtle);margin-bottom:13px;
  display:flex;align-items:center;gap:8px}
.cv-sel{width:100%;font-family:var(--font-mono);font-size:.8rem;padding:9px 12px;
  border-radius:12px;border:1px solid var(--stroke);background:var(--surface-2);
  color:var(--fg);cursor:pointer;appearance:none;
  background-image:linear-gradient(45deg,transparent 50%,var(--fg-subtle) 50%),
    linear-gradient(135deg,var(--fg-subtle) 50%,transparent 50%);
  background-position:calc(100% - 17px) 51%, calc(100% - 12px) 51%;
  background-size:5px 5px,5px 5px;background-repeat:no-repeat;padding-right:34px}
.cv-sel:hover{border-color:var(--fg-subtle)}
.cv-sel:focus-visible{outline:none;border-color:var(--brand-green)}
.cv-in{display:flex;align-items:baseline;gap:4px}
.cv-in input{width:3.6ch;font-family:var(--font-mono);font-size:1.55rem;font-weight:500;
  letter-spacing:-.02em;background:transparent;border:0;border-bottom:1px solid var(--stroke);
  color:var(--fg);padding:0 0 3px;text-align:right;-moz-appearance:textfield}
.cv-in input::-webkit-outer-spin-button,.cv-in input::-webkit-inner-spin-button{
  -webkit-appearance:none;margin:0}
.cv-in input:hover{border-bottom-color:var(--fg-subtle)}
.cv-in input:focus{outline:none;border-bottom-color:var(--brand-green)}
.cv-in span{font-family:var(--font-mono);font-size:1.1rem;color:var(--fg-subtle)}
.cv-sub{font-family:var(--font-mono);font-size:.7rem;color:var(--fg-subtle);margin-top:11px}
.cv-seg{display:flex;flex-wrap:wrap;gap:6px}
.cv-seg button{font-family:var(--font-mono);font-size:.72rem;padding:6px 12px;
  border-radius:var(--radius-pill);border:1px solid var(--stroke);background:transparent;
  color:var(--fg-muted);cursor:pointer;transition:background .2s,color .2s,border-color .2s}
.cv-seg button:hover{border-color:var(--fg-subtle);color:var(--fg)}
.cv-seg button[aria-pressed=true]{background:var(--brand-green);color:#131313;
  border-color:var(--brand-green)}
.cv-ro{font-family:var(--font-mono);font-size:1.55rem;letter-spacing:-.01em;line-height:1}
.cv-ro small{display:block;font-family:var(--font-ui);font-size:.76rem;font-weight:300;
  color:var(--fg-subtle);margin-top:10px;line-height:1.45}

/* ── returns ───────────────────────────────────────────────────────────────── */
.cv-ret{display:grid;grid-template-columns:minmax(270px,1fr) 2fr;gap:18px;margin-top:18px}
.cv-loz{border-radius:var(--radius-lg);padding:34px 36px;color:#131313;
  display:flex;flex-direction:column;justify-content:space-between;
  box-shadow:2px 4px 8px 0 rgba(0,0,0,.5), inset -1px 2px 6px 0 rgba(255,255,255,.5)}
.cv-loz .k{font-family:var(--font-mono);font-size:.66rem;letter-spacing:.1em;
  text-transform:uppercase;opacity:.62}
.cv-loz .v{font-family:var(--font-mono);font-size:clamp(2.6rem,5vw,3.6rem);line-height:1;
  letter-spacing:-.025em;font-weight:500;margin-top:14px}
.cv-loz .s{font-size:.82rem;opacity:.68;margin-top:10px}
.cv-up{background:var(--brand-green)} .cv-dn{background:var(--brand-pink)}
.cv-fl{background:var(--surface-2);color:var(--fg)}
.cv-f{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1px;
  background:var(--stroke);border:1px solid var(--stroke);border-radius:var(--radius-lg);
  overflow:hidden}
.cv-f>div{background:var(--surface-1);padding:22px 24px}
.cv-f span{display:block;font-family:var(--font-mono);font-size:.62rem;letter-spacing:.08em;
  text-transform:uppercase;color:var(--fg-subtle);margin-bottom:8px}
.cv-f b{font-family:var(--font-mono);font-size:1.28rem;font-weight:500;letter-spacing:-.01em}
.up{color:var(--pnl-up)} .down{color:var(--pnl-down)}

/* ── the calendar ──────────────────────────────────────────────────────────── */
.cv-cal{display:grid;grid-template-columns:repeat(auto-fill,minmax(212px,1fr));gap:16px;
  margin-top:26px}
.cv-m{background:var(--surface-1);border:1px solid rgba(255,255,255,.07);
  border-radius:16px;padding:15px 15px 13px}
.cv-m h5{margin:0;font-family:var(--font-ui);font-size:.82rem;font-weight:400;
  display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.cv-m h5 i{font-style:normal;font-family:var(--font-mono);font-size:.72rem;font-weight:500}
.cv-g{display:grid;grid-template-columns:repeat(7,1fr);gap:3px;margin-top:11px}
.cv-g>span{font-family:var(--font-mono);font-size:.53rem;color:var(--fg-subtle);
  text-align:center;padding-bottom:3px}
.cv-d{aspect-ratio:1;border-radius:5px;border:0;padding:0;background:rgba(255,255,255,.045);
  font-family:var(--font-mono);font-size:.54rem;color:rgba(255,255,255,.3);
  display:grid;place-items:center;cursor:default;
  transition:transform .16s var(--ease-out),box-shadow .16s var(--ease-out)}
.cv-d.void{background:transparent}
.cv-d.tr{cursor:pointer}
.cv-d.tr:hover{transform:scale(1.22);box-shadow:0 4px 14px rgba(0,0,0,.55);z-index:3;
  position:relative}
.cv-d.sel{box-shadow:0 0 0 2px var(--fg);position:relative;z-index:2}
.cv-d.sk{background:transparent;box-shadow:inset 0 0 0 1px var(--stroke);
  color:var(--fg-subtle)}
.cv-d.op{background:var(--open);color:#1E1840}
.cv-d.u1{background:var(--up1);color:rgba(255,255,255,.8)}
.cv-d.u2{background:var(--up2);color:rgba(255,255,255,.9)}
.cv-d.u3{background:var(--up3);color:#0C2914} .cv-d.u4{background:var(--up4);color:#06331A}
.cv-d.d1{background:var(--dn1);color:rgba(255,255,255,.8)}
.cv-d.d2{background:var(--dn2);color:rgba(255,255,255,.9)}
.cv-d.d3{background:var(--dn3);color:#2B0A12} .cv-d.d4{background:var(--dn4);color:#3D0A17}
.cv-lg{display:flex;align-items:center;gap:9px;flex-wrap:wrap;margin-top:20px;
  font-family:var(--font-mono);font-size:.66rem;color:var(--fg-subtle)}
.cv-lg i{width:15px;height:15px;border-radius:4px;display:inline-block}
.cv-lg .sp{width:14px}

/* ── tooltip ───────────────────────────────────────────────────────────────── */
.cv-tip{position:fixed;z-index:60;pointer-events:none;opacity:0;transform:translateY(5px);
  transition:opacity .14s,transform .14s;background:#0A0A0A;
  box-shadow:0 14px 34px rgba(0,0,0,.7),0 0 0 1px var(--stroke);border-radius:12px;
  padding:13px 15px;font-family:var(--font-mono);font-size:.72rem;min-width:172px;
  line-height:1.7}
.cv-tip.on{opacity:1;transform:translateY(0)}
.cv-tip .dt{color:var(--fg-subtle);font-size:.64rem;letter-spacing:.06em;
  text-transform:uppercase;margin-bottom:6px}
.cv-tip .rw{display:flex;justify-content:space-between;gap:20px}
.cv-tip .rw span{color:var(--fg-subtle)}

/* ── the day panel ─────────────────────────────────────────────────────────── */
.cv-day{margin-top:18px;background:var(--surface-1);border-radius:var(--radius-lg);
  border:1px solid rgba(255,255,255,.07);overflow:hidden}
.cv-day .hd{padding:24px 28px;border-bottom:1px solid var(--stroke);display:flex;
  justify-content:space-between;align-items:baseline;gap:18px;flex-wrap:wrap}
.cv-day .hd h4{margin:0;font-family:var(--font-display);font-size:1.7rem;font-weight:400}
.cv-day .hd .n{font-family:var(--font-mono);font-size:1.5rem;font-weight:500}
.cv-day .empty{padding:32px 28px;color:var(--fg-subtle);font-weight:300}
.cv-t{padding:22px 28px;border-bottom:1px solid var(--stroke)}
.cv-t:last-child{border-bottom:0}
.cv-th{display:flex;justify-content:space-between;gap:18px;flex-wrap:wrap;
  align-items:baseline}
.cv-th .w{font-family:var(--font-mono);font-size:.76rem;color:var(--fg-muted)}
.cv-th .w b{color:var(--fg);font-weight:500}
.cv-th .r{font-family:var(--font-mono);font-size:1.05rem;font-weight:500}
.cv-tbl{width:100%;border-collapse:collapse;margin-top:15px;font-family:var(--font-mono);
  font-size:.75rem}
.cv-tbl th{text-align:right;font-weight:400;color:var(--fg-subtle);font-size:.6rem;
  letter-spacing:.08em;text-transform:uppercase;padding:0 0 8px 14px;white-space:nowrap}
.cv-tbl th:first-child,.cv-tbl td:first-child{text-align:left;padding-left:0}
.cv-tbl td{text-align:right;padding:7px 0 7px 14px;
  border-top:1px solid rgba(255,255,255,.06);white-space:nowrap}
.cv-tbl .sell{color:var(--pnl-down)} .cv-tbl .buy{color:var(--brand-blue)}
.cv-note{margin-top:13px;font-size:.78rem;color:var(--fg-subtle);font-weight:300;
  max-width:80ch;line-height:1.5}
.cv-wrap{overflow-x:auto}
@media(max-width:820px){.cv-ret{grid-template-columns:1fr}
  .cv-f{grid-template-columns:repeat(2,1fr)}}
@media print{.cv-tip{display:none}.cv-cfg{background:#fff}
  .cv-m,.cv-day{background:#fff;border-color:#ccc}}
"""


# ---------------------------------------------------------------------------- script

# A LINE-FOR-LINE PORT of sizing.apply(). Same order, same base, same floor, same skip.
# test_capitalview.py runs both over a real payload and asserts they agree to the rupee;
# where they ever differ, the Python is right and this is the bug.
JS = r"""
(function(){
var D = window.__CV__; if(!D || !D.t || !D.t.length) return;
// Results stored before `days_held` reached the curve carry no entry dates.
D.opened = D.opened || {};
var $ = function(s,r){return (r||document).querySelector(s)};
var cfg = {cap:D.capital, deploy:D.deploy/100, rebase:'trade'};
var sized = null, picked = null;

// ---------------------------------------------------------------- formatting
function inr(v){
  if(v==null) return '—';
  var s = v<0?'−':'', a=Math.abs(v);
  if(a>=1e7) return s+'₹'+(a/1e7).toFixed(2)+' Cr';
  if(a>=1e5) return s+'₹'+(a/1e5).toFixed(2)+' L';
  if(a>=1000) return s+'₹'+(a/1000).toFixed(1)+'k';
  return s+'₹'+Math.round(a);
}
function full(v){
  if(v==null) return '—';
  return (v<0?'−':'')+'₹'+Math.round(Math.abs(v)).toLocaleString('en-IN');
}
function pc(v,dp){
  if(v==null||!isFinite(v)) return '—';
  dp = dp==null?2:dp;
  return (v>0?'+':v<0?'−':'')+Math.abs(v*100).toFixed(dp)+'%';
}
function sgn(v){return v>0?'up':v<0?'down':''}
// A win rate is not a change, so it takes no sign. pc() signs everything because every
// other number it formats is a return.
function rate(v,dp){ return v==null? '\u2014' : (v*100).toFixed(dp==null?0:dp)+'%' }
// In one-lot mode there is no capital to be a per cent OF, so every bucket reports
// rupees on one lot instead. Two units, one accessor, so no surface can drift.
function lab(b){ return sized.one ? inr(b.pnl) : pc(b.ret,1) }
function mag(b){ return sized.one ? Math.abs(b.pnl) : Math.abs(b.ret||0) }
function mins(m){
  if(m==null) return '—';
  if(m<120) return m+'m';
  if(m<1440) return Math.floor(m/60)+'h'+(m%60?' '+(m%60)+'m':'');
  var d=Math.floor(m/1440), h=Math.floor((m%1440)/60);
  return d+'d'+(h?' '+h+'h':'');
}
var MON = ['January','February','March','April','May','June','July','August',
           'September','October','November','December'];
var DOW = ['S','M','T','W','T','F','S'];

// ---------------------------------------------------------------- the model
// Mirrors sizing._period_key. A new bucket is a moment the position size is recalculated.
function pkey(d, mode){
  if(mode==='month') return d.slice(0,7);
  if(mode==='quarter') return d.slice(0,4)+'Q'+Math.floor((+d.slice(5,7)-1)/3);
  if(mode==='week'){
    var dt = new Date(d+'T00:00:00Z');
    dt.setUTCDate(dt.getUTCDate() - ((dt.getUTCDay()+6)%7));   // back to Monday
    return dt.toISOString().slice(0,10);
  }
  return '';
}

function size(){
  var one = D.mode==='onelot';
  var cap = one ? 0 : cfg.cap, eq=cap, peak=cap, maxDD=0, maxDDpct=0;
  var rows=[], taken=0, skipped=0, unsized=0, lots=[], margins=[];
  var baseEq = cap, lastKey = null;
  var days={}, months={}, years={};
  function slot(bag,key,eqBefore){
    if(!bag[key]) bag[key]={pnl:0,n:0,eq:eqBefore,sk:0};
    return bag[key];
  }
  for(var i=0;i<D.t.length;i++){
    var r=D.t[i], date=r[0], pnl1=r[1], m1=r[2], di=r[3];
    var mk=date.slice(0,7), yk=date.slice(0,4);
    var dd=slot(days,date,eq), mm=slot(months,mk,eq), yy=slot(years,yk,eq);
    var n;
    if(one){ n = 1 }
    else if(!(m1>0)){
      // No margin figure: unsizable, which is a different fact from unaffordable.
      unsized++; rows.push({d:date,lots:0,pnl:0,eq:eq,unsized:true,di:di,m:0});
      continue;
    } else {
      var key = pkey(date, cfg.rebase);
      if(key !== lastKey){ baseEq = eq; lastKey = key }
      var base = cfg.rebase==='trade' ? eq : cfg.rebase==='never' ? cap : baseEq;
      n = Math.floor(base*cfg.deploy/m1);
      if(n<1){
        skipped++; dd.sk++; mm.sk++; yy.sk++;
        rows.push({d:date,lots:0,pnl:0,eq:eq,skip:true,di:di,m:m1});
        continue;
      }
    }
    var pnl = pnl1*n;
    var before = eq;
    eq += pnl; if(eq>peak) peak=eq;
    var dr = eq-peak; if(dr<maxDD) maxDD=dr;
    var dp = peak? dr/peak : 0; if(dp<maxDDpct) maxDDpct=dp;
    taken++; lots.push(n); margins.push(m1*n);
    dd.pnl+=pnl; dd.n++; mm.pnl+=pnl; mm.n++; yy.pnl+=pnl; yy.n++;
    rows.push({d:date,lots:n,pnl:pnl,eq:eq,before:before,di:di,m:m1*n});
  }
  var net = eq-cap;
  var oneLot = med(D.t.map(function(r){return r[2]}));
  var cagr = (!one && D.years>=30/365.25 && eq>0) ? Math.pow(eq/cap,1/D.years)-1 : null;
  function ret(bag){ for(var k in bag){ bag[k].ret = bag[k].eq? bag[k].pnl/bag[k].eq : null }
    return bag }
  return {one:one, rows:rows, days:ret(days), months:ret(months), years:ret(years),
          end:eq, net:net, retPct:(one||!cap)?null:net/cap, cagr:cagr, unsized:unsized,
          maxDD:maxDD, maxDDpct:maxDDpct, taken:taken, skipped:skipped,
          lotsMed:med(lots), marginMed:med(margins), oneLot:oneLot,
          lotsMax:Math.max.apply(null,lots.concat([0]))};
}
function best(sign){ var b=0; for(var k in sized.days){ var v=sized.days[k].pnl;
  if(sign>0? v>b : v<b) b=v } return b }
function count(sign){ var n=0; for(var k in sized.days){ var v=sized.days[k].pnl;
  if(sign>0? v>0 : v<0) n++ } return n }
function med(a){ if(!a.length) return 0; var s=a.slice().sort(function(x,y){return x-y});
  return s.length%2 ? s[(s.length-1)/2] : (s[s.length/2-1]+s[s.length/2])/2 }

// ---------------------------------------------------------------- opening position
// WHY THE OPENING SHARE IS FITTED RATHER THAN FIXED. One lot of a naked strangle blocks
// more than the default allowance, and at 10% of Rs 10 L every trade is then skipped -- a
// correct answer that renders as an empty calendar and reads as a broken page. Capital is
// capped, so the fit moves the per-trade share, and the reason is put in the Capital
// tooltip rather than quietly presenting a different setting as though it were chosen.
var fitted = null;
function fit(){
  var want = D.t.length*0.9;
  function takes(dep){
    var eq=cfg.cap, n=0, baseEq=cfg.cap, lastKey=null;
    for(var i=0;i<D.t.length;i++){
      var m=D.t[i][2];
      if(!(m>0)) continue;
      var k=pkey(D.t[i][0], cfg.rebase);
      if(k!==lastKey){ baseEq=eq; lastKey=k }
      var base = cfg.rebase==='trade'? eq : cfg.rebase==='never'? cfg.cap : baseEq;
      var lots = Math.floor(base*dep/m);
      if(lots<1) continue;
      n++; eq += D.t[i][1]*lots;
    }
    return n;
  }
  if(takes(cfg.deploy) >= want) return;
  for(var i=0;i<D.ladder.length;i++){
    if(takes(D.ladder[i]/100) >= want){
      fitted = {deploy:D.ladder[i], from:D.deploy};
      cfg.deploy = D.ladder[i]/100;
      return;
    }
  }
}

// ---------------------------------------------------------------- controls
var HELP = {
  cap: ['Capital',
    'The account this strategy is being sized against. It is a starting figure, not a '
    + 'margin requirement: everything on the page is the same one-lot trades scaled to '
    + 'fit it, and nothing is re-run when you change it. Capped at Rs 10 lakh because '
    + 'this window is one year of NIFTY, which is not enough evidence to support a '
    + 'number bigger than that.'],
  reb: ['Position sizing',
    'How often the size is recalculated. "Every trade" resizes off the equity standing '
    + 'the moment each position settles, which nobody actually does; "Never" keeps the '
    + 'opening size all year, ignoring both profit and loss. The cycles in between are '
    + 'what people really trade — you look at the account at the start of a month or a '
    + 'quarter and set the size then. It matters more than it sounds: on a losing run, '
    + 'resizing often cuts you down as you go, while never resizing keeps you pressing '
    + 'at the old size all the way down.'],
  dep: ['Capital per trade',
    'The share of capital allowed as margin on any one position. Lots are whole numbers, '
    + 'so the size steps rather than scales smoothly, and a trade needing more margin '
    + 'than the allowance is skipped rather than taken undersized — those days show on '
    + 'the calendar as outlines. For a debit position (a bought option) the margin IS '
    + 'the premium, so a high share here buys a great many cheap contracts.'],
  mar: ['Margin per trade',
    'What this position actually blocks, at the size currently set. It comes from a SPAN '
    + 'calibration measured against real broker statements rather than an exchange '
    + 'formula, so it is what a broker would have held, not what a textbook says. The '
    + 'figure below it is the typical lot count and whether every trade fitted.'],
  cal: ['The calendar',
    'One cell per day, and the states are read in order. GREEN OR RED when something '
    + 'closed that day, shaded four steps a side at this strategy\u2019s own quartiles '
    + 'rather than a fixed rupee threshold \u2014 so the shade means "big for this '
    + 'strategy", the only comparison available on one page. PURPLE when a position was '
    + 'entered that day; a day that both entered and closed takes the P&L colour, because '
    + 'a result outranks an intention, and the entry is named in the tooltip. OUTLINED '
    + 'when the strategy wanted a trade but the allowance would not cover a single lot, '
    + 'so nothing was placed at all. EMPTY when it did nothing, which on a weekly cadence '
    + 'is most days. The percentage on each month is that month\u2019s return on the '
    + 'equity standing when it began, so the months do not add up to the year \u2014 they '
    + 'compound. Click any day to open it.']
};

function help(k){
  return '<button class="info" type="button" aria-label="About '+HELP[k][0]
    + '" data-t="'+HELP[k][0]+'" data-b="'+HELP[k][1].replace(/"/g,'&quot;')+'">i</button>';
}

function controls(){
  var host = $('#cvCfg'); if(!host) return;
  function k(label, key){
    return '<div class="cv-k"><span>'+label+'</span>'+help(key)+'</div>';
  }
  host.innerHTML = ''
   + '<div>'+k('Capital','cap')+'<div class="cv-seg" id="cvCap"></div></div>'
   + '<div>'+k('Position sizing','reb')
   +   '<select class="cv-sel" id="cvReb">'
   +   D.rebase.map(function(r){return '<option value="'+r[0]+'">'+r[1]+'</option>'}).join('')
   +   '</select></div>'
   + '<div>'+k('Capital per trade','dep')
   +   '<div class="cv-in"><input id="cvDep" type="number" min="1" max="100" step="1" '
   +   'inputmode="numeric" aria-label="per cent of capital per trade"><span>%</span></div>'
   +   '<div class="cv-sub" id="cvAllow"></div></div>'
   + '<div>'+k('Margin per trade','mar')+'<div class="cv-ro" id="cvMar"></div></div>';

  D.presets.forEach(function(v){
    var b=document.createElement('button'); b.dataset.v=v; b.textContent=inr(v);
    b.onclick=function(){cfg.cap=v; draw()}; $('#cvCap').appendChild(b);
  });
  $('#cvReb').onchange = function(){ cfg.rebase = this.value; draw() };
  var dep = $('#cvDep');
  dep.oninput = function(){
    var v = parseFloat(dep.value);
    if(!isFinite(v) || v <= 0) return;              // mid-edit: wait for a usable number
    // Clamp what is SHOWN as well as what is used. Silently capping the model at 100
    // while the field still reads 400 puts two different answers on the same control.
    if(v > 100){ v = 100; dep.value = '100' }
    cfg.deploy = v/100; draw();
  };
  dep.onblur = function(){ dep.value = (cfg.deploy*100).toFixed(0) };
}

function marks(){
  if(D.mode==='onelot'){ return }
  Array.prototype.forEach.call($('#cvCap').children, function(b){
    b.setAttribute('aria-pressed', String(+b.dataset.v)===String(cfg.cap)) });
  $('#cvReb').value = cfg.rebase;
  var dep = $('#cvDep');
  if(document.activeElement !== dep) dep.value = (cfg.deploy*100).toFixed(0);
  $('#cvAllow').textContent = full(cfg.cap*cfg.deploy)+' of '+full(cfg.cap)+' per trade';

  var lots = sized.lotsMed, lot = D.lot, allow = cfg.cap*cfg.deploy;
  $('#cvMar').innerHTML = full(lots? sized.marginMed : sized.oneLot)
    + '<small>'
    + (lots
       ? lots+' lot'+(lots===1?'':'s')+(lot? ' of '+lot:'')+' typically'
         + (sized.lotsMax>lots? ', up to '+sized.lotsMax+' at the peak':'') + '. '
         + (sized.skipped? sized.skipped+' of '+D.t.length+' trades were too big to take.'
            : 'Every trade fitted the allowance.')
       : 'for a single lot, against an allowance of '+full(allow)+'. Nothing could be '
         + 'taken at this size — raise the capital, or the share of it per trade.')
    + '</small>';

  // The fit is a fact about what the reader is looking at, so it belongs in the tooltip
  // of the control it moved rather than as another paragraph on the page.
  var b = $('#cvCap').parentNode.querySelector('.info');
  if(b) b.dataset.b = HELP.cap[1] + (fitted
    ? '  Opened at ' + fitted.deploy + '% per trade rather than the usual ' + fitted.from
      + '%: one lot of this position blocks ' + full(sized.oneLot)
      + ', so a smaller share could not have held it at all.'
    : '');
}

// ---------------------------------------------------------------- returns
function returns(){
  var cls = sized.net>0?'cv-up':sized.net<0?'cv-dn':'cv-fl';
  $('#cvHero').className = 'cv-loz '+cls;
  $('#cvHero').innerHTML = sized.one
    ? '<div class="k">profit and loss, one lot</div><div>'
      + '<div class="v" style="font-size:clamp(2rem,4vw,2.9rem)">'+inr(sized.net)+'</div>'
      + '<div class="s">'+sized.taken+' trades · no capital model available</div></div>'
    : '<div class="k">return on capital</div><div>'
      + '<div class="v">'+pc(sized.retPct,1)+'</div>'
      + '<div class="s">'+full(sized.net)+' on '+full(cfg.cap)+', '
      + {trade:'resized after every trade', week:'resized weekly',
         month:'resized monthly', quarter:'resized quarterly',
         never:'at a fixed size throughout'}[cfg.rebase]+'</div></div>';
  var st = D.stat || {};
  var num = function(v,dp){ return v==null? '—' : (+v).toFixed(dp==null?2:dp) };
  var f = sized.one ? [
    ['best day', full(best(1)), 'up'],
    ['worst day', full(best(-1)), 'down'],
    ['deepest hole', full(sized.maxDD), sized.maxDD<0?'down':''],
    ['trades', String(sized.taken), ''],
    ['win rate', rate(st.win), ''],
    ['profit factor', num(st.pf), '']
  ] : [
    ['ending capital', full(sized.end), sgn(sized.net)],
    ['annualised', pc(sized.cagr,1), sgn(sized.cagr)],
    ['worst drawdown', pc(sized.maxDDpct,1), sized.maxDDpct<0?'down':''],
    ['deepest hole', full(sized.maxDD), sized.maxDD<0?'down':''],
    ['win rate', rate(st.win), ''],
    ['profit factor', num(st.pf), ''],
    ['sharpe', num(st.sharpe), ''],
    // The engine's own unit, kept in view: one lot, unsized. Everything else in this
    // grid is a consequence of the controls above; this one is not.
    ['one lot, unsized', full(st.onelot), sgn(st.onelot)],
    ['trades taken', String(sized.taken)
      + (sized.unsized? ' of '+D.total : sized.skipped? ' of '+D.total : ''), '']
  ];
  $('#cvFacts').innerHTML = f.map(function(x){
    return '<div><span>'+x[0]+'</span><b class="'+x[2]+'">'+x[1]+'</b></div>' }).join('');
}

// ---------------------------------------------------------------- calendar
function ramp(){
  var a=[]; for(var k in sized.days){ if(sized.days[k].n) a.push(mag(sized.days[k])) }
  a.sort(function(x,y){return x-y});
  if(!a.length) return [0,0,0];
  // Quartiles of the window's OWN absolute day returns. A fixed rupee threshold renders
  // one strategy uniformly pale and the next uniformly saturated; this makes the ramp
  // mean "big for this strategy", which is the only comparison a reader can make here.
  var q=function(p){ return a[Math.min(a.length-1, Math.floor(a.length*p))] };
  return [q(.25), q(.55), q(.82)];
}
function calendar(){
  // Entry days, but only for trades the account could actually take at this size. A
  // trade skipped for want of margin was never entered, so painting its entry day would
  // mark an order that was not placed. This depends on the controls, so it is rebuilt on
  // every draw rather than sent down once.
  var entries = {};
  for(var k in D.opened){
    var n = 0;
    D.opened[k].forEach(function(i){
      var r = sized.rows[i];
      if(r && !r.skip && !r.unsized) n++;
    });
    if(n) entries[k] = n;
  }
  var cuts = ramp(), host = $('#cvCal'), out = [];
  D.months.forEach(function(ym){
    var y=ym[0], m=ym[1], key=y+'-'+String(m).padStart(2,'0');
    var mm = sized.months[key] || {pnl:0,n:0,ret:null};
    var first = new Date(Date.UTC(y, m-1, 1)), lead = first.getUTCDay();
    var ndays = new Date(Date.UTC(y, m, 0)).getUTCDate();
    var cells = DOW.map(function(d){return '<span>'+d+'</span>'});
    for(var i=0;i<lead;i++) cells.push('<div class="cv-d void"></div>');
    for(var d=1;d<=ndays;d++){
      var ds = key+'-'+String(d).padStart(2,'0'), day = sized.days[ds];
      var cls='cv-d', lbl=String(d);
      if(day && day.n){
        var t=mag(day);
        var step = t>cuts[2]?4 : t>cuts[1]?3 : t>cuts[0]?2 : 1;
        cls += ' tr '+(day.pnl>0?'u':'d')+step;
      } else if(entries[ds]){ cls += ' op tr' }
      else if(day && day.sk){ cls += ' sk tr' }
      cells.push('<button class="'+cls+'" data-d="'+ds+'">'+lbl+'</button>');
    }
    out.push('<div class="cv-m" id="m'+key+'"><h5>'+MON[m-1]+' <i class="'+sgn(mm.pnl)+'">'
      +(mm.n?lab(mm):'')+'</i></h5><div class="cv-g">'+cells.join('')+'</div></div>');
  });
  host.innerHTML = out.join('');
  Array.prototype.forEach.call(host.querySelectorAll('.cv-d.tr'), function(b){
    var day = sized.days[b.dataset.d];
    b.onmouseenter=function(e){
      var ds = b.dataset.d, op = entries[ds] || 0;
      var rows = day && day.n
        ? [['profit and loss', full(day.pnl)]].concat(sized.one? []
            : [['on the account', pc(day.ret,2)], ['equity before', full(day.eq)]],
            [['closed', String(day.n)+(day.sk?' (+'+day.sk+' skipped)':'')]],
            op? [['entered', String(op)]] : [])
        : op
        ? [['entered', op+' position'+(op===1?'':'s')],
           ['closed', 'nothing on this day']]
        : [['not taken', (day&&day.sk||0)+' trade'+((day&&day.sk)===1?'':'s')],
           ['why', 'the allowance would not cover one lot']];
      tip(e, pretty(b.dataset.d), rows);
    };
    b.onmousemove=move; b.onmouseleave=hide;
    b.onclick=function(){ pick(b.dataset.d) };
  });
  if(picked) mark();
}
function pretty(ds){
  var p=ds.split('-'), dt=new Date(Date.UTC(+p[0],+p[1]-1,+p[2]));
  return ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'][dt.getUTCDay()]
    + ', ' + (+p[2]) + ' ' + MON[+p[1]-1] + ' ' + p[0];
}
function mark(){
  Array.prototype.forEach.call(document.querySelectorAll('.cv-d'), function(b){
    b.classList.toggle('sel', b.dataset.d===picked) });
}

// ---------------------------------------------------------------- tooltip
var TIP;
function tip(e, head, rows){
  if(!TIP){ TIP=document.createElement('div'); TIP.className='cv-tip';
            document.body.appendChild(TIP) }
  TIP.innerHTML = '<div class="dt">'+head+'</div>' + rows.map(function(r){
    return '<div class="rw"><span>'+r[0]+'</span><b>'+r[1]+'</b></div>' }).join('');
  TIP.classList.add('on'); move(e);
}
function move(e){
  if(!TIP) return;
  var w=TIP.offsetWidth, h=TIP.offsetHeight;
  var x=Math.min(e.clientX+16, innerWidth-w-12), y=e.clientY-h-14;
  if(y<10) y=e.clientY+18;
  TIP.style.left=x+'px'; TIP.style.top=y+'px';
}
function hide(){ if(TIP) TIP.classList.remove('on') }

// ---------------------------------------------------------------- the day panel
function pick(ds){
  picked = (picked===ds) ? null : ds;
  mark(); day();
  if(picked) $('#cvDay').scrollIntoView({behavior:'smooth', block:'nearest'});
}
function day(){
  var host=$('#cvDay');
  if(!picked){ host.hidden = true; host.innerHTML=''; return }
  host.hidden = false;
  var d = sized.days[picked] || {pnl:0,n:0,ret:null,sk:0};
  var rows = sized.rows.filter(function(r){ return r.d===picked });
  // Entered on this day and closed on another. A skipped trade was never entered, so
  // it must not be listed here either -- it is already reported on its own exit day.
  var op = (D.opened[picked] || []).filter(function(i){
    var r = sized.rows[i];
    return D.t[i][0] !== picked && r && !r.skip && !r.unsized;
  });
  var head = '<div class="hd"><h4>'+pretty(picked)+'</h4>'
    + (d.n
       ? '<div class="n '+sgn(d.pnl)+'">'+full(d.pnl)+' <span style="font-size:.8rem;'
         + 'color:var(--fg-subtle)">'+(sized.one? 'on one lot'
             : pc(d.ret,2)+' on the account')+'</span></div>'
       : '<div class="n" style="font-size:.9rem;color:var(--fg-subtle)">'
         + (op.length? op.length+' position'+(op.length===1?'':'s')+' entered, none closed'
            : 'nothing closed on this day')+'</div>')
    + '</div>';
  host.innerHTML = head + rows.map(trade).join('')
    + op.map(function(i){
        var r = sized.rows[i];
        return '<div class="cv-t"><div class="cv-th"><div class="w">'
          + '<b>Entered</b> this day \u00b7 ' + r.lots + ' lot'
          + (r.lots===1?'':'s') + ' \u00b7 closed ' + pretty(D.t[i][0])
          + '</div><div class="r '+sgn(r.pnl)+'">'+full(r.pnl)+'</div></div></div>';
      }).join('');
}
function trade(r){
  if(r.unsized){
    return '<div class="cv-t"><div class="cv-th"><div class="w">The margin model returns '
      + 'zero for this position, so it cannot be sized against capital.</div>'
      + '<div class="r">—</div></div></div>';
  }
  if(r.skip){
    return '<div class="cv-t"><div class="cv-th"><div class="w">Not taken — one lot '
      + 'needed '+full(r.m)+' and the allowance was '+full(cfg.cap*cfg.deploy)+'.</div>'
      + '<div class="r">—</div></div></div>';
  }
  var t = r.di>=0 ? D.d[r.di] : null;
  var head = '<div class="cv-th"><div class="w">'
    + (t? '<b>'+t['in'].slice(11)+'</b> in on '+t['in'].slice(0,10)
          +' · <b>'+t.out.slice(11)+'</b> out · '+t.why
          +' · held '+mins(t.held)
        : 'closed this day')
    + ' · '+r.lots+' lot'+(r.lots===1?'':'s')+' · '+full(r.m)+' blocked</div>'
    + '<div class="r '+sgn(r.pnl)+'">'+full(r.pnl)+'</div></div>';
  if(!t){
    return '<div class="cv-t">'+head+'<div class="cv-note">The leg prices for this trade '
      + 'were not part of this release — a standard result carries the first '
      + D.released+' of '+D.total+' with their prices, and every aggregate on this page '
      + 'covers all '+D.total+'. Re-run with detail=\'full\' to see the rest.</div></div>';
  }
  // A stored note can contradict the row it sits on: results written before the
  // engine read the exit reason first carry "no per-leg price to report" directly above
  // a table of them. Trust what the legs actually contain.
  var priced = t.legs.some(function(l){ return l.out!=null });
  var legs = '<div class="cv-wrap"><table class="cv-tbl"><tr><th>leg</th><th>strike</th>'
    + '<th>entry</th><th>exit</th><th>points</th><th>closed by</th></tr>'
    + t.legs.map(function(l){
        // The leg's own contribution, not the raw price change: a short leg whose
        // price falls has GAINED, and printing that as a negative in green was a number
        // and a colour disagreeing on the same cell.
        var mv = (l['in']!=null && l.out!=null)
          ? (l.a==='SELL' ? l['in']-l.out : l.out-l['in']) * (l.q||1) : null;
        var q = l.q&&l.q>1 ? ' ×'+l.q : '';
        return '<tr><td class="'+(l.a||'').toLowerCase()+'">'+(l.a||'')+' '+(l.t||'')+q+'</td>'
          + '<td>'+(l.k!=null?l.k:'—')+'</td>'
          + '<td>'+(l['in']!=null?l['in'].toFixed(2):'—')+'</td>'
          + '<td>'+(l.out!=null?l.out.toFixed(2):'—')+'</td>'
          + '<td class="'+((mv==null)?'':sgn(mv))+'">'
          + (mv==null?'—':(mv>0?'+':'−')+Math.abs(mv).toFixed(2))+'</td>'
          + '<td>'+(l.by||'—')+'</td></tr>' }).join('')
    + '</table></div>';
  var meta = '<div class="cv-note">Index at entry '+(t.spot!=null?t.spot.toFixed(2):'—')
    + ' · expiry '+(t.exp||'—')+' ('+(t.dte!=null?t.dte:'—')+' DTE)'
    + ' · net '+(t.net!=null?t.net.toFixed(2):'—')+' pts on one lot, of which '
    + (t.slip!=null?t.slip.toFixed(2):'—')+' went to slippage'
    + ' · charges '+full(t.chg!=null? t.chg*r.lots : null)
    + (t.rom!=null? ' · '+pc(t.rom,2)+' on the margin it blocked':'')
    + (t.note && !priced? ' — '+t.note : '') + '</div>';
  var adj = t.adj && t.adj.length
    ? '<div class="cv-note"><b>Rules that fired</b> — '+t.adj.map(function(a){
        if(typeof a==='string') return a;
        var when = (a.ts||'').slice(11,16);
        return (when? when+' ':'') + '\u201C'+(a.rule||'rule')+'\u201D \u2192 '
          + (a.did||'acted')
          + (a.pnl_pts!=null? ' at '+(a.pnl_pts>0?'+':'\u2212')
             +Math.abs(a.pnl_pts).toFixed(2)+' pts':'');
      }).join(' · ')+'</div>' : '';
  return '<div class="cv-t">'+head+legs+meta+adj+'</div>';
}

// ---------------------------------------------------------------- go
function draw(){ sized=size(); marks(); returns(); calendar(); day() }
controls(); fit(); draw();
addEventListener('scroll', hide, {passive:true});
})();
"""


# ----------------------------------------------------------------------------- render

def render(payload, num="02", capital=None, deploy=None):
    """The whole capital block: controls, returns, calendar, day panel.

    Returns "" when the payload cannot support it -- a strategy that never traded has no
    account to model, and an empty calendar with a zero on it reads as a broken page
    rather than as the honest answer, which is that there is nothing to size.
    """
    data = series(payload, capital=capital, deploy=deploy)
    if not data["t"]:
        return ""

    one = data["mode"] == "onelot"
    est = "" if data["exact"] or one else (
        '<p class="cv-note" style="margin:14px 0 0">This result was stored before per-trade '
        'margin travelled with the equity curve, so trades outside the released '
        f'{data["released"]} are sized against the median margin of those that were '
        f'({_e(_rs(data["medianMargin"]))} for one lot) rather than their own. Re-run the '
        'strategy for exact sizing.</p>')

    # A position the margin model prices at zero cannot be sized against capital at all.
    # Saying so and keeping the calendar in one-lot rupees is more useful than a control
    # bar that produces zeroes, and far more useful than hiding the section.
    controls = ('<div class="cv-cfg" id="cvCfg"></div>'
                '<p class="cv-note" id="cvFit" style="margin:14px 0 0"></p>' if not one else
                '<div class="cv-cfg"><div style="grid-column:1/-1">'
                '<div class="cv-k">No capital model for this position</div>'
                '<p style="margin:0;font-weight:300;color:var(--fg-muted);max-width:78ch">'
                f'The margin model prices only {data["sizable"]} of these '
                f'{data["total"]} trades — it does not price a position held across two '
                'expiries — so there is no margin to size the book against and no capital '
                'figure to express a return as a per cent of. Everything below is '
                '<b>one lot</b>, in rupees. The trades, the dates and the P&amp;L are '
                'exact; only the account view is missing.</p></div></div>')

    lede = ("The same trades, sized against a capital figure you choose. Nothing is "
            "re-run when you change a control — position size is a view over one-lot "
            "results, not a different backtest, so every number here moves immediately. "
            "Returns are per cent of the account; hover any day or month for the rupees. "
            "One caution worth carrying: sized and unsized results can reach opposite "
            "signs through a deep drawdown, because after losing half the account every "
            "position that follows is half the size."
            if not one else
            "One lot throughout, because the margin model cannot price this position and "
            "there is therefore no capital to express a return as a per cent of. The "
            "trades, the dates and the P&L are exact; only the account view is missing.")

    day_ramp = ("Shaded by what the day did to the account" if not one else
                "Shaded by the day's profit and loss on one lot")

    legend = ('<div class="cv-lg"><span>Loss</span>'
              + "".join(f'<i style="background:var(--dn{i})"></i>' for i in (4, 3, 2, 1))
              + '<span class="sp"></span>'
              + "".join(f'<i style="background:var(--up{i})"></i>' for i in (1, 2, 3, 4))
              + '<span>Profit</span><span class="sp"></span>'
              '<i style="background:transparent;box-shadow:inset 0 0 0 1px var(--stroke)">'
              '</i><span>too small to take</span>'
              '<span class="sp"></span>'
              '<i style="background:var(--open)"></i><span>entry taken</span>'
              '<span class="sp"></span>'
              '<i style="background:rgba(255,255,255,.045)"></i><span>nothing happened'
              '</span><span class="sp"></span>'
              + info(
                  "The calendar",
                  f"{day_ramp}, four steps a side, cut at this strategy's own quartiles "
                  "rather than a fixed rupee threshold — so the shade means \u201cbig for "
                  "this strategy\u201d, which is the only comparison available on one "
                  "page. A day is empty when nothing closed, which on a weekly cadence is "
                  "most of them, and outlined when a trade was skipped for want of "
                  "margin. The percentage on each month is that month\u2019s return on "
                  "the equity standing when it began, so the months do not add up to the "
                  "year \u2014 they compound. Click any day to open it, and hover any "
                  "day for its numbers.")
              + '</div>')

    return f"""
<section class="acct q1" id="capital">
  <div class="sh"><span class="num">{_e(num)}</span><h2>On an account</h2>
    {info("On an account", lede)}</div>

  {controls}
  {est}

  <div class="cv-ret">
    <div class="cv-loz cv-fl" id="cvHero"></div>
    <div class="cv-f" id="cvFacts"></div>
  </div>

  <div class="cv-cal" id="cvCal"></div>
  {legend}

  <div class="cv-day" id="cvDay" hidden></div>
</section>
<script>window.__CV__ = {json.dumps(data, separators=(",", ":"))};</script>
<script>{JS}</script>
"""


def _rs(v):
    if v is None:
        return "—"
    a = abs(v)
    s = "−" if v < 0 else ""
    if a >= 1e7:
        return f"{s}₹{a / 1e7:.2f} Cr"
    if a >= 1e5:
        return f"{s}₹{a / 1e5:.2f} L"
    if a >= 1000:
        return f"{s}₹{a / 1000:.1f}k"
    return f"{s}₹{a:,.0f}"
