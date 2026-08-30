"""The replay screen: watch the strategy trade, instead of reading what it did.

WHY IT EXISTS. A report is an argument made after the fact, and every number in it is a
summary of something that happened over time. The replay is the same year with the summary
removed: the index moves, the position's premium moves against it, the equity curve grows a
segment, and the statistics -- win rate, average win, Sharpe, drawdown -- are recomputed at
every close, so a reader sees them settle rather than arrive. What a backtest hides is
sequence, and this is the surface that gives it back.

THE LAYOUT. A fixed left rail carries the things that must never leave the screen -- what is
open, what the account is worth, how the strategy is doing as of this instant. The main
column carries what is being watched. A bottom dock, floating ABOVE the page rather than
pushing it, holds the trade log. Both the rail and the dock are draggable, because how much
room a panel deserves depends on what you are looking for.

THE INDEX CHART IS CONTINUOUS. Only the combined premium and the payoff are trade-specific.
Giving each trade its own axis threw away the thing a reader most wants while watching a
premium seller work -- where this week sits in the year -- and made every entry look like a
fresh start. One series, every expiry marked, and the position drawn as a span within it.

WHAT IS ON THE PAGE AND WHAT IS NOT. Index candles: released in full, they are not the
asset. Combined premium: a signed sum over two to four legs, not invertible to any of them.
Strikes and sides: rule outputs, not prices. Per-leg option prices, and anything that
resolves to one -- a per-leg P&L is a per-leg price by another name: NOT HERE, not once,
which is why this screen costs nothing against the price meter. See engine/replay.py for
the arithmetic behind that claim.
"""
import html
import json
import pathlib

from . import design

LWC = (pathlib.Path(__file__).resolve().parent / "vendor" /
       "lightweight-charts.js").read_text()

# Timeframes offered by the selector. The base series is 1-minute and every coarser view is
# aggregated in the browser, so switching is instant and re-fetches nothing.
TIMEFRAMES = (1, 5, 15, 30, 60, 240)

# MARKET MINUTES PER REAL SECOND, not an abstract multiplier. "3x" is meaningless until you
# know what it is three times; "30 min / sec" says a session passes in twelve and a half
# seconds, which a reader can picture before pressing play.
#
# A SELECT RATHER THAN A BUTTON ROW. As buttons it sat beside the candle-resolution row
# looking identical to it -- two strips of 5m/15m/30m/1h that mean entirely different
# things. The dropdown makes them different objects, and gives each option room for a unit.
SPEEDS = ((5, "5 min / sec"), (15, "15 min / sec"), (30, "30 min / sec"),
          (60, "1 hour / sec"), (240, "4 hours / sec"))
DEFAULT_SPEED = 15


def _e(v):
    return html.escape("" if v is None else str(v))




CSS = design.TOKENS + design.CHART_CSS + design.MARK_CSS + """
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{background:var(--bg);color:var(--body);
font:400 14.5px/1.6 FONTSTACK;-webkit-font-smoothing:antialiased}

/* Scrollbars. The platform default is a chrome-grey slab that belongs to no palette on
   the page; these are the page's own tokens, thin, and they fade until you are near. */
*{scrollbar-width:thin;scrollbar-color:var(--line) transparent}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--line);border-radius:6px;
border:3px solid transparent;background-clip:content-box}
::-webkit-scrollbar-thumb:hover{background:var(--faint);background-clip:content-box;
border:2px solid transparent}
::-webkit-scrollbar-corner{background:transparent}

/* ---- shell ---- */
.app{display:grid;grid-template-columns:var(--sw,352px) 7px minmax(0,1fr);
min-height:100vh;align-items:start}
.side{position:sticky;top:0;height:100vh;overflow-y:auto;overflow-x:hidden;
background:var(--surface);border-right:1px solid var(--line);
padding:15px 15px 26px}
.grip{position:sticky;top:0;height:100vh;cursor:col-resize;
display:flex;align-items:center;justify-content:center}
.grip::after{content:"";width:3px;height:46px;border-radius:2px;background:var(--line)}
.grip:hover::after,.grip.on::after{background:var(--accent);height:80px}
.main{padding:14px 18px 300px;min-width:0}
/* THE MAIN COLUMN IS EXACTLY ONE VIEWPORT TALL AND THE INDEX CHART TAKES WHAT IS LEFT.
   It used to be a fixed calc() -- 100vh minus a guess at everything else -- which left a
   band of dead space under the lower panels at most window sizes and clipped them at a
   few. Flex does the arithmetic instead: the deck and the two lower panels take their
   natural height and the index chart absorbs the remainder, whatever the window is.

   --dockrest is the space RESERVED for the dock: its collapsed bar, or its resting height.
   Drag it past that and it goes back to floating over the page, which is the behaviour the
   dock is for. Collapsing it hands every pixel to the chart. */
@media(min-width:901px){
  .main{height:100vh;display:flex;flex-direction:column;overflow:hidden;
        padding:14px 18px calc(var(--dockrest,204px) + 12px)}
  .main .deck{flex:none}
  .main .two{flex:none}
  .chartbox.grow{flex:1 1 auto;min-height:170px;display:flex;flex-direction:column}
  .chartbox.grow #spot{flex:1 1 auto;height:auto;min-height:0}
}
body.dragging{cursor:col-resize;user-select:none}
body.rowdrag{cursor:row-resize;user-select:none}
@media(max-width:900px){
  .app{grid-template-columns:1fr}
  .side{position:static;height:auto;border-right:0;border-bottom:1px solid var(--line)}
  .grip{display:none}
}

/* ---- rail ---- */
.brand{display:flex;align-items:center;justify-content:space-between;gap:10px;
margin-bottom:14px}
.kick{font:600 10px/1 MONOSTACK;letter-spacing:.15em;text-transform:uppercase;
color:var(--accent)}
.back{font:600 10px/1 MONOSTACK;letter-spacing:.08em;text-transform:uppercase;
color:var(--muted);text-decoration:none;border:1px solid var(--line);border-radius:6px;
padding:7px 9px;white-space:nowrap}
.back:hover{color:var(--accent);border-color:var(--accent)}
.sname{font-size:17px;font-weight:600;color:var(--ink);letter-spacing:-.015em;
line-height:1.25}
.sper{font:400 11px/1.55 MONOSTACK;color:var(--faint);margin:3px 0 14px}
.sper b{color:var(--muted);font-weight:600}

.sh{display:flex;align-items:baseline;justify-content:space-between;gap:8px;
margin:18px 0 8px}
.sh b{font:600 9.5px/1 MONOSTACK;letter-spacing:.13em;text-transform:uppercase;
color:var(--muted)}
.sh i{font:400 10px/1 MONOSTACK;color:var(--faint);font-style:normal}
.sh:first-of-type{margin-top:0}

/* open position */
.pos{border:1px solid var(--line);border-radius:11px;overflow:hidden;
background:var(--surface)}
.pos .ph{padding:8px 11px;background:var(--raised);border-bottom:1px solid var(--line);
display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.pos .ph .t{font:600 11.5px/1.25 FONTSTACK;color:var(--ink)}
.pos .ph .n{font:400 9.5px/1.25 MONOSTACK;color:var(--faint)}
.pos .pnl{display:flex;align-items:baseline;justify-content:space-between;gap:8px;
padding:7px 11px;border-bottom:1px solid var(--soft)}
.pos .pnl .k{font:600 8.5px/1 MONOSTACK;letter-spacing:.1em;text-transform:uppercase;
color:var(--faint)}
.pos .pnl .v{font-size:18px;font-weight:600;letter-spacing:-.025em;
font-variant-numeric:tabular-nums}
.pos .meta{padding:6px 11px;font:400 9.5px/1.5 MONOSTACK;color:var(--faint)}
.legs{display:grid;gap:1px;background:var(--soft)}
.leg{background:var(--surface);padding:6px 11px;display:grid;
grid-template-columns:auto 1fr auto;gap:8px;align-items:center;
font:400 11px/1.3 MONOSTACK}
.side-tag{font-weight:600;font-size:8px;letter-spacing:.09em;padding:3px 4px;
border-radius:3px}
.side-tag.sell{background:var(--crit-soft);color:var(--crit)}
.side-tag.buy{background:var(--good-soft);color:var(--good)}
.leg .k{color:var(--ink);font-weight:600}
.leg .d{color:var(--muted);text-align:right;white-space:nowrap;
font-variant-numeric:tabular-nums}
.itmchip{font-style:normal;font-size:8px;font-weight:600;letter-spacing:.1em;
padding:2px 4px;border-radius:3px;background:var(--warn);color:var(--surface);
vertical-align:1px;margin-left:4px}
.leg.itm{background:var(--warn-soft)}
.leg.itm .k,.leg.itm .d{color:var(--warn)}
.idle{border:1px dashed var(--line);border-radius:11px;padding:14px;text-align:center;
color:var(--faint);font:400 10.5px/1.6 MONOSTACK}

/* account */
.hero{border:1px solid var(--line);border-radius:12px;padding:13px 14px;
background:linear-gradient(180deg,var(--raised),var(--surface))}
.hero .k{font:600 9px/1 MONOSTACK;letter-spacing:.11em;text-transform:uppercase;
color:var(--faint)}
.hero .v{font-size:29px;line-height:1.1;margin-top:5px;color:var(--ink);font-weight:600;
letter-spacing:-.03em;font-variant-numeric:tabular-nums}
.hero .d{display:flex;gap:9px;align-items:baseline;margin-top:6px;
font:500 12px/1.4 MONOSTACK}
.pill{font:600 9.5px/1 MONOSTACK;letter-spacing:.06em;padding:4px 6px;border-radius:4px}
.p-up{background:var(--good-soft);color:var(--good)}
.p-dn{background:var(--crit-soft);color:var(--crit)}
.hero .sub{font:400 10.5px/1.5 MONOSTACK;color:var(--faint);margin-top:6px}
/* The sizing controls have two states. Before the replay starts they are fields, because
   that is the one moment the answer matters. Once it is running they collapse to a line of
   text plus a reset -- the numbers are still on screen, they have just stopped being three
   input boxes competing with the equity figure above them. */
.cfg{margin-top:10px;padding-top:9px;border-top:1px solid var(--line)}
.cfgset{display:flex;gap:8px;flex-wrap:wrap}
.cfgset label{display:flex;align-items:center;gap:5px;font:400 10px/1 MONOSTACK;
color:var(--faint);letter-spacing:.05em;text-transform:uppercase}
.cfgset input{width:78px;font:600 12px/1 MONOSTACK;color:var(--ink);
background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:6px 7px}
.cfgset input:focus,.cfgset select:focus{outline:none;border-color:var(--accent)}
.cfgset select{font:600 11.5px/1 MONOSTACK;color:var(--ink);background:var(--surface);
border:1px solid var(--line);border-radius:6px;padding:6px 5px}
.cfgset span{text-transform:none;letter-spacing:0}
.cfgline{display:flex;align-items:center;justify-content:space-between;gap:8px;
font:400 10.5px/1.4 MONOSTACK;color:var(--faint)}
.cfgline b{color:var(--muted);font-weight:600}
.lnk{font:600 9px/1 MONOSTACK;letter-spacing:.1em;text-transform:uppercase;
color:var(--muted);background:none;border:1px solid var(--line);border-radius:5px;
padding:5px 7px;cursor:pointer}
.lnk:hover{color:var(--accent);border-color:var(--accent)}
.hid{display:none}
.eqwrap{border:1px solid var(--line);border-radius:11px;padding:8px 8px 4px;
background:var(--raised)}
#eq{height:126px}

/* stats */
.stats{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--line);
border:1px solid var(--line);border-radius:11px;overflow:hidden}
.st{background:var(--surface);padding:9px 11px;min-width:0}
.st .k{font:600 8.5px/1.3 MONOSTACK;letter-spacing:.09em;text-transform:uppercase;
color:var(--faint);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.st .v{font-size:17px;line-height:1.25;margin-top:3px;color:var(--ink);font-weight:600;
letter-spacing:-.022em;font-variant-numeric:tabular-nums}
.st .v.g{color:var(--good)}.st .v.b{color:var(--crit)}.st .v.dim{color:var(--faint);
font-size:15px}
.st .s{font:400 9.5px/1.4 MONOSTACK;color:var(--faint);margin-top:2px;
white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.wr{height:3px;border-radius:2px;background:var(--crit-soft);margin-top:5px;overflow:hidden}
.wr i{display:block;height:100%;background:var(--good)}

/* ---- deck ---- */
.deck{position:sticky;top:0;z-index:40;background:var(--surface);
border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow);
padding:9px 11px;margin-bottom:10px;display:flex;align-items:center;gap:10px;
flex-wrap:wrap}
.tbtn{font:600 11.5px/1 MONOSTACK;border:1px solid var(--line);background:var(--raised);
color:var(--body);border-radius:7px;padding:9px 10px;cursor:pointer;min-width:36px}
.tbtn:hover{border-color:var(--accent);color:var(--accent)}
.tbtn.play{background:var(--accent);border-color:var(--accent);color:var(--surface);
min-width:64px}
.tbtn.on{border-color:var(--accent);color:var(--accent);background:var(--accent-soft)}
.grp{display:flex;gap:3px}
.grp .tbtn{padding:8px 7px;min-width:0;font-size:10.5px}
.clock{font:600 12.5px/1 MONOSTACK;color:var(--ink);white-space:nowrap;
font-variant-numeric:tabular-nums}
.clock small{display:block;font-weight:400;font-size:9.5px;color:var(--faint);
letter-spacing:.07em;text-transform:uppercase;margin-top:4px}
.scrub{flex:1 1 140px;min-width:110px;accent-color:var(--accent)}
.deck .sep{width:1px;align-self:stretch;background:var(--line);margin:0 1px}
.tag{font:600 9px/1 MONOSTACK;letter-spacing:.1em;text-transform:uppercase;
color:var(--faint)}
.dsel{font:600 11.5px/1 MONOSTACK;color:var(--ink);background:var(--raised);
border:1px solid var(--line);border-radius:7px;padding:9px 7px;cursor:pointer}
.dsel:hover{border-color:var(--accent)}
.dsel:focus{outline:none;border-color:var(--accent)}

/* ---- charts ---- */
.chartbox{background:var(--surface);border:1px solid var(--line);border-radius:12px;
padding:10px;box-shadow:var(--shadow);margin-bottom:10px;position:relative}
.chead{display:flex;align-items:center;justify-content:space-between;gap:10px;
flex-wrap:wrap;margin-bottom:7px}
.ctitle{font:600 12.5px/1.3 FONTSTACK;color:var(--ink);white-space:nowrap}
.chint{font:400 10.5px/1.3 MONOSTACK;color:var(--faint)}
/* The legend lives in the header, not under the chart: it is reference material, and a
   strip of it below every panel was costing three rows of the vertical space the charts
   themselves needed. */
.lg{display:flex;gap:11px;flex-wrap:wrap;align-items:center;
font:400 10px/1.4 MONOSTACK;color:var(--muted);margin-left:auto}
.lg i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px;
vertical-align:-1px}
.lg i.dash{width:14px;height:0;border-top:2px dashed currentColor;border-radius:0;
vertical-align:3px}
.lg i.vline{width:0;height:11px;border-left:2px dotted currentColor;border-radius:0;
vertical-align:-2px;margin-right:6px}
/* The vertical budget: viewport minus the deck, the two lower panels, and the dock at its
   default height. The dock floats over the page, so if it is expanded it will cover the
   lower panels -- that is the trade, and it is why the main column keeps enough bottom
   padding to scroll them back out from under it. */
#spot{height:calc(100vh - 588px);min-height:240px}   /* fallback below 901px */
/* The lower pair gives ground on a short window so the index chart is not squeezed to a
   strip: at 800px tall a fixed 208 left it 172 pixels. */
#prem{height:clamp(146px,17vh,208px)}
.two{display:grid;grid-template-columns:1fr 1fr;gap:10px}
@media(max-width:1180px){.two{grid-template-columns:1fr}}
#payoffhost{height:clamp(146px,17vh,208px);cursor:crosshair}
#payoffhost svg{display:block;width:100%;height:100%}

/* Readouts follow the cursor. Pinned in a corner they described a point the eye was not
   looking at, which is most of the reason a tooltip exists. */
.read{position:fixed;z-index:60;pointer-events:none;background:var(--surface);
border:1px solid var(--line);border-radius:9px;padding:8px 10px;box-shadow:var(--shadow);
font:400 11px/1.6 MONOSTACK;color:var(--body);min-width:158px}
.read .rk{font:600 8.5px/1 MONOSTACK;letter-spacing:.1em;text-transform:uppercase;
color:var(--faint);margin-bottom:5px}
.read .rr{display:flex;justify-content:space-between;gap:14px}
.read .rr b{color:var(--ink);font-weight:600}
.read .big{font-size:17px;font-weight:600;letter-spacing:-.02em;margin:1px 0 4px}
.read .sep{border-top:1px solid var(--soft);margin:5px 0 4px}
.read.hid{display:none}

/* ---- bottom dock: floats ABOVE the page, never reflows it ---- */
.dock{position:fixed;left:0;right:0;bottom:0;z-index:70;background:var(--surface);
border-top:1px solid var(--line);box-shadow:0 -8px 28px -18px rgba(0,0,0,.45);
display:flex;flex-direction:column;height:var(--dockh,204px);max-height:88vh}
.dock.min{height:41px!important}
@media(min-width:901px){.dock{left:calc(var(--sw,352px) + 7px)}}
@media(prefers-reduced-motion:reduce){.dock{transition:none}}
.dockgrip{height:6px;margin-top:-6px;cursor:row-resize;flex:none}
.dockgrip:hover{background:var(--accent);opacity:.35}
.dockbar{display:flex;align-items:center;gap:4px;padding:0 10px;height:40px;flex:none;
border-bottom:1px solid var(--line);background:var(--raised)}
.dtab{font:600 10.5px/1 MONOSTACK;letter-spacing:.05em;color:var(--muted);
background:none;border:0;border-radius:7px;padding:9px 11px;cursor:pointer;
white-space:nowrap}
.dtab:hover{color:var(--ink);background:var(--sunk)}
.dtab.on{color:var(--accent);background:var(--accent-soft)}
.dtab .cnt{color:var(--faint);font-weight:400;margin-left:5px}
.dockspace{flex:1}
.dbtn{font:600 12px/1 MONOSTACK;color:var(--muted);background:none;border:0;
border-radius:6px;padding:8px 9px;cursor:pointer}
.dbtn:hover{color:var(--accent);background:var(--sunk)}
.dockbody{flex:1;overflow:auto;min-height:0}
.dock.min .dockbody,.dock.min .dockgrip{display:none}
.dockpane{display:none;padding:0}
.dockpane.on{display:block}

table{width:100%;border-collapse:collapse;font-size:12px}
th,td{text-align:left;padding:7px 12px;border-bottom:1px solid var(--soft);
font-variant-numeric:tabular-nums;white-space:nowrap}
thead th{position:sticky;top:0;background:var(--surface);z-index:2;
font:600 9px/1.3 MONOSTACK;letter-spacing:.07em;text-transform:uppercase;
color:var(--muted);box-shadow:0 1px 0 var(--line)}
tbody tr:last-child td{border-bottom:0}
tbody tr.new{animation:flash .9s ease-out}
tbody tr:hover{background:var(--sunk)}
@keyframes flash{from{background:var(--accent-soft)}to{background:transparent}}
td.n,th.n{text-align:right}
.g{color:var(--good)}.b{color:var(--crit)}
.empty td{color:var(--faint);text-align:center;padding:26px}
.pad{padding:14px 16px}
.pad p{font-size:12px;line-height:1.65;color:var(--faint);max-width:78ch;margin:0 0 10px}
.pad p:last-child{margin-bottom:0}
@media(prefers-reduced-motion:reduce){*{animation:none!important}}
"""


JS = r"""
(function(){
var D = window.__REPLAY__;
var css = getComputedStyle(document.documentElement);
function tok(n){ return css.getPropertyValue(n).trim() }
var UP=tok('--good'), DN=tok('--crit'), ACC=tok('--accent'), INK=tok('--ink'),
    WARN=tok('--warn'), FAINT=tok('--faint'), LINE=tok('--line'), SURF=tok('--surface');
var $ = function(id){ return document.getElementById(id) };
var WARN_SOFT = tok('--warn-soft');
/* Canvas needs a real font string; the token is a stack, so it is reused verbatim. */
var MONO_STACK = MONOFONT;

function money(v){ if(v==null||isNaN(v))return '—';
  var a=Math.abs(v), s = a>=1e7 ? '₹'+(a/1e7).toFixed(2)+'Cr'
    : a>=1e5 ? '₹'+(a/1e5).toFixed(2)+'L'
    : a>=1000 ? '₹'+(a/1000).toFixed(1)+'k' : '₹'+Math.round(a);
  return (v<0?'−':'')+s }
function sgn(v,dp){ if(v==null||isNaN(v))return '—';
  return (v>=0?'+':'−')+Math.abs(v).toFixed(dp==null?2:dp) }
function cls(v){ return v==null?'':(v>=0?'g':'b') }
function pad2(n){ return (n<10?'0':'')+n }

/* TIME IS IST AND MUST LOOK LIKE IT ON EVERY MACHINE. Lightweight Charts renders epoch
   seconds in the VIEWER's timezone, so shifting the number by +5:30 only happens to look
   right on a box already set to IST -- an earlier build printed 18:15 for a 12:45 bar. The
   wall-clock reading is handed over as though it were UTC and every label formatted with
   the UTC getters, so what was recorded is what is displayed, everywhere. */
var MON=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
function fmtHM(t){ var d=new Date(t*1000); return pad2(d.getUTCHours())+':'+pad2(d.getUTCMinutes()) }
function fmtDay(t){ var d=new Date(t*1000); return d.getUTCDate()+' '+MON[d.getUTCMonth()] }
function fmtMon(t){ var d=new Date(t*1000); return MON[d.getUTCMonth()]+' '+d.getUTCFullYear() }
function fmtFull(t){ return fmtDay(t)+' '+fmtHM(t) }
function fmtStamp(t){ var d=new Date(t*1000);
  return d.getUTCDate()+' '+MON[d.getUTCMonth()]+' '+d.getUTCFullYear()+'  '+fmtHM(t) }

/* ------------------------------------------------------------------ decoding
   engine/replay.py ships the index columnar, integer and delta-coded: prices as whole
   twentieths (NIFTY ticks in 0.05, so nothing is lost), open/high/low as offsets from
   their own bar's close, the timeline as minute gaps. 92,000 bars of objects would be
   2.3 MB of mostly repeated key names. Typed arrays here, objects only for what is drawn. */
var SP = (function(B){
  var n = B.n|0;
  var out = { n:n, time:new Float64Array(n), o:new Float64Array(n), h:new Float64Array(n),
              l:new Float64Array(n), c:new Float64Array(n) };
  var t = B.t0, c = 0, Q = B.q;
  for(var i=0;i<n;i++){
    if(i) t += B.dt[i]*60;
    c += B.c[i];
    out.time[i]=t; out.c[i]=c/Q;
    out.o[i]=(c+B.o[i])/Q; out.h[i]=(c+B.h[i])/Q; out.l[i]=(c+B.l[i])/Q; }
  return out; })(D.spot);
/* The index series is OHLC and nothing else. Optional columns are absent rather than
   null-filled, so nothing here may assume they exist. */

var TR = D.trades, Q = D.spot.q || 20;
TR.forEach(function(t){
  t.pAt = function(g){ var v = t.p[g - t.i0]; return v==null?null:v/Q }; });

/* Coarser timeframes are aggregated here rather than fetched. Grouping on
   floor(time / tf) aligns buckets to the IST clock and lands session boundaries for free,
   because the timestamps already carry the wall-clock reading. */
var AGG = null;                 /* {time, o, h, l, c, src} for the whole series at ST.tf */
function buildAgg(tf){
  if(tf <= 1){
    AGG = { n:SP.n, time:SP.time, o:SP.o, h:SP.h, l:SP.l, c:SP.c, src:null, of:null };
    return; }
  var step = tf*60, T=[],O=[],H=[],L=[],C=[],SRC=[];
  var of = new Int32Array(SP.n), key=null, j=-1;
  for(var i=0;i<SP.n;i++){
    var k = Math.floor(SP.time[i]/step);
    if(k!==key){ key=k; j++; T.push(k*step); O.push(SP.o[i]); H.push(SP.h[i]);
                 L.push(SP.l[i]); C.push(SP.c[i]); SRC.push(i); }
    else { if(SP.h[i]>H[j]) H[j]=SP.h[i]; if(SP.l[i]<L[j]) L[j]=SP.l[i]; C[j]=SP.c[i]; }
    of[i]=j; }
  AGG = { n:T.length, time:T, o:O, h:H, l:L, c:C, src:SRC, of:of }; }
function aggIndex(g){ return AGG.of ? AGG.of[g] : g }

var ST = { gi:0, ti:0, open:false, playing:false, locked:false, mps:15, stride:1, tf:5,
           capital:D.capital, equity:D.capital, peak:D.capital, base:D.capital,
           baseKey:null, compound:'trade', closed:[], maxdd:0, timer:null };

/* ------------------------------------------------------------------ position sizing
   The same rule as server/sizing.py, run in the browser so the capital controls are
   instant: lots = floor(base capital * deploy / margin for one lot), and a trade needing
   more margin than the whole allowance is SKIPPED rather than taken undersized.

   WHAT "BASE" MEANS IS THE COMPOUNDING CHOICE, and it is not a detail. Sizing off running
   equity after every trade is the most aggressive reading available -- it presses hardest
   exactly when a strategy has been lucky, and after a drawdown it shrinks every position
   at the worst possible moment. Nobody actually trades that way: money is added or taken
   out on a cadence. So the base is refreshed on a period, and "every trade" is only the
   default because it is what the report's own figures use. */
function periodKey(dateStr){
  var y = +dateStr.slice(0,4), m = +dateStr.slice(5,7), d = +dateStr.slice(8,10);
  switch(ST.compound){
    case 'never':   return 'fixed';
    case 'quarter': return y+'Q'+Math.ceil(m/3);
    case 'month':   return y+'M'+m;
    case 'week':    /* ISO-ish: the Monday the date belongs to */
      var t = Date.UTC(y,m-1,d), dow = (new Date(t).getUTCDay()+6)%7;
      return 'W'+Math.floor((t - dow*86400000)/86400000);
    default:        return null;          /* every trade: never matches, always refreshes */
  } }

function sizingBase(tr){
  if(ST.compound === 'never') return ST.capital;
  var k = periodKey(tr.entry);
  if(k === null || k !== ST.baseKey){ ST.baseKey = k; ST.base = ST.equity; }
  return ST.base; }

function lotsFor(base, tr){
  var perLot = tr.margin_points * tr.lot_size;
  if(!(perLot > 0)) return 0;
  return Math.floor(base * D.deploy / perLot); }

/* What a single lot ties up. Points times lot size, which is the number that decides how
   many lots the account can carry -- and the one figure a reader cannot derive from
   anything else on the page. */
function marginPerLot(tr){ return tr.margin_points * tr.lot_size }

/* ------------------------------------------------------------------ Black-Scholes
   r = q = 0. For 0-7 DTE index weeklies the carry term moves a theoretical price by far
   less than one tick, and dropping it keeps the payoff panel self-consistent: the vol
   solved out of the position's own combined premium is by construction the vol that
   reproduces that premium, so the "now" curve passes through the real P&L instead of
   missing it by a carry-shaped sliver. Deliberately not a general-purpose pricer. */
function ncdf(x){
  var s = x<0?-1:1, a = Math.abs(x)/Math.SQRT2, t = 1/(1+0.3275911*a);
  var y = 1 - (((((1.061405429*t - 1.453152027)*t) + 1.421413741)*t - 0.284496736)*t
           + 0.254829592)*t*Math.exp(-a*a);
  return 0.5*(1 + s*y); }
function bs(type, spot, K, T, iv){
  var intr = type==='CE' ? Math.max(spot-K,0) : Math.max(K-spot,0);
  if(!(spot>0)||!(T>0)||!(iv>0)||!(K>0)) return intr;
  var sq = iv*Math.sqrt(T), d1 = (Math.log(spot/K)+0.5*iv*iv*T)/sq, d2 = d1-sq;
  return type==='CE' ? spot*ncdf(d1)-K*ncdf(d2) : K*ncdf(-d2)-spot*ncdf(-d1); }
function posValue(legs, spot, T, iv){
  var v=0; for(var i=0;i<legs.length;i++){ var l=legs[i];
    v += (l.action==='SELL'?1:-1)*bs(l.type, spot, l.strike, T, iv) } return v; }

/* ONE VOL FOR THE WHOLE POSITION, SOLVED FROM ONE POSITION-LEVEL NUMBER.
   The usual way to draw a live payoff curve solves an implied vol per leg from each leg's
   mark -- which needs per-leg prices, which is exactly what may not leave here. So the
   unknown is a single sigma satisfying
       sum over legs of sign * BS(K, T, sigma) = the combined premium now
   one equation, one unknown, both sides already published. Bisection rather than Newton:
   position value is monotone in sigma once the net vega has a sign, and bisection cannot
   diverge. Null when the premium sits outside what any vol in range produces, in which
   case the panel says so rather than drawing a curve it cannot justify. */
function solveIV(legs, spot, T, target){
  if(!(T>0) || target==null) return null;
  var lo=0.005, hi=4.0, flo=posValue(legs,spot,T,lo)-target, fhi=posValue(legs,spot,T,hi)-target;
  if(flo===0) return lo;
  if(fhi===0) return hi;
  if((flo>0)===(fhi>0)) return null;
  for(var i=0;i<60;i++){ var m=0.5*(lo+hi), f=posValue(legs,spot,T,m)-target;
    if((f>0)===(flo>0)){ lo=m; flo=f } else hi=m }
  return 0.5*(lo+hi); }
function yearsTo(expiry, nowSec){
  var e = Date.UTC(+expiry.slice(0,4),+expiry.slice(5,7)-1,+expiry.slice(8,10),15,30)/1000;
  return Math.max(e-nowSec,0)/(365*24*3600); }

/* ------------------------------------------------------------------ charts */
var base = {
  layout:{ background:{color:'transparent'}, textColor:FAINT,
           attributionLogo:true,          /* the Apache-2.0 licence requires the credit */
           fontFamily:getComputedStyle(document.body).fontFamily },
  grid:{ vertLines:{color:LINE}, horzLines:{color:LINE} },
  rightPriceScale:{ borderColor:LINE, scaleMargins:{top:0.1, bottom:0.1} },
  timeScale:{ borderColor:LINE, timeVisible:true, secondsVisible:false, minBarSpacing:0.02,
              tickMarkFormatter:function(t,type){
                return type>=3 ? fmtHM(t) : type===2 ? fmtDay(t) : fmtMon(t) } },
  crosshair:{ mode:1, vertLine:{color:FAINT,width:1,style:2,labelBackgroundColor:ACC},
              horzLine:{color:FAINT,width:1,style:2,labelBackgroundColor:ACC} },
  localization:{ timeFormatter: fmtFull },
  handleScroll:true, handleScale:true
};
var LW = LightweightCharts;

var spotChart = LW.createChart($('spot'), base);
var candles = spotChart.addSeries(LW.CandlestickSeries, {
  upColor:UP, downColor:DN, borderUpColor:UP, borderDownColor:DN,
  wickUpColor:UP, wickDownColor:DN,
  /* WHILE A POSITION IS OPEN, ITS STRIKES ARE PART OF THE PRICE RANGE.
     On the old per-trade chart the range was pinned to the strikes outright. A continuous
     chart cannot do that -- it has to autoscale to whatever window you are looking at --
     but autoscaling alone put three of four leg lines off-screen, and the leg lines are
     most of the reason the index chart is here. So the visible range is whatever the
     candles need, widened just enough to contain the open position. */
  autoscaleInfoProvider:function(orig){
    var r = orig(); if(!r || !r.priceRange) return r;
    var tr = openTrade(); if(!tr) return r;
    var lo = r.priceRange.minValue, hi = r.priceRange.maxValue;
    tr.legs.forEach(function(l){ lo = Math.min(lo,l.strike); hi = Math.max(hi,l.strike) });
    var pad = (hi-lo)*0.04;
    r.priceRange.minValue = lo-pad; r.priceRange.maxValue = hi+pad;
    return r; } });

var premChart = LW.createChart($('prem'), base);
var premLine = premChart.addSeries(LW.AreaSeries, {
  lineColor:ACC, topColor:ACC+'2e', bottomColor:ACC+'00', lineWidth:2,
  priceLineVisible:false, lastValueVisible:true });

var eqChart = LW.createChart($('eq'), Object.assign({}, base, {
  rightPriceScale:{ borderColor:LINE, scaleMargins:{top:0.14, bottom:0.14} },
  timeScale:{ borderColor:LINE, timeVisible:false, minBarSpacing:0.02,
              tickMarkFormatter:function(t){ return fmtDay(t) } },
  handleScroll:false, handleScale:false }));
var eqLine = eqChart.addSeries(LW.AreaSeries, {
  lineColor:ACC, topColor:ACC+'26', bottomColor:ACC+'00', lineWidth:2,
  priceLineVisible:false, lastValueVisible:true,
  priceFormat:{ type:'custom', formatter:money, minMove:1 },
  /* Always keep the opening balance in frame. Left to autoscale the axis tracks the curve
     so tightly that the start line -- the only reference on the panel -- sits off-screen
     for months. */
  autoscaleInfoProvider:function(orig){
    var r = orig(); if(!r||!r.priceRange) return r;
    r.priceRange.minValue = Math.min(r.priceRange.minValue, ST.capital);
    r.priceRange.maxValue = Math.max(r.priceRange.maxValue, ST.capital);
    return r; } });
var startLine = eqLine.createPriceLine({ price:D.capital, color:FAINT, lineWidth:1,
  lineStyle:2, axisLabelVisible:false });

/* ---- expiry rules -------------------------------------------------------------------
   Lightweight Charts has horizontal price lines but no vertical time lines, so this is a
   small pane primitive. It earns its keep on a continuous chart: for a weekly premium
   seller the expiry bell IS the event -- everything before it is decay, everything at it
   is settlement -- and without the rules a year of candles gives no sense of the rhythm
   the strategy is trading. */
function VertRules(){ this._views = [new VertView(this)]; this.marks = []; this.chart = null;
  this.series = null; }
VertRules.prototype.attached = function(p){ this.chart = p.chart; this.series = p.series;
  this._req = p.requestUpdate };
VertRules.prototype.detached = function(){ this.chart = null };
VertRules.prototype.paneViews = function(){ return this._views };
/* Lightweight Charts calls updateAllViews() on the PRIMITIVE before each paint; it does
   not call update() on the views itself. Without this hook the coordinates were never
   computed and the rules silently drew nothing -- the primitive was attached, the times
   were set, and the canvas stayed empty. */
VertRules.prototype.updateAllViews = function(){
  for(var i=0;i<this._views.length;i++) this._views[i].update() };
/* LOGICAL INDICES, NOT TIMES.
   timeToCoordinate only resolves for a bar the series actually holds, so an expiry ahead
   of the cursor had no coordinate and the rule appeared the instant the replay reached it
   -- which is the moment it stops being useful. A logical index extrapolates into the
   empty space to the right of the data, so the line for next Thursday is drawn as soon as
   next Thursday is in frame, which is when a premium seller wants to see it. */
VertRules.prototype.setMarks = function(marks){ this.marks = marks;
  if(this._req) this._req() };

function VertView(src){ this._src = src; this._x = []; }
VertView.prototype.zOrder = function(){ return 'bottom' };
VertView.prototype.update = function(){
  var ts = this._src.chart ? this._src.chart.timeScale() : null;
  this._x = []; this._t = [];
  if(!ts) return;
  var vis = ts.getVisibleLogicalRange();
  var marks = this._src.marks;
  for(var i=0;i<marks.length;i++){
    if(vis && (marks[i].j < vis.from - 2 || marks[i].j > vis.to + 2)) continue;
    var x = ts.logicalToCoordinate(marks[i].j);
    if(x === null) continue;
    this._x.push(x); this._t.push(marks[i].label); } };
VertView.prototype.renderer = function(){
  var xs = this._x, labels = this._t;
  return { draw:function(target){
    target.useBitmapCoordinateSpace(function(scope){
      var ctx = scope.context, r = scope.verticalPixelRatio, hr = scope.horizontalPixelRatio;
      var H = scope.bitmapSize.height;
      ctx.save();
      ctx.strokeStyle = WARN; ctx.globalAlpha = 0.5; ctx.lineWidth = Math.max(1, hr);
      ctx.setLineDash([Math.max(2,2*r), Math.max(3,3*r)]);
      for(var i=0;i<xs.length;i++){
        var x = Math.round(xs[i]*hr) + 0.5;
        ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H - 18*r); ctx.stroke(); }
      /* A rule with no label is a mystery line. Each is dated at the foot -- the one strip
         of the chart nothing else occupies. Labels are skipped, not shrunk, where they
         would collide: zoomed out to four-hour candles the weekly expiries are a few
         pixels apart, and overlapping text is worse than none. The rules themselves are
         always drawn, so the rhythm survives even when the dates cannot. */
      ctx.setLineDash([]); ctx.globalAlpha = 1;
      ctx.font = '600 '+Math.round(9*r)+'px '+MONO_STACK;
      ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
      var lastRight = -Infinity;
      for(var i=0;i<xs.length;i++){
        var x = xs[i]*hr, txt = labels[i] || 'EXPIRY';
        var w = ctx.measureText(txt).width + 9*hr;
        if(x - w/2 < lastRight + 5*hr) continue;
        lastRight = x + w/2;
        ctx.fillStyle = WARN_SOFT;
        roundRect(ctx, x - w/2, H - 15*r, w, 13*r, 3*r); ctx.fill();
        ctx.fillStyle = WARN;
        ctx.fillText(txt, x, H - 4.5*r); }
      ctx.restore(); }); } }; };

function roundRect(ctx, x, y, w, h, r){
  ctx.beginPath();
  ctx.moveTo(x+r, y); ctx.arcTo(x+w, y, x+w, y+h, r); ctx.arcTo(x+w, y+h, x, y+h, r);
  ctx.arcTo(x, y+h, x, y, r); ctx.arcTo(x, y, x+w, y, r); ctx.closePath(); }
var rules = new VertRules();
candles.attachPrimitive(rules);

/* ---- entry and exit marks -----------------------------------------------------------
   Lightweight Charts' built-in markers are an arrow and a line of text, which on a
   continuous year of candles reads as litter: no sense of how long a position was held,
   no way to tell an entry from an exit at a glance, and the text collides with the next
   trade's. These are drawn instead -- the position's life shaded across the chart, a
   filled tag at the entry carrying the trade number, and a coloured pill at the exit
   carrying the return on margin, which is the number that says whether it was worth the
   capital it tied up. */
function TradeMarks(){ this._views=[new TradeView(this)]; this.items=[]; this.chart=null;
  this.series=null; }
TradeMarks.prototype.attached = function(p){ this.chart=p.chart; this.series=p.series;
  this._req=p.requestUpdate };
TradeMarks.prototype.detached = function(){ this.chart=null };
TradeMarks.prototype.paneViews = function(){ return this._views };
TradeMarks.prototype.updateAllViews = function(){
  for(var i=0;i<this._views.length;i++) this._views[i].update() };
TradeMarks.prototype.setItems = function(it){ this.items=it; if(this._req) this._req() };

function TradeView(src){ this._src=src; this._m=[] }
TradeView.prototype.zOrder = function(){ return 'top' };
TradeView.prototype.update = function(){
  var s=this._src, ts=s.chart?s.chart.timeScale():null, se=s.series;
  this._m=[];
  if(!ts||!se) return;
  for(var i=0;i<s.items.length;i++){
    var it=s.items[i];
    var x0=ts.timeToCoordinate(it.t0), y0=se.priceToCoordinate(it.p0);
    var x1=it.t1==null?null:ts.timeToCoordinate(it.t1);
    var y1=it.p1==null?null:se.priceToCoordinate(it.p1);
    if(x0===null) continue;
    this._m.push({x0:x0,y0:y0,x1:x1,y1:y1,n:it.n,pct:it.pct,win:it.win,open:it.open}); } };

TradeView.prototype.renderer = function(){
  var m=this._m;
  return { draw:function(target){
    target.useBitmapCoordinateSpace(function(scope){
      var ctx=scope.context, hr=scope.horizontalPixelRatio, vr=scope.verticalPixelRatio;
      var H=scope.bitmapSize.height;
      ctx.save();
      for(var i=0;i<m.length;i++){
        var d=m[i], x0=d.x0*hr, x1=(d.x1==null?null:d.x1*hr);
        /* The held span of a CLOSED trade, so a position reads as a duration rather than
           as two dots. The live one gets no band: a wash of colour over the candles you
           are actually watching obscures the only part of the chart that is still moving,
           and the open position is already named in the rail. */
        if(!d.open && x1!==null && x1>x0){
          ctx.fillStyle = d.win?UP:DN;
          ctx.globalAlpha = 0.055;
          ctx.fillRect(x0, 0, x1-x0, H);
          ctx.globalAlpha = 1; }
        /* entry: a solid tag under the candle it was opened on */
        if(d.y0!==null){
          var ey=d.y0*vr;
          ctx.fillStyle = ACC;
          ctx.beginPath();
          ctx.moveTo(x0, ey+5*vr);
          ctx.lineTo(x0-4.5*hr, ey+11*vr);
          ctx.lineTo(x0+4.5*hr, ey+11*vr);
          ctx.closePath(); ctx.fill();
          ctx.font='600 '+Math.round(9.5*vr)+'px '+MONO_STACK;
          ctx.textAlign='center'; ctx.textBaseline='top';
          var t='#'+d.n, w=ctx.measureText(t).width+9*hr;
          roundRect(ctx, x0-w/2, ey+11*vr, w, 14*vr, 3*hr);
          ctx.fill();
          ctx.fillStyle = SURF; ctx.fillText(t, x0, ey+13*vr); }
        /* exit: a coloured pill above the candle, carrying the return on margin */
        if(x1!==null && d.y1!==null){
          var xy=d.y1*vr;
          ctx.fillStyle = d.win?UP:DN;
          ctx.beginPath();
          ctx.moveTo(x1, xy-5*vr);
          ctx.lineTo(x1-4.5*hr, xy-11*vr);
          ctx.lineTo(x1+4.5*hr, xy-11*vr);
          ctx.closePath(); ctx.fill();
          ctx.font='600 '+Math.round(9.5*vr)+'px '+MONO_STACK;
          ctx.textAlign='center'; ctx.textBaseline='bottom';
          var t2=d.pct, w2=ctx.measureText(t2).width+9*hr;
          roundRect(ctx, x1-w2/2, xy-25*vr, w2, 14*vr, 3*hr);
          ctx.fill();
          ctx.fillStyle = SURF; ctx.fillText(t2, x1, xy-13.5*vr); } }
      ctx.restore(); }); } }; };
var tradeMarks = new TradeMarks();
candles.attachPrimitive(tradeMarks);

spotChart.timeScale().subscribeVisibleLogicalRangeChange(function(){
  /* The rules are filtered to the visible range, so panning or zooming has to recompute
     them -- otherwise scrolling forward reveals blank space where next week's expiry is. */
  if(rules._req) rules._req(); });

function resizeCharts(){
  [[spotChart,'spot'],[premChart,'prem'],[eqChart,'eq']].forEach(function(pr){
    var el = $(pr[1]);
    pr[0].applyOptions({ width:el.clientWidth, height:el.clientHeight }); });
  premFrame(); eqFrame(); }
window.addEventListener('resize', resizeCharts);

/* ---- strike lines. Side and strike, never a per-leg P&L: a per-leg P&L IS a per-leg
   price, and there is no version of it that stays inside the boundary. ---- */
var strikeLines = [];
function drawLegs(tr){
  strikeLines.forEach(function(l){ try{ candles.removePriceLine(l) }catch(e){} });
  strikeLines = !tr ? [] : tr.legs.map(function(l){
    var sell = l.action==='SELL';
    return candles.createPriceLine({ price:l.strike, color: sell?DN:UP,
      lineWidth: sell?2:1, lineStyle: sell?0:2, axisLabelVisible:true,
      title: l.action+' '+l.strike+' '+l.type }); }); }

/* ---- the continuous index series ----------------------------------------------------
   Revealed left to right by APPENDING. update() is legal here in a way it was not on the
   old per-trade chart, because there is no whitespace after the cursor to write into --
   the series simply grows, exactly as a live chart does. That keeps a 92,000-bar series at
   O(1) per frame instead of a full setData. */
var lastAgg = -1;
function redrawSpot(){
  var top = aggIndex(ST.gi), out = new Array(top+1);
  for(var i=0;i<=top;i++)
    out[i] = { time:AGG.time[i], open:AGG.o[i], high:AGG.h[i], low:AGG.l[i], close:AGG.c[i] };
  candles.setData(out);
  lastAgg = top;
  setRules(); }

/* Every expiry, not only the ones already reached -- the view drops whatever is out of
   frame, and the upcoming one is exactly the line worth seeing early. */
function setRules(){
  rules.setMarks(D.expiries.map(function(e){
    return { j: aggIndex(e.i), label: expLabel(e.date) } })); }

function expLabel(d){ return (+d.slice(8,10))+' '+MON[+d.slice(5,7)-1]+' EXPIRY' }

function advanceSpot(){
  var j = aggIndex(ST.gi);
  if(j === lastAgg){
    candles.update({ time:AGG.time[j], open:AGG.o[j], high:AGG.h[j],
                     low:AGG.l[j], close:AGG.c[j] });
  } else if(j === lastAgg + 1){
    candles.update({ time:AGG.time[j], open:AGG.o[j], high:AGG.h[j],
                     low:AGG.l[j], close:AGG.c[j] });
    lastAgg = j;
  } else {
    redrawSpot();                       /* a jump: flat stretches are revealed at once */
  } }

/* ---- the trade-specific premium series ---------------------------------------------
   Still mutate-in-place plus setData, because this one IS revealed into a reserved axis:
   the position's whole life is known when it opens, and the frame is pinned to it so the
   line grows across a window that does not move. */
var premArr = [], premView = [], creditLine = null;
var bnd = { plo:0, phi:0, credit:0 };

function loadPremium(tr){
  premView = [];
  if(tr){
    var a = aggIndex(tr.i0), b = aggIndex(tr.i1), seen = {};
    for(var g=tr.i0; g<=tr.i1; g++){
      var j = aggIndex(g), v = tr.pAt(g);
      if(v==null) continue;
      seen[j] = { time:AGG.time[j], value:v, g:g }; }
    for(var k in seen) premView.push(seen[k]);
    premView.sort(function(x,y){ return x.time-y.time }); }
  premArr = premView.map(function(b){ return { time:b.time } });
  premLine.setData(premArr);
  if(creditLine){ try{ premLine.removePriceLine(creditLine) }catch(e){} creditLine=null }
  if(tr){
    bnd.plo = tr.entry_credit; bnd.phi = tr.entry_credit; bnd.credit = tr.entry_credit;
    creditLine = premLine.createPriceLine({ price:tr.entry_credit, color:FAINT,
      lineWidth:1, lineStyle:2, axisLabelVisible:true, title:'credit' }); }
  premFrame(); }

/* The premium axis grows with WHAT HAS BEEN REVEALED, seeded with zero-to-credit. Pinning
   it to the whole trade hands the viewer the ending: an axis running to 300 while the line
   sits at 118 announces the loss before it happens. */
/* The floor is the REVEALED LOW, not zero. Anchoring at zero was honest about where a
   decaying premium is heading but wasted the bottom half of the panel on empty space --
   a line living between 29 and 40 was drawn inside an axis running 0 to 44. The credit is
   always kept in frame, so the one reference the chart needs is never lost. */
premLine.applyOptions({ autoscaleInfoProvider:function(){
  var lo = Math.min(bnd.plo, bnd.credit), hi = Math.max(bnd.phi, bnd.credit);
  var p = Math.max((hi-lo)*0.14, 0.4);
  return { priceRange:{ minValue:lo-p, maxValue:hi+p } } } });

var PAD_PX = 52;
function premFrame(){
  var n = Math.max(premView.length,1), el = $('prem');
  var ps = 60; try{ ps = premChart.priceScale('right').width()||60 }catch(e){}
  var draw = Math.max((el.clientWidth||900) - ps, 120);
  var bs = Math.max((draw - 2*PAD_PX)/n, 0.02);
  var shown = premArr.filter(function(b){ return b.value!=null }).length;
  premChart.applyOptions({ timeScale:{ barSpacing:bs,
    rightOffset: Math.max(n - shown, 0) + PAD_PX/bs } }); }

function paintPremium(){
  var tr = openTrade();
  if(!tr) return;
  for(var i=0;i<premView.length;i++){
    if(premView[i].g > ST.gi) break;
    if(premArr[i].value == null){
      premArr[i] = { time:premView[i].time, value:premView[i].value };
      bnd.plo = Math.min(bnd.plo, premView[i].value);
      bnd.phi = Math.max(bnd.phi, premView[i].value); } }
  premLine.setData(premArr); premFrame(); }

var eqArr = [];
function eqFrame(){
  var el = $('eq'), w = el.clientWidth||300;
  var ps = 56; try{ ps = eqChart.priceScale('right').width()||56 }catch(e){}
  var draw = Math.max(w-ps, 100);
  eqChart.applyOptions({ timeScale:{
    barSpacing: Math.max(draw/Math.max(eqArr.length,1), 0.02),
    rightOffset: Math.max(eqArr.length - 1 - ST.closed.length, 0) } }); }

/* ------------------------------------------------------------------ payoff panel
   Two curves. AT EXPIRY is exact and needs nothing but strikes and the net credit:
       payoff(S) = credit - sum over legs of sign * intrinsic(S, K)
   AT THIS MOMENT is modelled, priced off the single vol solved from the position's own
   combined premium, and labelled as modelled wherever it appears. Neither uses a per-leg
   price, which is the only reason this panel can exist. */
function intrinsic(t,S,K){ return t==='CE'?Math.max(S-K,0):Math.max(K-S,0) }
function payoffExpiry(tr,S){ var v=tr.entry_credit;
  for(var i=0;i<tr.legs.length;i++){ var l=tr.legs[i];
    v -= (l.action==='SELL'?1:-1)*intrinsic(l.type,S,l.strike) } return v }
function payoffNow(tr,S,T,iv){ return iv==null?null:tr.entry_credit-posValue(tr.legs,S,T,iv) }

var PAY = null;
function drawPayoff(tr, spot, lots, nowSec, premNow){
  var host = $('payoffhost');
  if(!tr){ host.innerHTML=''; PAY=null; $('ivlbl').textContent='no position open'; return }
  var W = host.clientWidth||420, H = host.clientHeight||208;
  var ks = tr.legs.map(function(l){ return l.strike });
  var lo=Math.min.apply(null,ks), hi=Math.max.apply(null,ks);
  var pd = Math.max((hi-lo)*0.5, hi*0.014);
  var x0 = Math.min(lo-pd, spot-pd*0.7), x1 = Math.max(hi+pd, spot+pd*0.7);

  var T = yearsTo(tr.expiry, nowSec);
  /* Inside the last few hours the two curves are the same curve, and a vol solved from a
     position with minutes of life left is noise -- it read "1% vol" on the final bar of
     every trade. These are 2-DTE condors, so this is not an edge case. */
  var NEAR = 0.25/365, expiring = T <= NEAR;
  var iv = (!expiring && premNow!=null) ? solveIV(tr.legs, spot, T, premNow) : null;

  var N=200, xs=[], ye=[], yn=[];
  for(var i=0;i<=N;i++){ var s=x0+(x1-x0)*i/N;
    xs.push(s); ye.push(payoffExpiry(tr,s)); yn.push(payoffNow(tr,s,T,iv)); }
  var mul = lots*tr.lot_size;
  var all = ye.concat(yn.filter(function(v){ return v!=null }));
  var ymin=Math.min.apply(null,all), ymax=Math.max.apply(null,all);
  var yp = Math.max((ymax-ymin)*0.16,1);
  var L=56,R=12,T0=10,B=22;
  var X = sc(x0,x1,L,W-R), Y = sc(ymin-yp,ymax+yp,H-B,T0);
  var svg = S_(host,W,H);
  PAY = { tr:tr, x0:x0, x1:x1, L:L, R:R, W:W, H:H, X:X, Y:Y, mul:mul, T:T, iv:iv,
          spot:spot, lots:lots, top:T0, bot:H-B, svg:svg, cursor:null,
          now: premNow==null ? null : tr.entry_credit - premNow };

  var zero = Y(0), d='';
  for(var i=0;i<=N;i++) d += (i?'L':'M')+X(xs[i]).toFixed(1)+' '+Y(ye[i]).toFixed(1);
  E('clipPath',{id:'cp'},svg).appendChild(
    E('rect',{x:L,y:T0,width:W-L-R,height:Math.max(zero-T0,0)}));
  E('clipPath',{id:'cn'},svg).appendChild(
    E('rect',{x:L,y:zero,width:W-L-R,height:Math.max(H-B-zero,0)}));
  var close='L'+X(x1).toFixed(1)+' '+zero+'L'+X(x0).toFixed(1)+' '+zero+'Z';
  P(d+close,{fill:UP,opacity:.13,'clip-path':'url(#cp)'},svg);
  P(d+close,{fill:DN,opacity:.13,'clip-path':'url(#cn)'},svg);
  P('M'+L+' '+zero+'H'+(W-R),{stroke:FAINT,'stroke-width':1,opacity:.6},svg);
  tr.legs.forEach(function(l){
    P('M'+X(l.strike).toFixed(1)+' '+T0+'V'+(H-B),
      {stroke:l.action==='SELL'?DN:UP,'stroke-width':1,'stroke-dasharray':'3 3',
       opacity:.7},svg); });
  if(iv!=null){ var dn='';
    for(var i=0;i<=N;i++) dn += (i?'L':'M')+X(xs[i]).toFixed(1)+' '+Y(yn[i]).toFixed(1);
    P(dn,{stroke:ACC,'stroke-width':1.6,'stroke-dasharray':'4 3',opacity:.95},svg); }
  P(d,{stroke:INK,'stroke-width':1.9,'stroke-linejoin':'round'},svg);

  /* breakevens -- the two numbers a premium seller actually watches the index against */
  for(var i=1;i<=N;i++){
    if((ye[i-1]<0)!==(ye[i]<0)){
      var f = Math.abs(ye[i-1])/(Math.abs(ye[i-1])+Math.abs(ye[i]));
      var bx = X(xs[i-1]+(xs[i]-xs[i-1])*f);
      P('M'+bx.toFixed(1)+' '+(zero-4)+'v8',{stroke:WARN,'stroke-width':1.6},svg);
      T_(Math.round(xs[i-1]+(xs[i]-xs[i-1])*f), bx, zero-8,'lm',svg,'middle'); } }

  var sx = X(spot);
  P('M'+sx.toFixed(1)+' '+T0+'V'+(H-B),{stroke:ACC,'stroke-width':1.6},svg);
  var hereN = payoffNow(tr,spot,T,iv), hereE = payoffExpiry(tr,spot);
  E('circle',{cx:sx.toFixed(1),cy:Y(hereN==null?hereE:hereN).toFixed(1),r:4,fill:ACC,
              stroke:SURF,'stroke-width':1.5},svg);
  /* the hover rule, parked off-canvas until the cursor arrives */
  PAY.cursor = P('M-10 '+T0+'V'+(H-B),
    {stroke:INK,'stroke-width':1.3,opacity:0,'pointer-events':'none'},svg);

  T_(Math.round(x0), L, H-7,'tk',svg);
  T_(Math.round(x1), W-R, H-7,'tk',svg,'end');
  T_(money(ymax*mul), L-5, Y(ymax).toFixed(1),'tk',svg,'end');
  T_(money(0), L-5, zero.toFixed(1),'tk',svg,'end');
  T_(money(ymin*mul), L-5, Y(ymin).toFixed(1),'tk',svg,'end');
  $('ivlbl').textContent =
      expiring ? 'settling today — the two curves have converged'
    : iv==null ? 'at expiry · the now curve is not solvable from this premium'
    : 'at expiry · at now (' + (iv*100).toFixed(0) + '% vol, modelled)'; }

/* ------------------------------------------------------------------ readouts
   They follow the cursor. Pinned in a corner a tooltip describes a point the eye is not
   looking at, which is most of the reason a tooltip exists at all. Placement flips to the
   other side of the pointer near an edge so the box never leaves the window. */
function place(el, x, y){
  el.classList.remove('hid');
  var w = el.offsetWidth, h = el.offsetHeight, m = 16;
  var left = x + m, top = y - h - m;
  if(left + w > window.innerWidth - 8) left = x - w - m;
  if(top < 8) top = y + m;
  if(top + h > window.innerHeight - 8) top = window.innerHeight - h - 8;
  el.style.left = Math.max(8,left)+'px'; el.style.top = Math.max(8,top)+'px'; }

$('payoffhost').addEventListener('mousemove', function(ev){
  if(!PAY){ return }
  var r = this.getBoundingClientRect();
  var px = (ev.clientX - r.left) * (PAY.W / r.width);
  if(px < PAY.L || px > PAY.W - PAY.R){
    $('payread').classList.add('hid');
    if(PAY.cursor) PAY.cursor.setAttribute('opacity',0);
    return; }
  var s = PAY.x0 + (px - PAY.L)/(PAY.W - PAY.R - PAY.L)*(PAY.x1 - PAY.x0);
  if(PAY.cursor){
    PAY.cursor.setAttribute('d','M'+px.toFixed(1)+' '+PAY.top+'V'+PAY.bot);
    PAY.cursor.setAttribute('opacity','.85'); }
  var e = payoffExpiry(PAY.tr, s), n = payoffNow(PAY.tr, s, PAY.T, PAY.iv);
  var el = $('payread');
  /* Rupees, not points. Points are the strategy; rupees are what the reader is deciding
     about, and a payoff panel that answers in points makes them do the lot arithmetic. */
  var now = PAY.now;
  el.innerHTML = '<div class="rk">if NIFTY were '+Math.round(s)+'</div>'+
    '<div class="rr" style="color:var(--faint)"><span>at expiry</span></div>'+
    '<div class="big '+cls(e)+'">'+money(e*PAY.mul)+'</div>'+
    (n==null?'':'<div class="rr"><span>right now</span><b class="'+cls(n)+'">'+
      money(n*PAY.mul)+'</b></div>')+
    (now==null?'':'<div class="rr"><span>vs holding here</span><b class="'+cls(e-now)+
      '">'+money((e-now)*PAY.mul)+'</b></div>')+
    '<div class="sep"></div>'+
    '<div class="rr"><span>from spot</span><b>'+(s>=PAY.spot?'+':'−')+
      Math.abs(Math.round(s-PAY.spot))+'</b></div>'+
    '<div class="rr"><span>'+PAY.lots+' lot'+(PAY.lots===1?'':'s')+'</span><b>'+
      sgn(e,2)+' pts</b></div>';
  place(el, ev.clientX, ev.clientY); });
$('payoffhost').addEventListener('mouseleave', function(){
  $('payread').classList.add('hid');
  if(PAY && PAY.cursor) PAY.cursor.setAttribute('opacity',0); });

var mouse = {x:0,y:0};
window.addEventListener('mousemove', function(e){ mouse.x=e.clientX; mouse.y=e.clientY });

premChart.subscribeCrosshairMove(function(pm){
  var el = $('premread'), tr = openTrade();
  if(!pm||!pm.time||!pm.point||!tr){ el.classList.add('hid'); return }
  var b = premAtTime(pm.time);
  if(!b || b.g > ST.gi || b.value==null){ el.classList.add('hid'); return }
  var lots = ST.lots, mul = lots*tr.lot_size;
  var pts = tr.entry_credit - b.value;
  el.innerHTML = '<div class="rk">'+fmtStamp(b.time)+'</div>'+
    '<div class="big '+cls(pts)+'">'+money(pts*mul)+'</div>'+
    '<div class="rr"><span>P&amp;L</span><b class="'+cls(pts)+'">'+sgn(pts)+' pts</b></div>'+
    '<div class="rr"><span>cost to close</span><b>'+b.value.toFixed(2)+'</b></div>'+
    '<div class="rr"><span>credit taken</span><b>'+tr.entry_credit.toFixed(2)+'</b></div>';
  place(el, mouse.x, mouse.y); });

function premAtTime(t){
  var sec = typeof t==='number' ? t
          : (t&&t.year) ? Date.UTC(t.year,t.month-1,t.day)/1000 : NaN;
  if(isNaN(sec)) return null;
  for(var i=0;i<premView.length;i++) if(premView[i].time===sec) return premView[i];
  return null; }

spotChart.subscribeCrosshairMove(function(pm){
  var el = $('spotread');
  if(!pm||!pm.time||!pm.point){ el.classList.add('hid'); return }
  var sec = typeof pm.time==='number' ? pm.time : NaN;
  if(isNaN(sec)){ el.classList.add('hid'); return }
  var j = -1;
  for(var i=aggIndex(ST.gi); i>=0; i--) if(AGG.time[i]===sec){ j=i; break }
  if(j<0){ el.classList.add('hid'); return }
  var tr = openTrade();
  var body = '<div class="rk">'+fmtStamp(AGG.time[j])+'</div>'+
    '<div class="big">'+AGG.c[j].toFixed(2)+'</div>'+
    '<div class="rr"><span>high / low</span><b>'+AGG.h[j].toFixed(0)+' · '+
      AGG.l[j].toFixed(0)+'</b></div>';
  if(tr){
    var g = AGG.src ? AGG.src[j] : j;
    var v = (g>=tr.i0 && g<=tr.i1) ? tr.pAt(g) : null;
    if(v!=null){
      var lots = ST.lots, pts = tr.entry_credit - v;
      body += '<div class="sep"></div><div class="rr"><span>position</span>'+
        '<b class="'+cls(pts)+'">'+money(pts*lots*tr.lot_size)+'</b></div>'; } }
  el.innerHTML = body;
  place(el, mouse.x, mouse.y); });

/* ------------------------------------------------------------------ statistics as of now
   Every figure is computed from the trades CLOSED SO FAR. That is the point of the screen:
   a win rate of 100% after four trades is a fact about four trades, and watching it decay
   is more informative than being handed the final number. */
var MIN_FOR_RATIOS = 30;
function stats(){
  var c=ST.closed, n=c.length;
  var wins=c.filter(function(t){return t.pnl>0}), losses=c.filter(function(t){return t.pnl<=0});
  var sw=wins.reduce(function(a,t){return a+t.pnl},0), sl=losses.reduce(function(a,t){return a+t.pnl},0);
  var roms=c.map(function(t){return t.rom});
  var mean=roms.length?roms.reduce(function(a,b){return a+b},0)/roms.length:0, sd=0;
  if(roms.length>1) sd=Math.sqrt(roms.reduce(function(a,b){return a+(b-mean)*(b-mean)},0)/(roms.length-1));
  var days = n>1 ? (Date.parse(c[n-1].exit)-Date.parse(c[0].entry))/86400000 : 0;
  var perYear = days>0 ? n*365.25/days : 0;
  /* The engine refuses a ratio below 30 trades, and so does this. Left ungated, five
     winning trades in a quiet August produced a Sharpe of 33 on screen -- correct
     arithmetic, and the kind of number that costs a page its credibility. */
  var sharpe = (sd>0&&perYear>0&&n>=MIN_FOR_RATIOS)?(mean/sd)*Math.sqrt(perYear):null;
  var streak=0;
  for(var i=n-1;i>=0;i--){ var w=c[i].pnl>0;
    if(i===n-1) streak=w?1:-1;
    else if((w&&streak>0)||(!w&&streak<0)) streak+=w?1:-1; else break }
  return { n:n, wins:wins.length, losses:losses.length, winRate:n?wins.length/n:null,
    avgWin:wins.length?sw/wins.length:null, avgLoss:losses.length?sl/losses.length:null,
    pf:(sl<0&&sw>0)?sw/-sl:null, sharpe:sharpe, streak:streak,
    best:n?Math.max.apply(null,c.map(function(t){return t.pnl})):null,
    worst:n?Math.min.apply(null,c.map(function(t){return t.pnl})):null }; }

function renderStats(){
  var s=stats(), out=[];
  function card(k,v,klass,sub,extra){
    out.push('<div class="st"><div class="k">'+k+'</div><div class="v '+(klass||'')+'">'+
      v+'</div>'+(extra||'')+(sub?'<div class="s">'+sub+'</div>':'')+'</div>') }
  card('Closed', String(s.n), s.n?'':'dim', s.n?s.wins+'W · '+s.losses+'L':'none yet');
  card('Win rate', s.winRate==null?'—':(s.winRate*100).toFixed(0)+'%',
       s.winRate==null?'dim':'', '',
       s.winRate==null?'':'<div class="wr"><i style="width:'+(s.winRate*100).toFixed(0)+'%"></i></div>');
  card('Avg win', money(s.avgWin), s.avgWin==null?'dim':'g', s.wins?'over '+s.wins:'');
  card('Avg loss', money(s.avgLoss), s.avgLoss==null?'dim':'b', s.losses?'over '+s.losses:'');
  card('Profit factor', s.pf==null?'—':s.pf.toFixed(2), s.pf==null?'dim':(s.pf>=1?'g':'b'),'won / lost');
  card('Sharpe', s.sharpe==null?'—':s.sharpe.toFixed(2), s.sharpe==null?'dim':'',
       s.n>=MIN_FOR_RATIOS?'annualised':'needs '+MIN_FOR_RATIOS+' ('+s.n+')');
  card('Max drawdown', ST.maxdd?'−'+Math.abs(ST.maxdd*100).toFixed(1)+'%':'0.0%',
       ST.maxdd?'b':'dim','peak to trough');
  card('Streak', s.n?(s.streak>0?s.streak+'W':(-s.streak)+'L'):'—',
       s.n?(s.streak>0?'g':'b'):'dim','consecutive');
  card('Best trade', money(s.best), s.best==null?'dim':'g','');
  card('Worst trade', money(s.worst), s.worst==null?'dim':'b','');
  $('stats').innerHTML = out.join('');

  var ret = ST.equity/ST.capital - 1;
  $('herov').textContent = money(ST.equity);
  $('heropill').className = 'pill '+(ret>=0?'p-up':'p-dn');
  $('heropill').textContent = (ret>=0?'+':'−')+Math.abs(ret*100).toFixed(2)+'%';
  $('herod').textContent = money(ST.equity-ST.capital)+' on '+money(ST.capital);
  $('herosub').textContent = s.n ? s.n+' of '+TR.length+' trades closed'
                                 : 'nothing closed yet'; }

/* ------------------------------------------------------------------ open position */
function openTrade(){
  var tr = TR[ST.ti];
  return (tr && ST.gi >= tr.i0 && ST.gi <= tr.i1) ? tr : null; }

function renderOpen(){
  var tr = openTrade();
  if(!tr){
    var nxt = TR[ST.ti];
    $('open').innerHTML = '<div class="idle">flat'+
      (nxt ? '<br>next entry ' + nxt.entry : '<br>replay complete')+'</div>';
    $('dockopen').innerHTML = '<div class="pad"><p>No position open.</p></div>';
    return; }
  var spot = SP.c[ST.gi], lots = ST.lots, mul = lots*tr.lot_size;
  var v = tr.pAt(ST.gi);
  var pts = v==null?null:tr.entry_credit-v, pnlR = pts==null?null:pts*mul;
  var legs = tr.legs.map(function(l){
    /* Lots in the third column, so a row reads as the order it is. Whether the strike is
       through spot is a chip beside it rather than a distance in points: the chart already
       draws every strike against the index, which answers "how much room is left" better
       than a bare number with no unit did. */
    var itm = l.type==='CE' ? spot>l.strike : spot<l.strike;
    return '<div class="leg'+(itm?' itm':'')+'">'+
      '<span class="side-tag '+(l.action==='SELL'?'sell':'buy')+'">'+l.action+'</span>'+
      '<span class="k">'+l.strike+' '+l.type+
        (itm?' <em class="itmchip">ITM</em>':'')+'</span>'+
      '<span class="d">'+lots+' lot'+(lots===1?'':'s')+'</span></div>';
  }).join('');

  var html = '<div class="pos">'+
    '<div class="ph"><span class="t">Trade '+tr.n+'</span>'+
      '<span class="n">exp '+tr.expiry+'</span></div>'+
    '<div class="pnl"><span class="k">unrealised</span>'+
      '<span class="v '+cls(pnlR)+'">'+money(pnlR)+'</span></div>'+
    '<div class="legs">'+legs+'</div>'+
    '<div class="meta">in '+tr.entry.slice(5)+' at '+tr.spot_entry+
      ' · credit '+tr.entry_credit.toFixed(2)+' pts'+
      '<br>margin '+money(marginPerLot(tr))+'/lot · '+money(marginPerLot(tr)*lots)+
      ' committed</div></div>';
  $('open').innerHTML = html;
  $('dockopen').innerHTML = html; }

/* ------------------------------------------------------------------ trade log */
function renderHistory(flash){
  var rows = ST.closed.slice().reverse().map(function(t,i){
    return '<tr'+(flash&&i===0?' class="new"':'')+'>'+
      '<td class="n">'+t.n+'</td><td>'+t.entry+'</td><td>'+t.exit+'</td>'+
      '<td>'+t.reason+'</td><td class="n">'+t.lots+'</td>'+
      '<td class="n">'+sgn(t.pts)+'</td>'+
      '<td class="n '+cls(t.pnl)+'">'+money(t.pnl)+'</td>'+
      '<td class="n">'+money(t.equity)+'</td></tr>' }).join('');
  $('hist').innerHTML = rows ||
    '<tr class="empty"><td colspan="8">nothing closed yet — press play</td></tr>';
  $('cnt-closed').textContent = ST.closed.length; }

/* ------------------------------------------------------------------ the loop */
function seedEquity(){
  /* The opening balance is REAL data and must be the OLDEST point: update() refuses to
     insert before the start of a series, so seeding the exits alone and adding the start
     afterwards threw and left the page frozen. */
  eqArr = [{ time:tsec(TR[0].entry), value:ST.capital }];
  TR.forEach(function(t){ eqArr.push({ time:tsec(t.exit) }) });
  eqLine.setData(eqArr); eqFrame(); }
function tsec(s){ return Date.UTC(+s.slice(0,4),+s.slice(5,7)-1,+s.slice(8,10),
                                  +s.slice(11,13),+s.slice(14,16))/1000 }
function paintEquity(k,v){
  if(eqArr[k+1]) eqArr[k+1] = { time:eqArr[k+1].time, value:v };
  eqLine.setData(eqArr); eqFrame(); }

function markers(){
  var items = [];
  for(var i=0;i<TR.length;i++){
    var t = TR[i];
    if(t.i0 > ST.gi) break;
    var closed = t.i1 <= ST.gi;
    /* RETURN ON MARGIN, not return on points. A trade's percentage has to be against what
       it tied up, or a 50-point win on a 250-point margin and the same win on a 900-point
       margin read as the same trade. */
    var rom = t.margin_points>0 ? t.net_points/t.margin_points*100 : 0;
    items.push({
      t0: AGG.time[aggIndex(t.i0)], p0: SP.c[t.i0],
      t1: closed ? AGG.time[aggIndex(t.i1)] : AGG.time[aggIndex(ST.gi)],
      p1: closed ? SP.c[t.i1] : null,
      n: t.n, win: t.net_points >= 0,
      pct: (rom>=0?'+':'−')+Math.abs(rom).toFixed(1)+'%',
      open: !closed }); }
  tradeMarks.setItems(items); }

function refresh(){
  var tr = openTrade();
  $('clockv').textContent = fmtStamp(SP.time[ST.gi]);
  $('tradelbl').textContent = tr ? ('in trade '+tr.n+' of '+TR.length)
    : (ST.ti>=TR.length ? 'complete' : 'flat · next is '+TR[ST.ti].n);
  drawLegs(tr);
  renderOpen();
  var lots = tr ? ST.lots : 0;
  drawPayoff(tr, tr?SP.c[ST.gi]:0, lots, SP.time[ST.gi], tr?tr.pAt(ST.gi):null);
  $('scrub').value = ST.gi; }

function openIt(tr){
  ST.open = true;
  /* The lot count is fixed at the moment of entry and then read everywhere else.
     Recomputing it per repaint would be wrong twice over: sizingBase() advances the
     compounding period as a side effect, and a position that silently resized mid-trade
     is not a position anyone could have held. */
  ST.lots = lotsFor(sizingBase(tr), tr);
  loadPremium(tr);
  drawLegs(tr); }

function closeIt(){
  var tr = TR[ST.ti], lots = ST.lots;
  /* EXACTLY server/sizing.py: the one-lot rupee P&L -- already net of that lot's charges
     -- times the lot count. Re-deriving it from points and charges here would be a second
     implementation of the same arithmetic and the two would drift. A trade needing more
     margin than the whole allowance is skipped, not taken at one lot. */
  var pnl = lots>0 ? tr.pnl_rupees*lots : 0;
  ST.equity += pnl;
  ST.peak = Math.max(ST.peak, ST.equity);
  ST.maxdd = Math.min(ST.maxdd, (ST.equity-ST.peak)/ST.peak);
  ST.closed.push({ n:tr.n, entry:tr.entry, exit:tr.exit,
    reason: lots>0?tr.exit_reason:'SKIPPED · margin',
    pts:tr.net_points, lots:lots, pnl:pnl, equity:ST.equity,
    rom: tr.margin_points>0 ? tr.net_points/tr.margin_points : 0 });
  paintEquity(ST.ti, ST.equity);
  ST.ti++; ST.open = false; }

/* SPEED IS MARKET MINUTES PER REAL SECOND. One base bar is BASE minutes of market, so the
   bar rate is mps/BASE -- at a 1-minute base, "1h per second" is sixty bars a second. A
   timer cannot fire that often reliably, so above 60 ticks a second the loop advances
   several bars per tick and paints once, which keeps the rate honest without asking
   setInterval for a 4-millisecond period it will not honour. */
var BASE = D.base || 1;
function step(){
  for(var k=0;k<ST.stride;k++) if(!tick()) break;
  paintPremium(); markers(); refresh(); }

function tick(){
  if(ST.ti >= TR.length){ pause(); finish(); return false }
  var tr = TR[ST.ti];
  /* THE CLOCK RUNS THROUGH THE FLAT STRETCHES TOO.
     An earlier version jumped straight from an exit to the next entry on the grounds that
     a weekly condor is open two days in seven and the other five are dead air. They are
     not: waiting is what the strategy does most of the time, and a replay that teleports
     over it hides both how long the account sits idle and what the index did to the
     levels the next position will be built around. */
  if(ST.open && ST.gi >= tr.i1){
    closeIt(); renderHistory(true); renderStats();
    if(ST.ti >= TR.length){ pause(); finish() }
    /* stop the batch on a close, so a trade ending is never buried inside one frame */
    return false; }
  if(ST.gi >= SP.n - 1){ pause(); finish(); return false }
  ST.gi++; advanceSpot();
  if(!ST.open && ST.gi >= tr.i0) openIt(tr);
  return true; }

function finish(){
  $('tradelbl').textContent = 'complete · '+TR.length+' trades';
  $('open').innerHTML = '<div class="idle">replay complete<br>'+TR.length+
    ' trades · ending '+money(ST.equity)+'</div>'; }

function play(){
  lockConfig();
  if(ST.ti >= TR.length) seekTrade(0);
  ST.playing = true; $('play').textContent = '❙❙ Pause';
  var bps = ST.mps / BASE, hz = Math.min(bps, 60);
  ST.stride = Math.max(1, Math.round(bps / hz));
  clearInterval(ST.timer); ST.timer = setInterval(step, 1000/hz); }
function pause(){ ST.playing=false; $('play').textContent='▶ Play';
  clearInterval(ST.timer); ST.timer=null }
function toggle(){ ST.playing?pause():play() }

/* Jumping REPLAYS the account up to the target rather than teleporting the curve: the
   equity at trade 30 is a function of the 29 before it, and the lot count at trade 30 is a
   function of that equity. Skipping the arithmetic would show a position size the account
   could never have taken. */
function seekTrade(i){
  pause();
  i = Math.max(0, Math.min(TR.length-1, i));
  ST.equity = ST.capital; ST.peak = ST.capital; ST.closed = []; ST.maxdd = 0;
  ST.base = ST.capital; ST.baseKey = null; ST.lots = 0;
  seedEquity();
  for(var k=0;k<i;k++){
    ST.ti = k;
    var tr = TR[k], lots = lotsFor(sizingBase(tr), tr);
    var pnl = lots>0 ? tr.pnl_rupees*lots : 0;
    ST.equity += pnl; ST.peak = Math.max(ST.peak,ST.equity);
    ST.maxdd = Math.min(ST.maxdd,(ST.equity-ST.peak)/ST.peak);
    ST.closed.push({ n:tr.n, entry:tr.entry, exit:tr.exit,
      reason: lots>0?tr.exit_reason:'SKIPPED · margin', pts:tr.net_points, lots:lots,
      pnl:pnl, equity:ST.equity, rom: tr.margin_points>0?tr.net_points/tr.margin_points:0 });
    if(eqArr[k+1]) eqArr[k+1] = { time:eqArr[k+1].time, value:ST.equity }; }
  eqLine.setData(eqArr);
  ST.ti = i; ST.gi = TR[i].i0; ST.open = true;
  redrawSpot(); openIt(TR[i]); paintPremium(); markers();
  renderHistory(false); renderStats(); refresh(); eqFrame(); }

function seekEnd(){
  seekTrade(TR.length-1);
  var tr = TR[TR.length-1];
  ST.gi = tr.i1; advanceSpot(); paintPremium();
  closeIt(); renderHistory(false); renderStats(); markers(); refresh(); finish(); }

/* Scrubbing works on the INDEX, not on trades, now that the chart is continuous -- so
   dragging lands wherever you point and the nearest trade boundary is inferred. */
function seekIndex(g){
  var i = 0;
  while(i < TR.length-1 && TR[i].i1 < g) i++;
  if(g >= TR[TR.length-1].i1){ seekEnd(); return }
  seekTrade(i);
  if(g > TR[i].i0 && g <= TR[i].i1){
    ST.gi = g; advanceSpot(); paintPremium(); markers(); refresh(); } }

/* ------------------------------------------------------------------ controls */
$('play').onclick = toggle;
$('prev').onclick = function(){ seekTrade(ST.ti-1) };
$('next').onclick = function(){ seekTrade(ST.ti+1) };
$('rewind').onclick = function(){ seekTrade(0) };

function bindGroup(sel, apply){
  Array.prototype.forEach.call(document.querySelectorAll(sel), function(b){
    b.onclick = function(){
      Array.prototype.forEach.call(document.querySelectorAll(sel), function(o){
        o.classList.toggle('on', o===b) });
      apply(b); } }) }

$('spd').onchange = function(){ ST.mps = +this.value; if(ST.playing) play() };

/* Changing timeframe keeps the CLOCK. The cursor is a base-resolution index, so it does
   not move at all -- only the aggregation under it changes, which is the whole advantage
   of aggregating client-side from one series. */
bindGroup('[data-tf]', function(b){
  var playing = ST.playing; pause();
  ST.tf = +b.dataset.tf;
  buildAgg(ST.tf);
  redrawSpot(); setRules(); loadPremium(openTrade()); paintPremium(); markers(); refresh();
  if(playing) play(); });

var scrub = $('scrub');
scrub.max = SP.n - 1;
scrub.oninput = function(){ seekIndex(+scrub.value) };

/* THE SIZING CONTROLS HAVE A BEFORE AND AN AFTER.
   Starting capital and the share of it committed per trade are decisions made once, before
   the first trade -- after that they are facts about the run you are watching, and leaving
   them as editable fields both clutters the panel and invites a mid-replay change whose
   effect is a silent full re-simulation. So they lock on the first play and collapse to a
   line of text with a reset beside it. */
var CMP_LABEL = { trade:'every trade', week:'weekly', month:'monthly',
                  quarter:'quarterly', never:'never' };
function applyCapital(){
  var cap = Math.max(50000, +$('cap').value || D.capital);
  var dep = Math.min(100, Math.max(1, +$('dep').value || 10));
  ST.capital = cap; D.deploy = dep/100; ST.compound = $('cmp').value;
  try{ eqLine.removePriceLine(startLine) }catch(e){}
  startLine = eqLine.createPriceLine({ price:cap, color:FAINT, lineWidth:1, lineStyle:2,
                                       axisLabelVisible:false });
  $('cfgtxt').innerHTML = '<b>'+money(cap)+'</b> · <b>'+dep+'%</b> per trade · compounds <b>'+
    CMP_LABEL[ST.compound]+'</b>';
  seekTrade(ST.ti); }
$('cap').onchange = applyCapital;
$('dep').onchange = applyCapital;
$('cmp').onchange = applyCapital;

function lockConfig(){
  if(ST.locked) return;
  ST.locked = true;
  applyCapital();
  $('cfgset').classList.add('hid');
  $('cfgline').classList.remove('hid'); }

$('cfgreset').onclick = function(){
  ST.locked = false;
  $('cfgset').classList.remove('hid');
  $('cfgline').classList.add('hid');
  seekTrade(0); };

document.addEventListener('keydown', function(e){
  if(e.target.tagName === 'INPUT') return;
  if(e.code==='Space'){ e.preventDefault(); toggle() }
  else if(e.key==='ArrowRight'){ seekTrade(ST.ti+1) }
  else if(e.key==='ArrowLeft'){ seekTrade(ST.ti-1) } });

/* ------------------------------------------------------------------ the bottom dock
   It floats above the page rather than occupying a row in it, so opening the trade log
   never reflows or shrinks the charts -- which is the thing that makes a resizable panel
   inside a layout so unpleasant to use. */
(function(){
  var dock = $('dock'), grip = $('dockgrip'), app = document.querySelector('.app');
  var MIN = 140, MAX = Math.round(window.innerHeight*0.85), KEY='stratify.replay.dock';
  var BAR = 41;                       /* the collapsed bar */
  /* Past this the dock stops reserving space and goes back to floating over the page.
     Below it, giving the space back to the chart is what a reader expects from dragging a
     bottom panel -- and the chart growing into it is the point of the flex layout above. */
  var RESERVE_MAX = Math.round(window.innerHeight*0.42);
  var raf = 0;
  function reserve(){
    var h = dock.classList.contains('min') ? BAR
          : Math.min(parseInt(getComputedStyle(dock).height,10) || 204, RESERVE_MAX);
    app.style.setProperty('--dockrest', h+'px');
    if(raf) cancelAnimationFrame(raf);
    raf = requestAnimationFrame(function(){ raf = 0; resizeCharts() }); }
  function setH(h){ h = Math.min(MAX, Math.max(MIN, h));
    dock.style.setProperty('--dockh', h+'px');
    try{ localStorage.setItem(KEY, String(h)) }catch(e){}
    reserve(); }
  try{ var sv = localStorage.getItem(KEY); if(sv) setH(+sv);
       if(localStorage.getItem(KEY+'.min')==='1') dock.classList.add('min') }catch(e){}
  window.addEventListener('resize', reserve);

  bindGroup('[data-pane]', function(b){
    Array.prototype.forEach.call(document.querySelectorAll('.dockpane'), function(p){
      p.classList.toggle('on', p.id === 'pane-'+b.dataset.pane) });
    dock.classList.remove('min');
    try{ localStorage.setItem(KEY+'.min','0') }catch(e){}
    reserve(); });

  $('dockmin').onclick = function(){
    var min = dock.classList.toggle('min');
    try{ localStorage.setItem(KEY+'.min', min?'1':'0') }catch(e){}
    $('dockmin').textContent = min ? '▲' : '▼';
    reserve(); };
  $('dockmax').onclick = function(){
    dock.classList.remove('min');
    $('dockmin').textContent = '▼';
    var cur = parseInt(getComputedStyle(dock).height,10);
    setH(cur > MAX*0.7 ? 204 : MAX); };

  var dragging = false;
  grip.addEventListener('mousedown', function(e){ e.preventDefault(); dragging = true;
    document.body.classList.add('rowdrag') });
  window.addEventListener('mousemove', function(e){
    if(!dragging) return; setH(window.innerHeight - e.clientY); });
  window.addEventListener('mouseup', function(){
    if(!dragging) return; dragging = false; document.body.classList.remove('rowdrag'); });
  reserve();
})();

/* ------------------------------------------------------------------ resizable rail
   Width is remembered per browser. localStorage can throw outright in a private window or
   a sandboxed frame, so every access is guarded -- a page that failed to load because it
   could not remember a sidebar width would be an absurd way to lose a reader. */
(function(){
  var app = document.querySelector('.app'), grip = $('grip');
  var MIN=270, MAX=640, KEY='stratify.replay.rail';
  try{ var sv = localStorage.getItem(KEY);
       if(sv) app.style.setProperty('--sw', Math.min(MAX,Math.max(MIN,+sv))+'px') }catch(e){}
  var dragging = false;
  function move(ev){
    if(!dragging) return;
    var x = (ev.touches?ev.touches[0].clientX:ev.clientX) - app.getBoundingClientRect().left;
    var w = Math.min(MAX, Math.max(MIN, x));
    app.style.setProperty('--sw', w+'px');
    try{ localStorage.setItem(KEY, String(w)) }catch(e){}
    resizeCharts(); }
  function up(){ if(!dragging) return; dragging=false; grip.classList.remove('on');
    document.body.classList.remove('dragging'); resizeCharts(); }
  grip.addEventListener('mousedown', function(e){ e.preventDefault(); dragging=true;
    grip.classList.add('on'); document.body.classList.add('dragging') });
  window.addEventListener('mousemove', move);
  window.addEventListener('mouseup', up);
  grip.addEventListener('dblclick', function(){
    app.style.setProperty('--sw','352px');
    try{ localStorage.setItem(KEY,'352') }catch(e){}
    resizeCharts(); });
})();

buildAgg(ST.tf);
resizeCharts();
seekTrade(0);
/* One free zoom-out on first paint: enough bars that the continuous chart reads as a
   chart rather than as a single session. */
spotChart.timeScale().applyOptions({ barSpacing: 4, rightOffset: 12 });
})();
"""


STRUCTURE_NAME = {
    "short_strangle": "Short Strangle", "iron_condor": "Iron Condor",
    "iron_fly": "Iron Fly", "credit_spread": "Credit Spread",
    "long_option": "Long Option",
}


def _guards(spec):
    """The exit rules, in the rail under the strategy name. Someone watching a position run
    against them needs to know whether anything is meant to stop it -- and for a structure
    that carries no stop, saying so is worth more than an empty field."""
    p = spec.get("params") or {}
    out = []
    if p.get("sl_mult") is not None:
        out.append(f"stop {p['sl_mult']}× credit")
    if p.get("sl_pct") is not None:
        out.append(f"stop −{p['sl_pct'] * 100:.0f}%")
    if p.get("tp_pct") is not None:
        out.append(f"target {p['tp_pct'] * 100:.0f}%")
    if spec.get("exit_time"):
        out.append(f"square off {spec['exit_time']}")
    return " · ".join(out) or "no stop, no target — held to expiry"


def render(payload, track, capital=1_000_000, deploy=0.10, report_url=None, title=None):
    """The replay page. A pure function of the track -- the same contract as fullreport."""
    spec = payload.get("spec") or {}
    s = payload.get("summary") or {}
    name = title or STRUCTURE_NAME.get(spec.get("structure"),
                                       str(spec.get("structure", "strategy")).title())
    period = s.get("period") or {}
    trades = track.get("trades") or []
    spot = track.get("spot") or {}
    base_tf = track.get("bucket_minutes") or 1
    n_legs = len(trades[0]["legs"]) if trades else 0

    speeds = "".join(
        f'<option value="{v}"{" selected" if v == DEFAULT_SPEED else ""}>{_e(lbl)}</option>'
        for v, lbl in SPEEDS)
    tfs = "".join(
        f'<button class="tbtn{" on" if v == 5 else ""}" data-tf="{v}">'
        f'{v if v < 60 else v // 60}{"m" if v < 60 else "h"}</button>'
        for v in TIMEFRAMES if v >= base_tf)

    data = {"capital": capital, "deploy": deploy, "spot": spot, "base": base_tf,
            "expiries": track.get("expiries") or [], "trades": trades}

    side_note = (" Per-leg P&amp;L is not shown anywhere and cannot be: a per-leg P&amp;L "
                 "is a per-leg price by another name.")

    body = f"""<div class="app">

<aside class="side" id="side">
  <div class="brand">
    <span class="kick">Stratify · replay</span>
    {f'<a class="back" href="{_e(report_url)}">&larr; report</a>' if report_url else ""}
  </div>
  <div class="sname">{_e(name)}</div>
  <div class="sper">{_e(period.get("from", "?"))} → {_e(period.get("to", "?"))}
    &middot; {len(trades)} trades<br><b>{_e(_guards(spec))}</b></div>

  <div class="sh"><b>Account</b><i>as of this moment</i></div>
  <div class="hero">
    <div class="k">equity</div>
    <div class="v" id="herov">—</div>
    <div class="d"><span class="pill p-up" id="heropill">—</span>
      <span id="herod">—</span></div>
    <div class="sub" id="herosub">—</div>
    <div class="cfg" id="cfg">
      <div class="cfgset" id="cfgset">
        <label>start <input id="cap" type="number" step="50000" min="50000"
               value="{int(capital)}"></label>
        <label>capital <input id="dep" type="number" step="1" min="1" max="100"
               value="{deploy * 100:.0f}"><span>% per trade</span></label>
        <label>compound
          <select id="cmp">
            <option value="trade">every trade</option>
            <option value="week">weekly</option>
            <option value="month">monthly</option>
            <option value="quarter">quarterly</option>
            <option value="never">never</option>
          </select></label>
      </div>
      <div class="cfgline hid" id="cfgline">
        <span id="cfgtxt">&mdash;</span>
        <button class="lnk" id="cfgreset">reset</button>
      </div>
    </div>
  </div>

  <div class="sh"><b>Open position</b><i>live</i></div>
  <div id="open"></div>

  <div class="sh"><b>Equity curve</b><i>per closed trade</i></div>
  <div class="eqwrap"><div id="eq"></div></div>

  <div class="sh"><b>Performance</b><i>closed trades only</i></div>
  <div class="stats" id="stats"></div>
</aside>

<div class="grip" id="grip" title="drag to resize · double-click to reset"></div>

<main class="main">
  <div class="deck">
    <button class="tbtn" id="rewind" title="restart">&#x21BA;</button>
    <button class="tbtn" id="prev" title="previous trade (←)">&#x25C0;&#x25C0;</button>
    <button class="tbtn play" id="play" title="play / pause (space)">&#x25B6; Play</button>
    <button class="tbtn" id="next" title="next trade (→)">&#x25B6;&#x25B6;</button>
    <span class="tag">speed</span>
    <select class="dsel" id="spd" aria-label="replay speed">{speeds}</select>
    <div class="sep"></div>
    <div class="clock"><span id="clockv">—</span><small id="tradelbl">ready</small></div>
    <input class="scrub" id="scrub" type="range" min="0" value="0" step="1"
           aria-label="scrub through the year">
    <div class="sep"></div>
    <span class="tag">candles</span><div class="grp">{tfs}</div>
  </div>

  <div class="chartbox grow">
    <div class="chead">
      <div class="ctitle">NIFTY &middot; continuous</div>
      <div class="lg">
        <span><i style="background:var(--crit)"></i>sold leg</span>
        <span><i style="background:var(--good)"></i>bought leg</span>
        <span><i class="vline" style="color:var(--warn)"></i>expiry 15:30</span>
        <span><i style="background:var(--accent)"></i>entry &amp; exit</span>
      </div>
    </div>
    <div id="spot"></div>
  </div>

  <div class="two">
    <div class="chartbox">
      <div class="chead">
        <div class="ctitle">Combined premium</div>
        <div class="lg"><span>this trade &middot; points to close, all
          {n_legs} legs &middot; hover for the P&amp;L at that moment</span></div>
      </div>
      <div id="prem"></div>
    </div>
    <div class="chartbox">
      <div class="chead">
        <div class="ctitle">Payoff</div>
        <div class="lg">
          <span><i class="dash" style="color:var(--accent)"></i>now</span>
          <span><i style="background:var(--ink)"></i>expiry</span>
          <span><i style="background:var(--warn)"></i>breakeven</span>
        </div>
      </div>
      <div class="chint" id="ivlbl" style="margin:-2px 0 6px">&nbsp;</div>
      <div id="payoffhost"></div>
    </div>
  </div>
</main>
</div>

<div class="read hid" id="spotread"></div>
<div class="read hid" id="premread"></div>
<div class="read hid" id="payread"></div>

<section class="dock" id="dock">
  <div class="dockgrip" id="dockgrip" title="drag to resize"></div>
  <div class="dockbar">
    <button class="dtab on" data-pane="closed">Closed trades
      <span class="cnt" id="cnt-closed">0</span></button>
    <button class="dtab" data-pane="open">Open position</button>
    <button class="dtab" data-pane="about">What this page releases</button>
    <div class="dockspace"></div>
    <button class="dbtn" id="dockmax" title="expand / restore">&#x2195;</button>
    <button class="dbtn" id="dockmin" title="collapse">&#x25BC;</button>
  </div>
  <div class="dockbody">
    <div class="dockpane on" id="pane-closed">
      <table>
        <thead><tr><th class="n">#</th><th>Entry</th><th>Exit</th><th>Why</th>
          <th class="n">Lots</th><th class="n">Points</th><th class="n">P&amp;L</th>
          <th class="n">Equity</th></tr></thead>
        <tbody id="hist"></tbody>
      </table>
    </div>
    <div class="dockpane" id="pane-open"><div id="dockopen"></div></div>
    <div class="dockpane" id="pane-about">
      <div class="pad">
        <p><strong>{spot.get("n", 0):,} index bars</strong> at {base_tf}-minute base
        resolution, one continuous series; coarser candles are aggregated in the browser,
        so changing the timeframe re-fetches nothing.</p>
        <p>Every series on this page is either the index &mdash; which is not the asset and
        is published in full &mdash; or a signed sum across {n_legs} legs of a position,
        which no amount of reconstructs a single contract from.{side_note} The payoff at
        expiry is drawn from the strikes and the net credit alone; the &ldquo;now&rdquo;
        curve is priced off one volatility solved from the position&rsquo;s own combined
        premium, and is labelled modelled because it is.</p>
        <p><strong>Not investment advice.</strong> This is a replay of a backtest, not a
        recording of a live account. Position sizes are a model: real margin is set by the
        exchange and your broker and moves intraday. A position&rsquo;s terminal value is
        its settlement against the index, which is why the last step of a trade can differ
        from the last price its options printed.</p>
        {design.MARK_HTML}
      </div>
    </div>
  </div>
</section>"""

    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Stratify · {_e(name)} · replay</title>'
            f'<style>{CSS}</style></head><body>{body}'
            f'<script>{LWC}</script>'
            f'<script>{design.CHART_JS}</script>'
            # S and T are the shared SVG helpers. The replay's state object is ST rather
            # than S precisely so nothing shadows them, but the aliases stay because the
            # helper names are single letters and a future edit could reintroduce the clash
            # without noticing.
            f'<script>var S_=S,T_=T;var MONOFONT={json.dumps(design.MONO)};</script>'
            f'<script>window.__REPLAY__={json.dumps(data, separators=(",", ":"), default=str)};</script>'
            f'<script>{JS}</script></body></html>')
