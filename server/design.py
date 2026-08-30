"""The Stratify design system. ONE definition, used by every surface.

The report a Claude user publishes as an Artifact, the page at /r/{token}, and the internal
catalogue at ops/visuals all import from here. They looked alike before this file existed,
which is a different and much worse thing: two stylesheets that agree today and drift the
moment either is edited.

CONSTRAINTS EVERY SURFACE INHERITS, because the strictest consumer is an artifact sandbox:
  - no external requests. No font host, no CDN, no image URL. System stack only.
  - one file. Tokens, chart classes and chart primitives are all strings, inlined at build.
  - theme-aware in THREE states: explicit light, explicit dark, and the unstamped default
    where only prefers-color-scheme separates them. Every colour is a token defined on
    bare :root, so nothing has its only definition inside a media or [data-theme] block.
"""

FONT = ('-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,'
        'sans-serif')
MONO = 'ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace'

# The palette. Deep teal against a warm off-white paper, semantic green/amber/red kept
# separate from the accent so "good" never reads as "branded".
_LIGHT = """--bg:#F7F8F6;--surface:#FFF;--raised:#FBFCFA;--sunk:#F0F2EF;--ink:#171A18;
--body:#2C312E;--muted:#5D6560;--faint:#8A928C;--line:#E2E6E1;--soft:#EDF0EC;
--accent:#1B5E57;--accent-soft:#E4EEEB;--accent-ink:#144840;--good:#1F6B47;
--good-soft:#E6F1EA;--warn:#8A6118;--warn-soft:#F6EEDD;--crit:#9E3327;--crit-soft:#F8E7E4;
--shadow:0 1px 2px rgba(23,26,24,.05),0 8px 24px -14px rgba(23,26,24,.16)"""

_DARK = """--bg:#121412;--surface:#191C1A;--raised:#1E221F;--sunk:#141715;--ink:#E9ECE8;
--body:#CDD3CE;--muted:#98A09A;--faint:#767E78;--line:#2A2E2B;--soft:#232725;
--accent:#5FB3A6;--accent-soft:#17302C;--accent-ink:#8FCFC4;--good:#5AB483;
--good-soft:#17281F;--warn:#CDA24F;--warn-soft:#2A2417;--crit:#E08376;--crit-soft:#2E1C1A;
--shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px -14px rgba(0,0,0,.6)"""

TOKENS = (f":root{{{_LIGHT}}}\n"
          f"@media(prefers-color-scheme:dark){{:root:not([data-theme=light]){{{_DARK}}}}}\n"
          f":root[data-theme=dark]{{{_DARK}}}\n")

# Chart primitives. Every SVG on every surface draws with these class names, so a change to
# the line weight or the loss colour lands everywhere at once.
CHART_CSS = """
svg text{font:400 10.5px/1 MONOSTACK}
.tk{fill:var(--faint);font-size:10px}
.cap{fill:var(--muted);font-size:10.5px}
.cl{fill:var(--surface);font-size:9.5px;font-weight:600}
.zero{stroke:var(--faint);opacity:.5;stroke-width:1}
.dash{stroke:var(--faint);stroke-dasharray:3 3;opacity:.7;stroke-width:1}
.ln{stroke:var(--accent);stroke-width:1.8;fill:none;stroke-linejoin:round}
.ln-b{stroke:var(--crit);stroke-width:1.4;fill:none}
.ln-w{stroke:var(--warn);stroke-width:1.6;fill:none}
.ar{fill:var(--accent);opacity:.11}
.ar-b{fill:var(--crit);opacity:.13}
.ar-w{fill:var(--warn);opacity:.13}
.bg{fill:var(--good);opacity:.82}
.bb{fill:var(--crit);opacity:.82}
.ba{fill:var(--accent);opacity:.88}
.bm{fill:var(--faint);opacity:.55}
.dg{fill:var(--good)}.db{fill:var(--crit)}.da{fill:var(--accent)}
.lg{fill:var(--good);font-size:10px;font-weight:600}
.lb{fill:var(--crit);font-size:10px;font-weight:600}
.la{fill:var(--accent);font-size:10px;font-weight:600}
.lm{fill:var(--muted);font-size:10px}
.band-g{fill:var(--good);opacity:.09}
.band-b{fill:var(--crit);opacity:.07}
.band-a{fill:var(--accent);opacity:.08}
.pt-g{fill:var(--good);opacity:.5}
.pt-b{fill:var(--crit);opacity:.55}
""".replace("MONOSTACK", MONO)

# The SVG helpers every renderer is built on. Shared for the same reason as the classes:
# so a chart drawn for the catalogue and a chart drawn for a customer's report are the
# same code path, not two implementations that happen to agree.
CHART_JS = r"""
var NS='http://www.w3.org/2000/svg';
function E(n,a,p){var x=document.createElementNS(NS,n);for(var k in a)if(a[k]!=null)
 x.setAttribute(k,a[k]);if(p)p.appendChild(x);return x}
function S(h,w,ht){h.innerHTML='';return E('svg',{viewBox:'0 0 '+w+' '+ht,width:'100%',
 preserveAspectRatio:'xMidYMid meet',role:'img'},h)}
function P(d,a,p){return E('path',Object.assign({d:d,fill:'none'},a),p)}
function T(s,x,y,c,p,an){var t=E('text',{x:x,y:y,class:c,'text-anchor':an||'start'},p);
 t.textContent=s;return t}
function sc(a,b,c,d){var s=(b-a)||1;return function(v){return c+(v-a)/s*(d-c)}}
function M(v){if(v==null)return'—';var a=Math.abs(Math.round(v));
 var s=a>=1e7?'₹'+(a/1e7).toFixed(2)+'Cr':a>=1e5?'₹'+(a/1e5).toFixed(2)+'L'
 :a>=1000?'₹'+(a/1000).toFixed(1)+'k':'₹'+a;return(v<0?'−':'')+s}
function line(vals,x,y){return vals.map(function(v,i){
 return(i?'L':'M')+x(i).toFixed(1)+' '+y(v).toFixed(1)}).join('')}
"""

# The mark. Small, in the footer, on every document that leaves the building -- the report
# is the artefact users forward to other people, and it should say where it came from.
MARK_CSS = """
.mark{display:flex;align-items:center;gap:9px;margin-top:18px;padding-top:14px;
border-top:1px solid var(--line)}
.mark .glyph{width:19px;height:19px;flex:none}
.mark .txt{font:600 10.5px/1.3 MONOSTACK;letter-spacing:.1em;text-transform:uppercase;
color:var(--faint)}
.mark .txt b{color:var(--accent);font-weight:600}
""".replace("MONOSTACK", MONO)

# Three ascending bars in the accent colour -- drawn, not fetched, because an <img> would
# be blocked by the artifact sandbox and a data: URI would be larger than the shape.
MARK_HTML = """<div class="mark">
<svg class="glyph" viewBox="0 0 20 20" aria-hidden="true">
<rect x="1" y="12" width="4.6" height="7" rx="1.2" fill="var(--accent)" opacity=".45"></rect>
<rect x="7.7" y="7" width="4.6" height="12" rx="1.2" fill="var(--accent)" opacity=".72"></rect>
<rect x="14.4" y="1" width="4.6" height="18" rx="1.2" fill="var(--accent)"></rect>
</svg>
<span class="txt">Made with <b>Stratify MCP</b> &middot; real 1-minute NIFTY options data</span>
</div>"""
