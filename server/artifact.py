"""A Stratify report as one self-contained HTML document.

WHAT THIS IS FOR. A Claude user asks for a backtest and wants to keep the result. An MCP
server cannot create an Artifact -- only the model can -- so the reliable path is to hand
the model a FINISHED document and tell it to publish that verbatim. The alternative, giving
the model numbers and asking it to draw the charts, means every report is re-derived by a
language model from a table, which is exactly how a figure ends up different from the
backtest that produced it.

The same document is the ChatGPT Canvas artifact, the Gemini Canvas artifact, and the page
served at /r/{token}. One renderer, and every surface shows identical numbers because they
are the same bytes.

CONSTRAINTS THIS DOCUMENT MEETS, because an artifact sandbox enforces them:
  - zero external requests. No CDN, no font host, no image URL. System font stack.
  - one file. Inline CSS, inline JS, data embedded as JSON.
  - theme-aware in all three states: explicit light, explicit dark, and the unstamped
    default where only prefers-color-scheme separates them.
  - no horizontal page scroll; wide things scroll inside their own box.

THE DISCLAIMER IS LOAD-BEARING TOO. "Not investment advice" appears verbatim in the footer
and a test asserts it. This document is the thing users forward to other people, so it is
the one surface that must carry the disclaimer on its own -- pending the securities-lawyer
review that is still open in the operating plan.

ORDERING IS LOAD-BEARING. The honesty panel comes BEFORE the P&L numbers. A report that
leads with a number invites the reader to stop at the number, and the number is the least
reliable thing on the page. This was inverted once during a rewrite and a test caught it;
the test is server/tests/test_server.py::test_report_renders_and_leads_with_the_honesty_panel.

RELEASE. This document embeds exactly what the payload already released and nothing more.
It is a rendering of a tool result, not a second, richer channel -- so a summary-detail
backtest produces a report with no per-trade prices in it, because there were none to draw.
"""
import html
import json

from .design import CHART_CSS, CHART_JS, FONT, MARK_CSS, MARK_HTML, MONO, TOKENS

CSS = TOKENS + CHART_CSS + MARK_CSS + """
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--body);font:400 15px/1.6 FONTSTACK;
-webkit-font-smoothing:antialiased}
.wrap{max-width:60rem;margin:0 auto;padding:34px 20px 72px}
.mast{border-bottom:2px solid var(--ink);padding-bottom:16px;margin-bottom:22px}
.kick{font:600 10.5px/1 MONOSTACK;letter-spacing:.14em;text-transform:uppercase;
color:var(--accent);margin-bottom:10px}
h1{font-size:clamp(24px,4vw,33px);line-height:1.12;margin:0 0 8px;color:var(--ink);
letter-spacing:-.015em;font-weight:600}
.sub{color:var(--muted);font-size:14px;margin:0}
.mmeta{display:flex;flex-wrap:wrap;gap:6px 18px;margin-top:14px;font:400 11.5px/1 MONOSTACK;
color:var(--faint)}
h2{font:600 10.5px/1 MONOSTACK;letter-spacing:.12em;text-transform:uppercase;
color:var(--muted);margin:36px 0 12px}
h2:first-of-type{margin-top:6px}
p{margin:0 0 12px;max-width:68ch}
.verdict{border-radius:11px;padding:16px 18px;margin-bottom:22px}
.verdict .vt{font:600 10px/1 MONOSTACK;letter-spacing:.13em;text-transform:uppercase;
display:block;margin-bottom:8px}
.verdict h3{margin:0 0 8px;font-size:19px;font-weight:600;color:var(--ink)}
.verdict p{margin:0;font-size:14.5px;max-width:70ch}
.verdict p+p{margin-top:9px}
.v-strong{background:var(--good-soft)}.v-strong .vt{color:var(--good)}
.v-weak{background:var(--warn-soft)}.v-weak .vt{color:var(--warn)}
.v-poor{background:var(--crit-soft)}.v-poor .vt{color:var(--crit)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(136px,1fr));gap:10px}
.stat{background:var(--surface);border:1px solid var(--line);border-radius:9px;
padding:11px 13px}
.stat .k{font:600 9.5px/1.3 MONOSTACK;letter-spacing:.08em;text-transform:uppercase;
color:var(--faint)}
.stat .v{font-size:20px;line-height:1.2;margin-top:4px;color:var(--ink);font-weight:600;
font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.stat .v.g{color:var(--good)}.stat .v.b{color:var(--crit)}
.stat .s{font-size:11.5px;color:var(--faint);margin-top:3px;line-height:1.4}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:11px;
padding:6px 4px 2px;box-shadow:var(--shadow);margin-bottom:12px;overflow-x:auto}
.panel svg{display:block;min-width:460px;width:100%}
.scroll{overflow-x:auto;background:var(--surface);border:1px solid var(--line);
border-radius:11px;box-shadow:var(--shadow)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--soft);
font-variant-numeric:tabular-nums;white-space:nowrap}
tbody tr:last-child td{border-bottom:0}
th{font:600 9.5px/1.3 MONOSTACK;letter-spacing:.07em;text-transform:uppercase;
color:var(--muted)}
td.n,th.n{text-align:right}
td.w{white-space:normal;min-width:15rem}
.g{color:var(--good)}.b{color:var(--crit)}
.pill{display:inline-block;font:600 9.5px/1 MONOSTACK;letter-spacing:.09em;
text-transform:uppercase;padding:4px 6px;border-radius:3px}
.p-ok{background:var(--good-soft);color:var(--good)}
.p-no{background:var(--crit-soft);color:var(--crit)}
ul{margin:0 0 12px;padding-left:20px;max-width:68ch}
li{margin:5px 0;line-height:1.5}
.note{font-size:12.5px;line-height:1.55;color:var(--faint);border-left:2px solid var(--line);
padding-left:11px;margin:10px 0 0;max-width:70ch}
code{font:500 12.5px/1 MONOSTACK;background:var(--sunk);border:1px solid var(--soft);
border-radius:4px;padding:2px 5px}
.foot{margin-top:44px;border-top:1px solid var(--line);padding-top:16px}
.foot p{font-size:12.5px;line-height:1.6;color:var(--faint);max-width:72ch}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
""".replace("FONTSTACK", FONT).replace("MONOSTACK", MONO)

JS = "(function(){" + CHART_JS + r"""
function host(id){return document.getElementById(id)}

// -------- equity curve with the underwater plot beneath it
function equity(rows){
 var h=host('c-eq');if(!h||!rows||rows.length<2)return;
 var W=760,H=310,SP=206,L=70,R=18,Tp=16,B=24;
 var eq=rows.map(function(r){return r[2]}),dd=rows.map(function(r){return r[3]});
 var s=S(h,W,H),x=sc(0,eq.length-1,L,W-R);
 var y=sc(Math.min.apply(null,eq.concat([0]))*1.06,Math.max.apply(null,eq.concat([0]))*1.08,SP-B,Tp);
 var y2=sc(Math.min.apply(null,dd)*1.06||-1,0,H-14,SP+8);
 P('M'+L+' '+y(0)+'H'+(W-R),{class:'zero'},s);
 var d=line(eq,x,y);
 P(d+'L'+x(eq.length-1)+' '+y(0)+'L'+L+' '+y(0)+'Z',{class:'ar'},s);P(d,{class:'ln'},s);
 T(M(eq[eq.length-1]),L-8,y(eq[eq.length-1])+4,'tk',s,'end');
 T('cumulative net P&L, one lot',L,Tp+2,'cap',s);
 var d2=line(dd,x,y2);
 P(d2+'L'+x(dd.length-1)+' '+y2(0)+'L'+L+' '+y2(0)+'Z',{class:'ar-b'},s);
 P(d2,{class:'ln-b'},s);
 var w=Math.min.apply(null,dd),wi=dd.indexOf(w);
 E('circle',{cx:x(wi),cy:y2(w),r:3.5,class:'db'},s);
 T('worst drawdown '+M(w),Math.min(x(wi)+7,W-R-160),y2(w)+4,'lb',s);
 T('underwater',L,SP+18,'cap',s);
 T(D.rows[0][0],L,H-3,'tk',s);T(D.rows[D.rows.length-1][0],W-R,H-3,'tk',s,'end');
}
// -------- walk-forward folds
function folds(f){
 var h=host('c-fold');if(!h||!f||!f.length)return;
 var W=760,H=210,L=64,R=18,Tp=26,B=42,s=S(h,W,H);
 var v=f.map(function(o){return o.pnl_rupees});
 var y=sc(Math.min.apply(null,v.concat([0]))*1.35,Math.max.apply(null,v.concat([0]))*1.15,H-B,Tp);
 var bw=(W-R-L)/f.length;
 P('M'+L+' '+y(0)+'H'+(W-R),{class:'zero'},s);
 f.forEach(function(o,i){
  var cx=L+bw*i+bw/2,w=Math.min(bw*.5,64),p=o.pnl_rupees,neg=p<0;
  E('rect',{x:cx-w/2,y:y(Math.max(p,0)),width:w,height:Math.abs(y(p)-y(0)),
   class:neg?'bb':'bg',rx:2},s);
  T(M(p),cx,y(Math.max(p,0))-6,neg?'lb':'lg',s,'middle');
  T(o.from.slice(2,7)+'→'+o.to.slice(2,7),cx,H-B+15,'tk',s,'middle');
  T(o.n_trades+' trades',cx,H-B+28,'tk',s,'middle');});
 T('read as a sequence, not a total — decay is invisible in the sum',L,Tp-9,'cap',s);
}
// -------- gross to net
function costs(c){
 var h=host('c-cost');if(!h||!c)return;
 var W=760,H=225,L=64,R=18,Tp=24,B=40,s=S(h,W,H);
 var st=[['gross edge',c.gross_points_before_costs,'a'],
         ['charges',-Math.abs(c.charges_points),'b'],
         ['slippage',-Math.abs(c.slippage_points),'b'],
         ['net',c.net_points_after_costs,'n']];
 var lo=0,hi=0,run=0;
 st.forEach(function(o){if(o[2]==='n')return;run+=o[1];lo=Math.min(lo,run);hi=Math.max(hi,run)});
 lo=Math.min(lo,c.net_points_after_costs,0);hi=Math.max(hi,c.gross_points_before_costs,0);
 var y=sc(lo*1.15-1,hi*1.15+1,H-B,Tp),bw=(W-R-L)/st.length;run=0;
 P('M'+L+' '+y(0)+'H'+(W-R),{class:'zero'},s);
 st.forEach(function(o,i){
  var cx=L+bw*i+bw/2,w=Math.min(bw*.46,70);
  var a=o[2]==='n'?0:run,b=o[2]==='n'?o[1]:run+o[1];
  var t=Math.max(a,b),bt=Math.min(a,b);
  E('rect',{x:cx-w/2,y:y(t),width:w,height:Math.max(1,Math.abs(y(t)-y(bt))),
   class:o[2]==='n'?'ba':(o[2]==='b'?'bb':'bg'),rx:2},s);
  T(o[1].toFixed(1),cx,y(t)-6,o[2]==='b'?'lb':(o[2]==='n'?'la':'lg'),s,'middle');
  T(o[0],cx,H-B+15,'tk',s,'middle');
  if(o[2]!=='n'){run=b;if(i<st.length-2)P('M'+(cx+w/2)+' '+y(run)+'H'+(cx+bw-w/2),{class:'dash'},s)}});
 T('points per lot. '+(c.share_of_edge_surviving==null
   ?'The gross edge was not positive, so costs did not merely reduce it — they are the whole loss.'
   :Math.round(100-c.share_of_edge_surviving*100)+'% of the gross edge went to friction.'),
   L,Tp-9,'cap',s);
}
// -------- month grid
function months(by){
 var h=host('c-mon');if(!h||!by)return;
 var ks=Object.keys(by).sort(),yrs=[],mp={};
 ks.forEach(function(k){var y=k.slice(0,4);if(yrs.indexOf(y)<0)yrs.push(y);mp[k]=by[k]});
 var W=760,H=48+yrs.length*34,L=52,R=18,Tp=30,B=18,s=S(h,W,H);
 var cw=(W-R-L)/12,ch=(H-B-Tp)/yrs.length;
 var mx=Math.max.apply(null,ks.map(function(k){return Math.abs(by[k].pnl_rupees)}))||1;
 ['J','F','M','A','M','J','J','A','S','O','N','D'].forEach(function(m,i){
  T(m,L+cw*i+cw/2,Tp-8,'tk',s,'middle')});
 yrs.forEach(function(yr,yi){
  T(yr,L-8,Tp+ch*yi+ch/2+4,'tk',s,'end');
  for(var mi=0;mi<12;mi++){
   var k=yr+'-'+String(mi+1).padStart(2,'0'),o=mp[k];if(!o)continue;
   var a=Math.min(.94,Math.abs(o.pnl_rupees)/mx*.8+.14);
   E('rect',{x:L+cw*mi+1.5,y:Tp+ch*yi+1.5,width:cw-3,height:ch-3,rx:2,
    class:o.pnl_rupees>=0?'dg':'db','fill-opacity':a.toFixed(2)},s);}});
 T('darker is larger, in either direction',L,H-3,'cap',s);
}
// -------- per-trade P&L, ordered as they happened
function dist(tr){
 var h=host('c-dist');if(!h||!tr||tr.length<4)return;
 var v=tr.map(function(t){return t.pnl_rupees}).filter(function(x){return x!=null});
 if(v.length<4)return;
 var W=760,H=215,L=64,R=18,Tp=24,B=32,s=S(h,W,H);
 var lo=Math.min.apply(null,v),hi=Math.max.apply(null,v),NB=Math.min(29,Math.max(9,Math.round(v.length/4)));
 var b=new Array(NB).fill(0);
 v.forEach(function(x){b[Math.min(NB-1,Math.floor((x-lo)/((hi-lo)||1)*NB))]++});
 var x=sc(0,NB-1,L,W-R),y=sc(0,Math.max.apply(null,b)*1.12,H-B,Tp),bw=(W-R-L)/NB;
 b.forEach(function(c,i){if(!c)return;
  var mid=lo+(i+.5)/NB*(hi-lo);
  E('rect',{x:x(i)-bw*.42,y:y(c),width:bw*.84,height:(H-B)-y(c),
   class:mid>=0?'bg':'bb',rx:1},s);});
 if(lo<0&&hi>0){var z=(0-lo)/(hi-lo)*(NB-1);P('M'+x(z)+' '+Tp+'V'+(H-B),{class:'zero'},s);}
 T(M(lo),L,H-B+15,'tk',s,'middle');T(M(hi),W-R,H-B+15,'tk',s,'middle');
 T('one bar per group of trades. The left tail is the strategy.',L,Tp-9,'cap',s);
}
// -------- how trades ended
function exits(by){
 var h=host('c-exit');if(!h||!by)return;
 var ks=Object.keys(by);if(!ks.length)return;
 var tot=ks.reduce(function(a,k){return a+by[k].n_trades},0);
 var W=760,H=112,L=18,R=18,Tp=30,s=S(h,W,H),cx=L;
 var cls={EXPIRY:'ba',TARGET:'bg',STOP:'bb',SL:'bb',TP:'bg',TIME:'bm'};
 ks.sort(function(a,b){return by[b].n_trades-by[a].n_trades}).forEach(function(k){
  var w=by[k].n_trades/tot*(W-L-R);
  E('rect',{x:cx,y:Tp,width:Math.max(1,w-2),height:26,class:cls[k]||'bm',rx:3},s);
  if(w>34)T(by[k].n_trades,cx+w/2-1,Tp+17,'cl',s,'middle');
  T(k.toLowerCase()+(w>90?' · '+M(by[k].pnl_rupees):''),cx,Tp+44,'tk',s);
  cx+=w;});
 T('a stop that never fires is decorative; a target that always fires is cutting winners',
   L,Tp-11,'cap',s);
}
try{equity(D.rows);folds(D.folds);costs(D.costs);months(D.months);dist(D.trades);
 exits(D.exits);}catch(e){}
})();
"""


def _e(v):
    return html.escape("" if v is None else str(v))


def _money(v):
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e7:
        s = f"₹{a / 1e7:.2f}Cr"
    elif a >= 1e5:
        s = f"₹{a / 1e5:.2f}L"
    elif a >= 1000:
        s = f"₹{a / 1000:.1f}k"
    else:
        s = f"₹{a:,.0f}"
    return ("−" if v < 0 else "") + s


def _pct(v, dp=1):
    return "—" if v is None else f"{v * 100:.{dp}f}%"


def _stat(k, v, sub="", cls=""):
    tail = f'<div class="s">{_e(sub)}</div>' if sub else ""
    return (f'<div class="stat"><div class="k">{_e(k)}</div>'
            f'<div class="v {cls}">{_e(v)}</div>{tail}</div>')


VERDICT_CLASS = {"strong_evidence": "v-strong", "moderate_evidence": "v-strong",
                 "weak_evidence": "v-weak", "insufficient_evidence": "v-poor",
                 "no_evidence": "v-poor"}


def _spec_line(spec):
    params = ", ".join(f"{k} {v}" for k, v in sorted((spec.get("params") or {}).items()))
    bits = [spec.get("structure", "?")]
    if params:
        bits.append(params)
    bits.append(f"entry {spec.get('entry_time', '?')}")
    if spec.get("exit_time"):
        bits.append(f"exit {spec['exit_time']}")
    bits.append(spec.get("cadence", "weekly"))
    if spec.get("gate") and spec["gate"] != "always":
        bits.append(f"gate {spec['gate']}")
    return " · ".join(bits)


def _honesty_table(h):
    oos = h.get("out_of_sample") or {}
    mc = h.get("multiple_comparisons") or {}
    rows = []

    def row(name, verdict, ok, detail):
        rows.append(f'<tr><td class="name">{_e(name)}</td>'
                    f'<td><span class="pill {"p-ok" if ok else "p-no"}">{_e(verdict)}</span></td>'
                    f'<td class="w">{detail}</td></tr>')

    ins, outs = oos.get("in_sample") or {}, oos.get("out_of_sample") or {}
    row("Out of sample", "held up" if oos.get("held_up") else "did not hold",
        bool(oos.get("held_up")),
        f'Chronological 70/30. In-sample {_money(ins.get("pnl_rupees"))} over '
        f'{ins.get("n_trades", "?")} trades; held-out {_money(outs.get("pnl_rupees"))} over '
        f'{outs.get("n_trades", "?")}. Never a random split, which would leak across cycles.')

    wf = h.get("walk_forward") or []
    prof = sum(1 for f in wf if f.get("profitable"))
    row("Walk-forward", f"{prof} of {len(wf)}", len(wf) and prof > len(wf) / 2,
        "Each fold trains on what came before it and is judged on what came after. "
        "The worst fold matters more than the total.")

    ci = h.get("bootstrap_ci_95_return_on_margin") or []
    if len(ci) == 2:
        row("Bootstrap 95% interval", "spans zero" if ci[0] < 0 < ci[1] else "excludes zero",
            not (ci[0] < 0 < ci[1]),
            f"Return on margin between {_pct(ci[0], 2)} and {_pct(ci[1], 2)}. "
            f"An interval that spans zero means the sign of the edge is not established.")

    dsp = mc.get("deflated_sharpe_probability")
    row("Multiple comparisons",
        "—" if dsp is None else f"{dsp * 100:.0f}% real",
        dsp is not None and dsp >= 0.5,
        f'{mc.get("variants_tested_last_24h", "?")} variant(s) tried in the last 24h on '
        f'{_e(mc.get("scope", "this key"))}. The more you try, the better the best one looks '
        f'by chance alone, and this is the correction for it.')

    cd = h.get("cost_drag") or {}
    surv = cd.get("share_of_edge_surviving")
    row("Cost sensitivity",
        "costs exceed edge" if surv is None else f"{surv * 100:.0f}% survives",
        surv is not None and surv > 0.4,
        f'Gross {cd.get("gross_points_before_costs", "?")} points, charges '
        f'{cd.get("charges_points", "?")}, slippage {cd.get("slippage_points", "?")}, '
        f'net {cd.get("net_points_after_costs", "?")}.')
    return "".join(rows)


def _trades_table(trades, show_prices):
    if not trades:
        return ""
    head = ("<tr><th class='n'>#</th><th>Entry</th><th>Exit</th><th>Why</th>"
            "<th class='n'>Net</th><th class='n'>ROM</th>"
            + ("<th class='w'>Legs</th>" if show_prices else "") + "</tr>")
    body = []
    for t in trades:
        legs = ""
        if show_prices and t.get("legs"):
            legs = "<td class='w'>" + "<br>".join(
                f'{_e(l.get("action"))} {_e(l.get("strike"))} {_e(l.get("option_type"))} '
                f'@ {l.get("entry_price")}'
                + (f' → {l["exit_price"]}' if l.get("exit_price") is not None else "")
                for l in t["legs"]) + "</td>"
        pnl = t.get("pnl_rupees")
        rom = t.get("return_on_margin")
        body.append(
            f'<tr><td class="n">{t.get("n", "")}</td><td>{_e(t.get("entry"))}</td>'
            f'<td>{_e(t.get("exit"))}</td><td>{_e(t.get("exit_reason"))}</td>'
            f'<td class="n {"g" if (pnl or 0) >= 0 else "b"}">{_money(pnl)}</td>'
            f'<td class="n">{_pct(rom, 2) if rom is not None else "—"}</td>{legs}</tr>')
    return (f'<div class="scroll"><table><thead>{head}</thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def render(payload, backtest_id, report_url=None):
    """One self-contained HTML document. Nothing in it that is not in `payload`."""
    s = payload.get("summary") or {}
    h = payload.get("honesty") or {}
    spec = payload.get("spec") or {}
    interp = payload.get("interpretation") or {}
    bd = payload.get("breakdown") or {}
    curve = payload.get("equity_curve") or {}
    trades = payload.get("trades") or []
    detail = payload.get("trade_detail") or {}
    ratios = s.get("ratios") or {}

    pnl = s.get("total_pnl_rupees")
    health = h.get("health_score")
    verdict = h.get("verdict", "")
    vclass = VERDICT_CLASS.get(verdict, "v-weak")

    data = {
        "rows": curve.get("rows") or [],
        "folds": h.get("walk_forward") or [],
        "costs": h.get("cost_drag") or {},
        "months": bd.get("by_month") or {},
        "exits": bd.get("by_exit_reason") or {},
        "trades": [{"pnl_rupees": t.get("pnl_rupees")} for t in trades],
    }

    stats = "".join([
        _stat("Net P&L", _money(pnl), f'{s.get("n_trades", "?")} trades',
              "g" if (pnl or 0) >= 0 else "b"),
        _stat("Win rate", _pct(s.get("win_rate"), 1), "share of trades in profit"),
        _stat("Max drawdown", _money(s.get("max_drawdown_rupees")), "peak to trough", "b"),
        _stat("Return on margin", _pct(s.get("mean_return_on_margin"), 2), "mean per trade"),
        _stat("Sharpe", ratios.get("sharpe", "—"), "annualised by observed frequency"),
        _stat("Profit factor", ratios.get("profit_factor", "—"), "gross win ÷ gross loss"),
        _stat("Peak margin", f'{s.get("peak_margin_points", "—")} pts', "per lot, worst cycle"),
        _stat("Charges", _money(s.get("total_charges_rupees")), "brokerage, taxes, GST", "b"),
        _stat("Health", f'{health}/100' if health is not None else "—",
              _e(verdict.replace("_", " ")),
              "g" if (health or 0) >= 50 else "b"),
    ])

    reading = "".join(f"<li>{_e(x)}</li>" for x in (interp.get("reading") or []))
    dont = "".join(f"<li>{_e(x)}</li>" for x in (interp.get("do_not_conclude") or []))
    nxt = "".join(f"<li>{_e(x)}</li>" for x in (interp.get("next_step") or []))
    notes = "".join(f"<li>{_e(x)}</li>" for x in
                    (s.get("notes") or []) + (s.get("warnings") or []))

    def panel(title, cid, when=True):
        return f'<h2>{title}</h2><div class="panel" id="{cid}"></div>' if when else ""

    show_prices = bool(detail.get("prices_included"))
    trunc = detail.get("truncation")

    body = f"""<div class="wrap">
<header class="mast">
  <div class="kick">Stratify · backtest report</div>
  <h1>{_e(spec.get("structure", "Backtest").replace("_", " ").title())} on NIFTY weekly options</h1>
  <p class="sub">{_e(_spec_line(spec))}</p>
  <div class="mmeta">
    <span>{_e((s.get("period") or {}).get("from", "?"))} → {_e((s.get("period") or {}).get("to", "?"))}</span>
    <span>{s.get("n_trades", "?")} trades</span>
    <span>1-minute data, net of real charges and slippage</span>
    <span>{_e(backtest_id)}</span>
  </div>
</header>

<div class="verdict {vclass}">
  <span class="vt">Verdict · {_e(verdict.replace("_", " "))}</span>
  <h3>{_money(pnl)} over {s.get("n_trades", "?")} trades, health {health}/100</h3>
  <p>{_e(h.get("explanation", ""))}</p>
</div>

<h2>What the evidence supports</h2>
<div class="scroll"><table>
  <thead><tr><th>Check</th><th>Result</th><th class="w">What it means</th></tr></thead>
  <tbody>{_honesty_table(h)}</tbody>
</table></div>

<h2>The numbers</h2>
<div class="grid">{stats}</div>

{panel("Equity and drawdown", "c-eq", bool(data["rows"]))}
{panel("Walk-forward folds", "c-fold", bool(data["folds"]))}
{panel("Gross to net", "c-cost", bool(data["costs"]))}
{panel("Month by month", "c-mon", bool(data["months"]))}
{panel("Distribution of trade P&amp;L", "c-dist", len(data["trades"]) >= 4)}
{panel("How trades ended", "c-exit", bool(data["exits"]))}

<h2>How to read this</h2>
{f"<ul>{reading}</ul>" if reading else ""}
{f"<h2>What this does not show</h2><ul>{dont}</ul>" if dont else ""}
{f"<h2>Sensible next step</h2><ul>{nxt}</ul>" if nxt else ""}

{f'<h2>Trades</h2>{_trades_table(trades, show_prices)}' if trades else ""}
{f'<p class="note">{_e(trunc)}</p>' if trunc else ""}

{f"<h2>Caveats carried by this run</h2><ul>{notes}</ul>" if notes else ""}

<footer class="foot">
  <p>Entry and exit at the traded price on the 1-minute bar, not the midpoint, plus slippage
  from measured bid-ask. Stops and targets are evaluated on 1-minute bars. Expiry settles on
  the NSE convention — the mean of the index over the final 30 minutes, not the closing
  print. Lot size is the one in force on each trade date.</p>
  {MARK_HTML}
  <p><strong>Not investment advice.</strong> This is evidence about the past under stated
  assumptions, not a recommendation and not a claim about the future. Backtested results
  are hypothetical and carry no guarantee that any strategy will achieve them.{
  f' Source of record: {_e(report_url)}' if report_url else ''}</p>
</footer>
</div>
<script>var D={json.dumps(data, separators=(",", ":"), default=str)};{JS}</script>"""

    title = f'Stratify · {spec.get("structure", "backtest")} · {_money(pnl)}'
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{_e(title)}</title><style>{CSS}</style></head>'
            f'<body>{body}</body></html>')
