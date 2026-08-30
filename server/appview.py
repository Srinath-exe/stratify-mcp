"""The inline chat view: charts rendered INSIDE the conversation, not behind a link.

This is an MCP Apps template (extension io.modelcontextprotocol/ui, spec 2026-01-26). The
host fetches it once by URI, renders it in a sandboxed iframe attached to the tool result,
and pushes the result in over a postMessage JSON-RPC bridge.

WHY THIS IS DIFFERENT FROM THE REPORT, and worth having as well as it:

  The report is model-mediated. The model has to decide to call build_report, and the
  document costs ~8,000 tokens every time it passes through the conversation.

  This is not. The host fetches this template DIRECTLY by URI -- it never enters the
  model's context, so it costs ZERO tokens per call, however many charts it draws. And the
  model is not in the loop at all: it cannot forget to show it, cannot decide the user does
  not need it, and cannot redraw it wrong. It is a property of the tool.

DATA ARRIVES BY NOTIFICATION, not by templating. The host sends ui/notifications/tool-result
with `content` and `structuredContent`, and structuredContent is our whole backtest payload
-- the same object the report renders from. So this file ships no data; it ships a renderer
that waits for one.

TWO LAYOUTS, chosen from hostContext.displayMode. Inline is a narrow card in the message
flow: verdict, four numbers, the equity curve, the folds. Fullscreen adds the month grid,
the cost waterfall and the distribution. An inline view is roughly 400px wide -- the full
report squeezed into it would be unreadable, so it is not the full report squeezed into it.

DEGRADES TO NOTHING. A host that does not implement the extension ignores the tool's
_meta.ui field and never fetches this, and the caller still gets the text brief and the
report link. There is no path where adding this makes anything worse.
"""
from .design import CHART_CSS, CHART_JS, FONT, MARK_HTML, MONO, TOKENS

URI = "ui://stratify/backtest"
MIME = "text/html;profile=mcp-app"

CSS = TOKENS + CHART_CSS + """
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{background:var(--bg);color:var(--body);font:400 13.5px/1.5 FONTSTACK;
-webkit-font-smoothing:antialiased}
.v{padding:12px 13px 14px}
.hd{display:flex;align-items:baseline;justify-content:space-between;gap:10px;
flex-wrap:wrap;margin-bottom:10px}
.ti{font:600 15px/1.25 FONTSTACK;color:var(--ink);letter-spacing:-.01em;margin:0}
.sp{font:400 10.5px/1.3 MONOSTACK;color:var(--faint);margin-top:3px}
.vd{font:600 9.5px/1 MONOSTACK;letter-spacing:.1em;text-transform:uppercase;
padding:5px 7px;border-radius:3px;white-space:nowrap;flex:none}
.vd.ok{background:var(--good-soft);color:var(--good)}
.vd.mid{background:var(--warn-soft);color:var(--warn)}
.vd.bad{background:var(--crit-soft);color:var(--crit)}
.st{display:grid;grid-template-columns:repeat(auto-fit,minmax(78px,1fr));gap:7px;
margin-bottom:12px}
.s{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:8px 9px}
.s .k{font:600 8.5px/1.25 MONOSTACK;letter-spacing:.07em;text-transform:uppercase;
color:var(--faint)}
.s .n{font:600 16px/1.2 FONTSTACK;color:var(--ink);margin-top:3px;letter-spacing:-.02em;
font-variant-numeric:tabular-nums}
.s .n.g{color:var(--good)}.s .n.b{color:var(--crit)}
.ch{background:var(--surface);border:1px solid var(--line);border-radius:9px;
padding:6px 5px 3px;margin-bottom:9px;overflow:hidden}
.cl{font:600 8.5px/1 MONOSTACK;letter-spacing:.1em;text-transform:uppercase;
color:var(--faint);padding:5px 7px 2px}
.ev{background:var(--surface);border:1px solid var(--line);border-radius:9px;
padding:9px 11px;margin-bottom:9px;font-size:12.5px;line-height:1.5}
.ev div+div{margin-top:5px}
.ev b{color:var(--ink);font-weight:600}
.ev .no{color:var(--crit)}.ev .yes{color:var(--good)}
.ac{display:flex;gap:7px;flex-wrap:wrap;margin-top:11px}
button{appearance:none;cursor:pointer;border:1px solid var(--line);background:var(--surface);
color:var(--body);border-radius:7px;padding:7px 11px;font:500 12px/1 FONTSTACK}
button.p{background:var(--accent);border-color:var(--accent);color:#fff}
button:hover{border-color:var(--accent)}
.wait{padding:22px 14px;color:var(--faint);font:400 12.5px/1.5 FONTSTACK;text-align:center}
.mark{display:flex;align-items:center;gap:7px;margin-top:11px;padding-top:9px;
border-top:1px solid var(--line)}
.mark .glyph{width:15px;height:15px;flex:none}
.mark .txt{font:600 8.5px/1.3 MONOSTACK;letter-spacing:.09em;text-transform:uppercase;
color:var(--faint)}
.mark .txt b{color:var(--accent);font-weight:600}
.wide{display:none}
body.full .wide{display:block}
body.full .v{max-width:56rem;margin:0 auto;padding:20px}
body.full .st{grid-template-columns:repeat(auto-fit,minmax(110px,1fr))}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
""".replace("FONTSTACK", FONT).replace("MONOSTACK", MONO)

JS = r"""
(function(){
CHARTPRIMS

// ---- postMessage JSON-RPC bridge to the host -------------------------------------
var nextId=1, pending={}, ctx={displayMode:'inline',theme:null}, payload=null;
function send(msg){ (window.parent||window).postMessage(msg,'*') }
function request(method,params){
  return new Promise(function(res,rej){
    var id=nextId++; pending[id]={res:res,rej:rej};
    send({jsonrpc:'2.0',id:id,method:method,params:params||{}});
  });
}
function notify(method,params){ send({jsonrpc:'2.0',method:method,params:params||{}}) }

window.addEventListener('message',function(ev){
  var m=ev.data; if(!m||m.jsonrpc!=='2.0')return;
  // A response is a message carrying result OR error -- not merely one with an id.
  // Checking the id alone made the view resolve its OWN outgoing requests in any context
  // where parent === self, which is every test harness and any host that renders the
  // template in the same window rather than a child frame.
  if(m.id!=null&&pending[m.id]&&('result' in m||'error' in m)){
    var p=pending[m.id]; delete pending[m.id];
    return m.error?p.rej(m.error):p.res(m.result);
  }
  if(m.method==null)return;
  if(m.method==='ui/notifications/tool-result'){
    // structuredContent is the whole backtest payload -- the same object the hosted
    // report renders from. It is delivered here for RENDERING and is deliberately not
    // part of the model's context, so drawing it costs the conversation nothing.
    payload=(m.params||{}).structuredContent||null; draw();
  } else if(m.method==='ui/notifications/host-context-changed'){
    applyContext((m.params||{}).hostContext||m.params||{}); draw();
  } else if(m.method==='ui/resource-teardown'){
    send({jsonrpc:'2.0',id:m.id,result:{}});
  }
});

function applyContext(c){
  if(!c)return;
  if(c.theme){ ctx.theme=c.theme;
    document.documentElement.setAttribute('data-theme',c.theme==='dark'?'dark':'light'); }
  if(c.displayMode){ ctx.displayMode=c.displayMode;
    document.body.classList.toggle('full',c.displayMode==='fullscreen'); }
  if(c.containerDimensions){
    var d=c.containerDimensions;
    // Fixed height means the host owns the size; a maxHeight means we do, and we must
    // report our own with ui/notifications/size-changed or the frame stays collapsed.
    if('height' in d) document.documentElement.style.height='100vh';
    else if(d.maxHeight) document.documentElement.style.maxHeight=d.maxHeight+'px';
  }
}

var lastH=0;
function measure(){
  var b=document.body,e=document.documentElement;
  return Math.ceil(Math.max(b.scrollHeight||0,b.offsetHeight||0,e.scrollHeight||0,
    e.offsetHeight||0,(b.getBoundingClientRect()||{}).height||0));
}
function reportSize(){
  // In flexible mode the host sizes the frame from THIS notification. Reporting nothing
  // leaves it collapsed, so a measurement of zero -- which is what any environment
  // without layout returns -- must not be allowed to mean "stay silent". Fall back to a
  // height that shows the content rather than to nothing.
  var h=measure()||(ctx.displayMode==='fullscreen'?900:560);
  if(Math.abs(h-lastH)>3){ lastH=h;
    notify('ui/notifications/size-changed',
      {width:document.body.scrollWidth||document.documentElement.clientWidth||420,height:h}); }
}

// ---- rendering --------------------------------------------------------------------
function pct(v,d){ return v==null?'—':(v*100).toFixed(d==null?1:d)+'%' }
function el(t,c,p,txt){var n=document.createElement(t);if(c)n.className=c;
 if(txt!=null)n.textContent=txt;if(p)p.appendChild(n);return n}
function stat(p,k,v,cls){var s=el('div','s',p);el('div','k',s,k);
 var n=el('div','n'+(cls?' '+cls:''),s);n.textContent=v}
function chart(p,label,id){var w=el('div','ch',p);if(label)el('div','cl',w,label);
 var h=el('div',null,w);h.id=id;return h}

function equity(h,rows){
  if(!rows||rows.length<2)return;
  var W=520,H=150,SP=104,L=52,R=10,Tp=10,B=14;
  var eq=rows.map(function(r){return r[2]}),dd=rows.map(function(r){return r[3]});
  var s=S(h,W,H),x=sc(0,eq.length-1,L,W-R);
  var y=sc(Math.min.apply(null,eq.concat([0]))*1.06,Math.max.apply(null,eq.concat([0]))*1.08,SP-B,Tp);
  var y2=sc(Math.min.apply(null,dd)*1.08||-1,0,H-6,SP+4);
  P('M'+L+' '+y(0)+'H'+(W-R),{class:'zero'},s);
  var d=line(eq,x,y);
  P(d+'L'+x(eq.length-1)+' '+y(0)+'L'+L+' '+y(0)+'Z',{class:'ar'},s);
  P(d,{class:'ln'},s);
  T(M(eq[eq.length-1]),L-6,y(eq[eq.length-1])+4,'tk',s,'end');
  var d2=line(dd,x,y2);
  P(d2+'L'+x(dd.length-1)+' '+y2(0)+'L'+L+' '+y2(0)+'Z',{class:'ar-b'},s);
  P(d2,{class:'ln-b'},s);
  var w=Math.min.apply(null,dd);
  T('worst '+M(w),L-6,y2(w)+4,'tk',s,'end');
}
function folds(h,f){
  if(!f||!f.length)return;
  var W=520,H=112,L=14,R=10,Tp=16,B=26,s=S(h,W,H);
  var v=f.map(function(o){return o.pnl_rupees});
  var y=sc(Math.min.apply(null,v.concat([0]))*1.3,Math.max.apply(null,v.concat([0]))*1.2,H-B,Tp);
  var bw=(W-R-L)/f.length;
  P('M'+L+' '+y(0)+'H'+(W-R),{class:'zero'},s);
  f.forEach(function(o,i){
    var cx=L+bw*i+bw/2,w=Math.min(bw*.56,44),p=o.pnl_rupees;
    E('rect',{x:cx-w/2,y:y(Math.max(p,0)),width:w,height:Math.abs(y(p)-y(0)),
      class:p<0?'bb':'bg',rx:2},s);
    T(M(p),cx,y(Math.max(p,0))-4,p<0?'lb':'lg',s,'middle');
    T(String(o.from).slice(2,7),cx,H-B+13,'tk',s,'middle');});
}
function months(h,by){
  if(!by)return; var ks=Object.keys(by).sort(); if(!ks.length)return;
  var yrs=[]; ks.forEach(function(k){var y=k.slice(0,4);if(yrs.indexOf(y)<0)yrs.push(y)});
  var W=520,H=30+yrs.length*22,L=36,R=10,Tp=20,B=8,s=S(h,W,H);
  var cw=(W-R-L)/12,ch=(H-B-Tp)/yrs.length;
  var mx=Math.max.apply(null,ks.map(function(k){return Math.abs(by[k].pnl_rupees)}))||1;
  'JFMAMJJASOND'.split('').forEach(function(m,i){T(m,L+cw*i+cw/2,Tp-6,'tk',s,'middle')});
  yrs.forEach(function(yr,yi){
    T(yr,L-6,Tp+ch*yi+ch/2+3.5,'tk',s,'end');
    for(var mi=0;mi<12;mi++){
      var o=by[yr+'-'+String(mi+1).padStart(2,'0')]; if(!o)continue;
      E('rect',{x:L+cw*mi+1,y:Tp+ch*yi+1,width:cw-2,height:ch-2,rx:1.5,
        class:o.pnl_rupees>=0?'dg':'db',
        'fill-opacity':Math.min(.92,Math.abs(o.pnl_rupees)/mx*.8+.14).toFixed(2)},s);}});
}
function costs(h,c){
  if(!c)return;
  var W=520,H=120,L=14,R=10,Tp=14,B=24,s=S(h,W,H);
  var st=[['gross',c.gross_points_before_costs,'bg'],
          ['charges',-Math.abs(c.charges_points),'bb'],
          ['slippage',-Math.abs(c.slippage_points),'bb'],
          ['net',c.net_points_after_costs,'ba']];
  var lo=0,hi=0,run=0;
  st.forEach(function(o){if(o[0]==='net')return;run+=o[1];lo=Math.min(lo,run);hi=Math.max(hi,run)});
  lo=Math.min(lo,c.net_points_after_costs,0);hi=Math.max(hi,c.gross_points_before_costs,0);
  var y=sc(lo*1.2-1,hi*1.2+1,H-B,Tp),bw=(W-R-L)/4;run=0;
  P('M'+L+' '+y(0)+'H'+(W-R),{class:'zero'},s);
  st.forEach(function(o,i){
    var cx=L+bw*i+bw/2,w=Math.min(bw*.5,46);
    var a=o[0]==='net'?0:run,b=o[0]==='net'?o[1]:run+o[1];
    var t=Math.max(a,b),bt=Math.min(a,b);
    E('rect',{x:cx-w/2,y:y(t),width:w,height:Math.max(1,Math.abs(y(t)-y(bt))),class:o[2],rx:2},s);
    T(Math.round(o[1]),cx,y(t)-4,'tk',s,'middle');
    T(o[0],cx,H-B+13,'tk',s,'middle');
    if(o[0]!=='net')run=b;});
}

var VD={strong_evidence:'ok',moderate_evidence:'ok',mixed_evidence:'mid',
        weak_evidence:'mid',insufficient_evidence:'bad',no_evidence:'bad'};

function draw(){
  var root=document.getElementById('root'); root.innerHTML='';
  if(!payload||!payload.summary){
    el('div','wait',root,'Waiting for the backtest result…'); reportSize(); return;
  }
  var s=payload.summary||{},h=payload.honesty||{},sp=payload.spec||{};
  var r=s.ratios||{},oos=h.out_of_sample||{},wf=h.walk_forward||[],cd=h.cost_drag||{};
  var v=el('div','v',root);

  var hd=el('div','hd',v), l=el('div',null,hd);
  el('div','ti',l,(sp.structure||'backtest').replace(/_/g,' ')+' · NIFTY weeklies');
  el('div','sp',l,((s.period||{}).from||'?')+' → '+((s.period||{}).to||'?')+
    '  ·  '+(s.n_trades||'?')+' trades  ·  1-min data, net of costs');
  var vd=el('div','vd '+(VD[h.verdict]||'mid'),hd);
  vd.textContent=(h.verdict||'').replace(/_/g,' ')+' · '+(h.health_score||'?')+'/100';

  var st=el('div','st',v);
  var pnl=s.total_pnl_rupees;
  stat(st,'Net P&L',M(pnl),pnl>=0?'g':'b');
  stat(st,'Win rate',pct(s.win_rate,0));
  stat(st,'Max DD',M(s.max_drawdown_rupees),'b');
  stat(st,'Sharpe',r.sharpe==null?'—':r.sharpe);
  stat(st,'Ret/margin',pct(s.mean_return_on_margin,2));
  stat(st,'Charges',M(s.total_charges_rupees),'b');

  equity(chart(v,'Equity and drawdown','c1'),(payload.equity_curve||{}).rows);
  if(wf.length)folds(chart(v,'Walk-forward folds','c2'),wf);

  var ev=el('div','ev',v);
  var held=el('div',null,ev);
  held.innerHTML='<b>Out of sample</b> '+(oos.held_up
    ?'<span class="yes">held up</span>':'<span class="no">did not hold up</span>')+
    ' &middot; chronological 70/30';
  var prof=wf.filter(function(f){return f.profitable}).length;
  el('div',null,ev).innerHTML='<b>Folds</b> '+prof+' of '+wf.length+' profitable';
  if((h.multiple_comparisons||{}).reading)
    el('div',null,ev).innerHTML='<b>Deflated Sharpe</b> '+h.multiple_comparisons.reading;
  if(cd.net_points_after_costs!=null)
    el('div',null,ev).innerHTML='<b>Costs</b> gross '+cd.gross_points_before_costs+
      ' pts → net '+cd.net_points_after_costs+' pts';

  var wide=el('div','wide',v);
  if((payload.breakdown||{}).by_month)months(chart(wide,'Month by month','c3'),payload.breakdown.by_month);
  if(cd&&cd.gross_points_before_costs!=null)costs(chart(wide,'Gross to net','c4'),cd);

  var ac=el('div','ac',v);
  if(ctx.displayMode!=='fullscreen'){
    var b=el('button','p',ac,'Expand');
    b.onclick=function(){ request('ui/request-display-mode',{mode:'fullscreen'})
      .then(function(res){ applyContext({displayMode:(res&&res.displayMode)||'fullscreen'});
        draw(); }).catch(function(){}); };
  }
  if(payload.report_url){
    var o=el('button',null,ac,'Open full report');
    o.onclick=function(){ request('ui/open-link',{url:payload.report_url}).catch(function(){}); };
  }
  var ask=el('button',null,ac,'Explain this result');
  ask.onclick=function(){ request('ui/message',{role:'user',content:{type:'text',
    text:'Explain this backtest result — what does the evidence actually support, and what '+
         'should I not conclude from it?'}}).catch(function(){}); };

  v.insertAdjacentHTML('beforeend',MARKHTML);
  reportSize();
}

// ---- start ------------------------------------------------------------------------
request('ui/initialize',{
  protocolVersion:'2026-01-26',
  clientInfo:{name:'stratify-backtest-view',version:'1.0.0'},
  appCapabilities:{availableDisplayModes:['inline','fullscreen']}
}).then(function(res){ applyContext((res||{}).hostContext||{}); draw(); })
  .catch(function(){ draw(); });

draw();
if(window.ResizeObserver)new ResizeObserver(reportSize).observe(document.body);
window.addEventListener('resize',reportSize);
})();
""".replace("CHARTPRIMS", CHART_JS).replace("MARKHTML", repr(MARK_HTML))

TEMPLATE = (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Stratify backtest</title><style>{CSS}</style></head>'
            f'<body><div id="root"></div><script>{JS}</script></body></html>')

# The descriptor the host reads from resources/list. No connectDomains, no
# resourceDomains: this view fetches nothing at all, so the default deny-everything CSP is
# exactly right and declaring domains would only widen it.
RESOURCE = {
    "uri": URI,
    "name": "backtest view",
    "title": "Backtest result",
    "description": ("Inline chart view for a backtest result: verdict, headline numbers, "
                    "equity and drawdown, walk-forward folds, and the evidence summary. "
                    "Expands to add the month grid and the cost breakdown."),
    "mimeType": MIME,
    "_meta": {"ui": {"prefersBorder": True}},
}


def contents():
    """The resources/read body for this template."""
    return {"uri": URI, "mimeType": MIME, "text": TEMPLATE,
            "_meta": {"ui": {"prefersBorder": True}}}
