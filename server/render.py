"""HTML for the website. THIS IS THE FILE THE DESIGN LIVES IN.

Every tag, every class and every byte of CSS on this site is in this module. Nothing else
imports it and it imports nothing but `html` -- so a new visual design is a rewrite of
this file and touches no logic, no auth, no data access and no test that asserts on what a
page is allowed to say.

THE CONTRACT. `site.py` hands each function a plain dict. The dicts are the API between
"what is true" and "what it looks like", and they are documented by the functions in
site.py that build them. A redesign may restructure any markup here freely; what it must
not do is start deciding what data to fetch or who may see it, because that is the half
that has guards in it.

TWO RULES THAT SURVIVE ANY REDESIGN, because they are correctness rather than taste:
  * `esc()` on every value that came from a person or from Google. The account name and
    the strategy name are attacker-controlled strings.
  * A secret key is rendered exactly once, on the response that mints it. If a redesign
    puts it anywhere it can be re-fetched, the "shown once" promise is gone.

Deliberately plain for now: semantic elements, one stylesheet, no framework, no build step
and no client-side state. The structure is meant to be re-skinned, so the class names say
what a thing IS rather than what it looks like.
"""
import html
import json

# --------------------------------------------------------------------------- style
# One stylesheet, one place. Replace everything between here and END STYLE.
STYLE = """
/* THE REPORT'S DESIGN, ON THE SITE. One product, one look: the same ground, the same
   serif, the same four brand surfaces that number the report's sections number these
   pages' steps. Committed to dark on purpose -- the report is dark-only by design and a
   site that flips to cream would make the two feel like different companies. */
:root{--bg:#131313;--surface:#1C1C1C;--surface-2:#2B2B2B;--fg:#FFFFFF;
  --fg-muted:#BABABA;--fg-subtle:#898989;--stroke:#424242;--line:var(--stroke);
  --muted:var(--fg-muted);
  --brand-green:#8DDD8D;--brand-yellow:#E0E055;--brand-blue:#6066EE;--brand-pink:#FAAAFA;
  --good:#0AE448;--bad:#FF5470;--warn:var(--brand-yellow);--accent:var(--brand-green);
  --font-display:"Instrument Serif",Georgia,"Times New Roman",serif;
  --font-ui:"Spline Sans",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  --font-mono:"Spline Sans Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
  --radius:20px;--radius-sm:12px;--pill:9999px;--gap:16px;--measure:62ch;
  --ease:cubic-bezier(.23,1,.32,1);
  --elev:0 1px 0 0 rgba(255,255,255,.04),0 12px 32px -16px rgba(0,0,0,.7)}
*{box-sizing:border-box}
html{color-scheme:dark;background:var(--bg)}
body{margin:0;background:var(--bg);color:var(--fg);font-family:var(--font-ui);
  font-size:16px;line-height:1.6;-webkit-font-smoothing:antialiased;
  text-rendering:optimizeLegibility}
::selection{background:var(--brand-green);color:#131313}
a{color:var(--brand-green);text-decoration:none}
a:hover{text-decoration:underline;text-underline-offset:3px}
code,pre{font-family:var(--font-mono);font-size:.86rem}
pre{background:#0D0D0D;border:1px solid var(--stroke);border-radius:var(--radius-sm);
  padding:14px 16px;overflow-x:auto;margin:0;line-height:1.55;color:var(--fg-muted);
  overscroll-behavior-x:contain}
h1,h2,h3{font-weight:400;margin:0;text-wrap:balance;letter-spacing:-.015em}
h1{font-family:var(--font-display);font-size:clamp(2.4rem,5.5vw,4.2rem);line-height:1.02}
h2{font-family:var(--font-display);font-size:clamp(1.7rem,3.2vw,2.5rem);line-height:1.1;
  margin:56px 0 18px}
h3{font-family:var(--font-ui);font-size:1.05rem;font-weight:500;margin:0 0 6px;
  letter-spacing:0}
p{margin:0 0 12px;max-width:var(--measure)}
.muted{color:var(--fg-muted)} .subtle{color:var(--fg-subtle)}
.small{font-size:.9rem}
.mono{font-family:var(--font-mono)}
.eyebrow{font-family:var(--font-mono);font-size:.7rem;letter-spacing:.12em;
  text-transform:uppercase;color:var(--brand-green);margin:0 0 16px}
.wrap{max-width:1100px;margin:0 auto;padding:32px 24px 96px}
.wrap.wide{max-width:1240px}
.narrow{max-width:640px}

/* ---- chrome --------------------------------------------------------------------- */
header.top{position:sticky;top:0;z-index:20;background:rgba(19,19,19,.86);
  backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);
  border-bottom:1px solid var(--stroke)}
header.top .inner{max-width:1240px;margin:0 auto;padding:12px 24px;display:flex;
  align-items:center;gap:22px;flex-wrap:wrap}
.brand{font-family:var(--font-display);font-size:1.35rem;color:var(--fg);letter-spacing:0;display:inline-flex;align-items:center;gap:9px}
.brand .mark{width:28px;height:28px;display:block}
.brand:hover{text-decoration:none}
nav.main{display:flex;gap:4px;flex-wrap:wrap}
nav.main a{font-family:var(--font-mono);font-size:.74rem;letter-spacing:.04em;
  color:var(--fg-muted);padding:6px 12px;border-radius:var(--pill);
  border:1px solid transparent;transition:all .25s var(--ease)}
nav.main a:hover{color:var(--fg);text-decoration:none;border-color:var(--stroke)}
nav.main a.on{color:#131313;background:var(--brand-green);border-color:var(--brand-green)}
.spacer{flex:1}
/* On a phone the header was 230px tall: brand, three links and the button each took a
   row. Brand and the button stay on the first row; the links wrap to one line beneath
   and scroll sideways if they must, so the page's own content starts within a thumb. */
@media(max-width:640px){
  header.top .inner{gap:10px 14px;padding:10px 16px}
  .brand{order:0}
  header.top .spacer{order:1}
  header.top > .inner > span.small{display:none}
  header.top .btn{order:2}
  nav.main{order:3;flex-basis:100%;flex-wrap:nowrap;overflow-x:auto;gap:2px;
    -webkit-overflow-scrolling:touch;scrollbar-width:none;margin:0 -16px;padding:0 16px}
  nav.main::-webkit-scrollbar{display:none}
  nav.main a{white-space:nowrap;font-size:.72rem;padding:5px 11px}
  .wrap{padding:20px 16px 72px}
  .hero{padding:36px 0 28px}
  h2{margin-top:44px}
  .card{padding:18px 18px}
  .step{padding:22px 20px 24px;min-height:0}
}
footer.site{border-top:1px solid var(--stroke);margin-top:72px}
footer.site .inner{max-width:1240px;margin:0 auto;padding:22px 24px;font-size:.84rem;
  color:var(--fg-subtle);display:flex;gap:18px;flex-wrap:wrap;align-items:center}
footer.site a{color:var(--fg-muted)}

/* ---- controls ------------------------------------------------------------------- */
.btn{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--stroke);
  background:transparent;color:var(--fg);border-radius:var(--pill);padding:11px 20px;
  font:500 .95rem/1 var(--font-ui);cursor:pointer;text-decoration:none;
  transition:all .25s var(--ease);min-height:44px}
.btn:hover{border-color:var(--fg-subtle);text-decoration:none;transform:translateY(-1px)}
.btn.primary{background:var(--brand-green);border-color:var(--brand-green);color:#131313}
.btn.primary:hover{background:#A4E8A4;border-color:#A4E8A4}
.btn.ghost{color:var(--fg-muted)}
.btn.danger{color:var(--bad);border-color:rgba(255,84,112,.4)}
.btn.small{padding:7px 13px;font-size:.82rem;min-height:34px}
.btn.big{padding:15px 26px;font-size:1.05rem;min-height:52px}
.btn .g{width:18px;height:18px;flex:none}
input[type=email],input[type=text],input[type=number],input[type=password],select{
  font:inherit;color:var(--fg);background:#0D0D0D;border:1px solid var(--stroke);
  border-radius:var(--pill);padding:11px 16px;min-height:44px;outline:none;
  transition:border-color .2s var(--ease)}
input:focus,select:focus{border-color:var(--brand-green)}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}

/* ---- surfaces ------------------------------------------------------------------- */
.card{background:var(--surface);border:1px solid var(--stroke);border-radius:var(--radius);
  padding:22px 24px;margin:0 0 var(--gap);box-shadow:var(--elev)}
.grid{display:grid;gap:var(--gap)}
@media(min-width:720px){.grid.two{grid-template-columns:1fr 1fr}
  .grid.three{grid-template-columns:repeat(3,1fr)}
  .grid.four{grid-template-columns:repeat(4,1fr)}}
@media(min-width:720px) and (max-width:980px){.grid.four{grid-template-columns:1fr 1fr}}
/* The four brand surfaces. Saturated colour is used as a WHOLE surface, never as a line
   -- the same rule the report follows -- so a coloured card is one of these four and
   carries dark ink, and everything else is grey. */
.q0{background:var(--brand-green);color:#131313}
.q1{background:var(--brand-yellow);color:#131313}
.q2{background:var(--brand-blue);color:#fff}
.q3{background:var(--brand-pink);color:#131313}
.q0 a,.q1 a,.q3 a{color:#131313;text-decoration:underline}
.q2 a{color:#fff;text-decoration:underline}
.q0 .btn,.q1 .btn,.q2 .btn,.q3 .btn{text-decoration:none}
.num{font-family:var(--font-mono);font-size:.78rem;letter-spacing:.08em;flex:none;
  width:44px;height:44px;border-radius:50%;display:grid;place-items:center;font-weight:500;
  box-shadow:2px 4px 8px 0 rgba(0,0,0,.45),inset -1px 2px 6px 0 rgba(255,255,255,.5)}
.num.q0{background:var(--brand-green)} .num.q1{background:var(--brand-yellow)}
.num.q2{background:var(--brand-blue);color:#fff} .num.q3{background:var(--brand-pink)}
table{width:100%;border-collapse:collapse;font-size:.9rem}
th,td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--stroke);
  vertical-align:top}
th{font-family:var(--font-mono);color:var(--fg-subtle);font-weight:500;font-size:.7rem;
  text-transform:uppercase;letter-spacing:.08em}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;width:auto;height:auto;
  border-radius:0;box-shadow:none;display:table-cell;font-size:inherit}
.scroll{overflow-x:auto;overscroll-behavior-x:contain}
.tag{display:inline-block;font-family:var(--font-mono);font-size:.7rem;letter-spacing:.06em;
  border:1px solid var(--stroke);border-radius:var(--pill);padding:3px 10px;
  color:var(--fg-muted)}
.tag.ok{color:var(--good);border-color:currentColor}
.tag.bad{color:var(--bad);border-color:currentColor}
.tag.warn{color:var(--warn);border-color:currentColor}
.note{border-left:3px solid var(--brand-green);padding:10px 0 10px 16px;margin:0 0 var(--gap);
  color:var(--fg-muted);font-size:.92rem;max-width:var(--measure)}
.note.bad{border-color:var(--bad);color:var(--bad)}
.meter{height:6px;background:var(--surface-2);border-radius:3px;overflow:hidden;margin-top:10px}
.meter i{display:block;height:100%;background:var(--brand-green)}
.meter i.full{background:var(--bad)}
.stat .k{font-family:var(--font-mono);font-size:.7rem;color:var(--fg-subtle);
  text-transform:uppercase;letter-spacing:.08em}
.stat .v{font-family:var(--font-mono);font-size:1.5rem;font-variant-numeric:tabular-nums;
  margin-top:4px;color:var(--fg)}
.secret{background:#0D0D0D;border:2px dashed var(--brand-green);border-radius:var(--radius);
  padding:18px 20px;margin:0 0 var(--gap)}
.secret code{font-size:.98rem;word-break:break-all;display:block;margin:10px 0;
  color:var(--brand-green)}
.empty{text-align:center;padding:36px 20px;color:var(--fg-subtle)}

/* ---- landing -------------------------------------------------------------------- */
.hero{padding:64px 0 40px}
.hero h1{max-width:16ch}
.hero .lede{font-size:clamp(1.05rem,1.6vw,1.25rem);color:var(--fg-muted);max-width:54ch;
  margin:22px 0 0;line-height:1.55}
.hero .cta{display:flex;gap:12px;flex-wrap:wrap;margin:30px 0 0;align-items:center}
.steps{display:grid;gap:var(--gap);margin:18px 0 0}
@media(min-width:760px){.steps{grid-template-columns:repeat(3,1fr)}}
.step{border-radius:var(--radius);padding:26px 24px 28px;position:relative;
  display:flex;flex-direction:column;gap:12px;min-height:220px;
  box-shadow:var(--elev);transition:transform .3s var(--ease)}
.step:hover{transform:translateY(-3px)}
.step .n{font-family:var(--font-mono);font-size:.74rem;letter-spacing:.1em;opacity:.7}
.step h3{font-family:var(--font-display);font-size:1.7rem;font-weight:400;margin:0;
  line-height:1.05;letter-spacing:-.01em}
.step p{margin:0;font-size:.95rem;line-height:1.5;opacity:.88;max-width:none}
.step .act{margin-top:auto;padding-top:6px}
.step .btn{border-color:rgba(0,0,0,.35);color:inherit;background:rgba(0,0,0,.12)}
.step .btn:hover{background:rgba(0,0,0,.22)}
.q2 .step .btn,.step.q2 .btn{border-color:rgba(255,255,255,.4);background:rgba(255,255,255,.12)}
.qa{display:grid;gap:var(--gap)}
@media(min-width:760px){.qa{grid-template-columns:repeat(3,1fr)}}
.qa .card h3{font-family:var(--font-display);font-size:1.4rem;margin:0 0 10px}
.qa .card p{font-size:.95rem;color:var(--fg-muted);margin:0;max-width:none}
.exchange{display:grid;gap:var(--gap)}
@media(min-width:860px){.exchange{grid-template-columns:5fr 6fr}}
.bubble{background:var(--surface-2);border-radius:var(--radius);padding:18px 22px;
  font-size:1.02rem;line-height:1.55;position:relative}
.bubble .who{font-family:var(--font-mono);font-size:.68rem;letter-spacing:.1em;
  text-transform:uppercase;color:var(--fg-subtle);margin:0 0 8px}
.gbtn{background:#fff;color:#1f1f1f;border-color:#fff}
.err{color:var(--brand-pink)}
.reach{display:grid;grid-template-columns:max-content 1fr;gap:8px 18px;margin:12px 0 0;
  font-variant-numeric:tabular-nums}
.reach dt{color:var(--fg-muted);font-size:.9rem}
.reach dd{margin:0}
.pwd{margin-top:28px;border-top:1px solid var(--stroke);padding-top:16px}
.pwd summary{cursor:pointer;color:var(--fg-muted);font-size:.95rem;list-style:none}
.pwd summary::-webkit-details-marker{display:none}
.pwd summary::before{content:"+ ";font-family:var(--font-mono)}
.pwd[open] summary::before{content:"– "}
.stack{display:flex;flex-direction:column;gap:12px;max-width:380px;margin-top:12px}
.stack label{display:flex;flex-direction:column;gap:6px;font-size:.9rem;color:var(--fg-muted)}
.stack input{width:100%}
.gbtn:hover{background:#f1f1f1;border-color:#f1f1f1}

/* ---- explore -------------------------------------------------------------------- */
.sh{display:flex;align-items:center;gap:18px;margin:56px 0 8px}
.sh h2{margin:0}
.sh + .sub{color:var(--fg-muted);margin:0 0 20px 62px;max-width:60ch}
.feat{display:grid;gap:var(--gap)}
@media(min-width:720px){.feat{grid-template-columns:1fr 1fr}}
.feat .card{margin:0}
.feat .card p{color:var(--fg-muted);font-size:.95rem;margin:0;max-width:none}
.clients{display:grid;gap:12px;margin-top:8px}
@media(min-width:720px){.clients{grid-template-columns:repeat(4,1fr)}}
.client{border:1px solid var(--stroke);border-radius:var(--radius-sm);padding:14px 16px}
.client b{display:block;font-weight:500}
.client span{color:var(--fg-subtle);font-size:.86rem}

/* ---- pricing -------------------------------------------------------------------- */
.plans{display:grid;gap:var(--gap);margin-top:24px;align-items:stretch}
@media(min-width:800px){.plans{grid-template-columns:1fr 1fr}}
.plan{display:flex;flex-direction:column;gap:14px;margin:0}
.plan .price{font-family:var(--font-display);font-size:2.6rem;line-height:1;margin:6px 0 0}
.plan .sub{color:var(--fg-muted);font-size:.95rem;margin:0}
.plan ul{list-style:none;padding:0;margin:8px 0 0;display:grid;gap:10px}
.plan li{padding-left:26px;position:relative;font-size:.97rem;line-height:1.45}
.plan li::before{content:"";position:absolute;left:0;top:.5em;width:12px;height:12px;
  border-radius:50%;background:var(--brand-green)}
.plan.soon li::before{background:var(--brand-pink)}
.plan li b{display:block;font-weight:500}
.plan li span{color:var(--fg-muted);font-size:.9rem}
.plan .act{margin-top:auto;padding-top:10px}
.waitform{display:flex;gap:10px;flex-wrap:wrap}
.waitform input{flex:1;min-width:200px}

/* ---- dashboard ------------------------------------------------------------------ */
.hello{display:flex;align-items:baseline;gap:16px;flex-wrap:wrap;margin:8px 0 26px}
.hello h1{font-size:clamp(2rem,4vw,3rem)}
.keycta{padding:30px 28px;display:flex;flex-direction:column;gap:14px}
.keycta h2{margin:0;font-size:clamp(1.6rem,3vw,2.2rem)}
.keycta p{opacity:.85;max-width:none;margin:0}
.snips{display:grid;gap:14px}
.snips details{border:1px solid var(--stroke);border-radius:var(--radius-sm);padding:0}
.snips summary{cursor:pointer;padding:12px 16px;font-weight:500;list-style:none;
  display:flex;align-items:center;gap:10px}
.snips summary::-webkit-details-marker{display:none}
.snips summary::after{content:"+";margin-left:auto;font-family:var(--font-mono);
  color:var(--fg-subtle)}
.snips details[open] summary::after{content:"–"}
.snips pre{border:0;border-top:1px solid var(--stroke);border-radius:0 0 var(--radius-sm) var(--radius-sm)}
.toc{position:sticky;top:76px}
.toc a{display:block;padding:4px 0;font-size:.88rem;color:var(--fg-muted)}
@media(min-width:900px){.docs{display:grid;grid-template-columns:200px 1fr;gap:36px}}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""
# END STYLE


# On EVERY page, not only the landing one. A backtest is a description of the past, and a
# reader who arrives straight on a report link deserves to be told that as plainly as one
# who came through the front door.
DISCLAIMER = ("Historical simulation on real 1-minute data. Not investment advice, and "
              "past results are not a forecast.")


def esc(v):
    return html.escape("" if v is None else str(v), quote=True)


def _json(obj):
    return esc(json.dumps(obj, indent=2))


# The top-level pages a stranger can reach. Shown on every page so nobody is ever more
# than one click from "what is this" or "what does it cost". The dashboard's own nav
# (NAV in site.py) is shown INSTEAD once signed in -- two navs stacked is a mess.
SITE_NAV = [
    {"key": "explore", "label": "Explore", "href": "/explore"},
    {"key": "pricing", "label": "Pricing", "href": "/pricing"},
    {"key": "docs", "label": "Docs", "href": "/docs"},
]

GOOGLE_G = ('<svg class="g" viewBox="0 0 24 24" aria-hidden="true"><path fill="#4285F4" '
            'd="M23.5 12.3c0-.8-.1-1.6-.2-2.3H12v4.5h6.5c-.3 1.5-1.1 2.8-2.4 3.6v3h3.9c2.3-2.1 '
            '3.5-5.2 3.5-8.8z"/><path fill="#34A853" d="M12 24c3.2 0 6-1.1 8-2.9l-3.9-3c-1.1.7'
            '-2.5 1.2-4.1 1.2-3.1 0-5.8-2.1-6.7-5H1.2v3.1C3.2 21.3 7.3 24 12 24z"/><path '
            'fill="#FBBC05" d="M5.3 14.3c-.2-.7-.4-1.5-.4-2.3s.1-1.6.4-2.3V6.6H1.2C.4 8.2 0 '
            '10 0 12s.4 3.8 1.2 5.4l4.1-3.1z"/><path fill="#EA4335" d="M12 4.8c1.8 0 3.3.6 '
            '4.6 1.8l3.4-3.4C18 1.2 15.2 0 12 0 7.3 0 3.2 2.7 1.2 6.6l4.1 3.1c.9-2.9 3.6-4.9 '
            '6.7-4.9z"/></svg>')


def shell(title, body, nav=None, you=None, active=None, wide=False):
    """The page frame. Every page on the site goes through here."""
    links = "".join(
        f'<a href="{esc(n["href"])}" class="{"on" if n["key"] == active else ""}">'
        f'{esc(n["label"])}</a>' for n in (nav or SITE_NAV))
    right = ('<a class="btn small" href="/logout">Sign out</a>' if you else
             f'<a class="btn small primary" href="/auth/google?next=/app">Sign in</a>')
    who = (f'<span class="small subtle">{esc(you["email"])}</span>' if you else "")
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<title>{esc(title)}</title>
<link rel="icon" href="/favicon.ico" sizes="32x32"><link rel="icon" href="/static/icon-192.png" type="image/png" sizes="192x192"><link rel="apple-touch-icon" href="/static/icon-192.png">
<link rel="stylesheet" href="/static/fonts.css">
<style>{STYLE}</style></head><body>
<header class="top"><div class="inner">
<a class="brand" href="/"><img class="mark" src="/static/icon-64.png" width="28" height="28" alt="">Stratify</a>
<nav class="main">{links}</nav>
<span class="spacer"></span>{who}{right}
</div></header>
<main class="wrap{' wide' if wide else ''}">{body}</main>
<footer class="site"><div class="inner">
<a href="/explore">Explore</a><a href="/pricing">Pricing</a><a href="/docs">Docs</a>
<a href="/app">Dashboard</a><a href="/privacy">Privacy</a>
<a href="/terms">Terms</a><a href="/contact">Contact</a>
<span class="spacer"></span><span>{esc(DISCLAIMER)}</span>
</div></footer></body></html>"""


# ------------------------------------------------------------------------- landing

def onetap_widget(cfg):
    """Google One Tap: the small "Continue as ..." prompt.

    KEEP THIS WHEN YOU RESKIN THE PAGE. It looks decorative and is not -- deleting it
    removes a sign-in path, and the attributes are a contract with Google's script, not
    styling. Google renders and positions the prompt itself; nothing here is themable.

    `data-login_uri` makes Google POST the credential straight to our endpoint as a form,
    with a matching CSRF cookie it sets itself. That is Google's documented double-submit
    pattern and it needs no JavaScript of ours at all -- so there is no callback to get
    wrong, and the page keeps working with scripts blocked.

    FedCM is switched on because Chrome now requires it for One Tap; without it the prompt
    silently never appears in the browser most people use.
    """
    if not cfg:
        return ""
    return (f'<script src="{esc(cfg["script"])}" async defer></script>'
            f'<div id="g_id_onload"'
            f' data-client_id="{esc(cfg["client_id"])}"'
            f' data-login_uri="{esc(cfg["login_uri"])}"'
            f' data-use_fedcm_for_prompt="true"'
            f' data-cancel_on_tap_outside="false"'
            f' data-context="signin"></div>')


def landing(v):
    """The front door, written for somebody whose only software is a chat box.

    The three steps ARE the hero. There is no feature wall above them: a first-time
    visitor wants to know what to do, not what it is made of, and the one thing this page
    must never do is make them scroll to find the button.
    """
    h = v["hero"]
    err = f'<div class="note bad">{esc(v["error"])}</div>' if v.get("error") else ""
    if v["signed_in"]:
        primary = f'<a class="btn primary big" href="/app">Open your dashboard</a>'
    elif v["google_ready"]:
        primary = (f'<a class="btn big gbtn" href="/auth/google?next=/app">{GOOGLE_G}'
                   f'Start free with Google</a>')
    else:
        primary = '<span class="tag warn">Google sign-in is not configured on this server</span>'

    steps = ""
    for st in v["steps"]:
        act = ""
        if st["n"] == 1 and not v["signed_in"] and v["google_ready"]:
            act = (f'<div class="act"><a class="btn" href="/auth/google?next=/app">'
                   f'Sign in →</a></div>')
        elif st["n"] == 1 and v["signed_in"]:
            act = '<div class="act"><span class="tag">Done ✓</span></div>'
        elif st["n"] == 2:
            act = '<div class="act"><a class="btn" href="/app">Get my key →</a></div>'
        elif st["n"] == 3:
            act = '<div class="act"><a class="btn" href="/explore">See what it can do →</a></div>'
        steps += (f'<div class="step q{st["q"]}"><div class="n">STEP {st["n"]}</div>'
                  f'<h3>{esc(st["title"])}</h3><p>{esc(st["body"])}</p>{act}</div>')

    qa = "".join(f'<div class="card"><h3>{esc(q)}</h3><p>{esc(a)}</p></div>'
                 for q, a in v["what"])
    ft = v["free_tier"]
    body = f"""
{onetap_widget(v.get("onetap"))}
{err}
<section class="hero">
  <p class="eyebrow">{esc(h["eyebrow"])}</p>
  <h1>{esc(h["title"])}</h1>
  <p class="lede">{esc(h["lede"])}</p>
  <div class="cta">{primary}
    <a class="btn ghost" href="/explore">What can it do?</a>
  </div>
</section>

<section class="steps">{steps}</section>

<h2>Three questions, answered</h2>
<section class="qa">{qa}</section>

<h2>What a conversation looks like</h2>
<section class="exchange">
  <div class="bubble"><p class="who">You, in Claude or ChatGPT</p>
    {esc(h["example"]["ask"])}</div>
  <div><p class="who mono subtle" style="font-size:.68rem;letter-spacing:.1em;
    text-transform:uppercase;margin:0 0 8px">What Stratify runs, on real prices</p>
    <pre>{_json(h["example"]["spec"])}</pre></div>
</section>
<p class="small subtle" style="margin-top:14px">You never write the right-hand side.
The chat app does — that is the whole point. The result comes back as numbers and a
report link.</p>

<h2>Free means free</h2>
<div class="card"><div class="grid three">
  <div class="stat"><div class="k">History</div><div class="v">{esc(ft["window"])}</div></div>
  <div class="stat"><div class="k">Rate</div><div class="v">{esc(ft["requests"])}</div></div>
  <div class="stat"><div class="k">Compute</div><div class="v">{esc(ft["cpu"])}</div></div>
</div><p class="small muted" style="margin-top:16px">{esc(ft["note"])}
<a href="/pricing">See what's coming →</a></p></div>
"""
    return shell("Stratify — backtest any NIFTY options strategy", body, active="home")


# ------------------------------------------------------------------------- explore

def explore(v):
    e = v["explore"]
    groups = ""
    for g in e["groups"]:
        cards = "".join(f'<div class="card"><h3>{esc(t)}</h3><p>{esc(b)}</p></div>'
                        for t, b in g["items"])
        groups += (f'<div class="sh" id="{esc(g["key"])}"><div class="num q{g["q"]}">'
                   f'0{g["q"] + 1}</div><h2>{esc(g["title"])}</h2></div>'
                   f'<p class="sub">{esc(g["sub"])}</p><div class="feat">{cards}</div>')
    clients = "".join(f'<div class="client"><b>{esc(n)}</b><span>{esc(d)}</span></div>'
                      for n, d in e["clients"])
    body = f"""
<section class="hero" style="padding-bottom:8px">
  <p class="eyebrow">What you can do</p>
  <h1>{esc(e["title"])}</h1>
  <p class="lede">{esc(e["lede"])}</p>
</section>
{groups}
<h2>Where it works</h2>
<p class="muted">The same server, in whichever assistant you already use.</p>
<div class="clients">{clients}</div>
<div class="card q0" style="margin-top:48px;display:flex;gap:18px;align-items:center;
  flex-wrap:wrap;justify-content:space-between">
  <div><h3 style="font-family:var(--font-display);font-size:1.6rem;margin:0">Free, today.</h3>
  <p style="margin:4px 0 0;opacity:.85;max-width:none">A year of NIFTY, every feature above, no card.</p></div>
  <a class="btn" href="{esc(v["cta_href"])}">{esc(v["cta"])} →</a>
</div>
"""
    return shell("Explore — Stratify", body, active="explore")


# ------------------------------------------------------------------------- pricing

def pricing(v):
    f, pm = v["free"], v["premium"]
    msg = ""
    if v.get("message"):
        msg = f'<div class="note{"" if v.get("joined") else " bad"}">{esc(v["message"])}</div>'
    free_items = "".join(f"<li>{esc(i)}</li>" for i in f["items"])
    prem_items = "".join(f"<li><b>{esc(t)}</b><span>{esc(d)}</span></li>"
                         for t, d in pm["items"])

    # THE WAITLIST FORM. Signed in: one click, their address, nothing to type. Signed out:
    # an email field. Already on it: say so and show no button, because a button that
    # does nothing the second time is a bug people report.
    if v["on_list"]:
        wait = ('<div class="act"><span class="tag ok">You\'re on the list</span> '
                '<span class="small muted">We\'ll write when it exists.</span></div>')
    elif v["signed_in"]:
        wait = (f'<form class="act" method="post" action="/waitlist">'
                f'<button class="btn primary" type="submit">Join the waitlist as '
                f'{esc(v["email"])}</button></form>')
    else:
        wait = ('<form class="act waitform" method="post" action="/waitlist">'
                '<input type="email" name="email" placeholder="you@example.com" '
                'required autocomplete="email" aria-label="Email address">'
                '<button class="btn primary" type="submit">Join the waitlist</button>'
                '</form>')

    body = f"""
{msg}
<section class="hero" style="padding-bottom:8px">
  <p class="eyebrow">Pricing</p>
  <h1>Free now. More data later.</h1>
  <p class="lede">The free tier is the whole product on one year of NIFTY. Premium is the
  same product on ten years of everything — and it does not have a price yet, so the
  only honest button is the one that tells you when it does.</p>
</section>
<section class="plans">
  <div class="card plan">
    <p class="eyebrow" style="margin:0">{esc(f["name"])}</p>
    <div class="price">{esc(f["price"])}</div>
    <p class="sub">{esc(f["sub"])}</p>
    <ul>{free_items}</ul>
    <div class="act"><a class="btn primary" href="{esc(f["cta_href"])}">{esc(f["cta"])}</a></div>
  </div>
  <div class="card plan soon">
    <p class="eyebrow" style="margin:0;color:var(--brand-pink)">{esc(pm["name"])}</p>
    <div class="price">{esc(pm["price"])}</div>
    <p class="sub">{esc(pm["sub"])}</p>
    <ul>{prem_items}</ul>
    {wait}
  </div>
</section>
<p class="small subtle" style="margin-top:22px">Joining the list is not a purchase and
not a commitment. It is one email, once, when Premium is ready.</p>
"""
    return shell("Pricing — Stratify", body, active="pricing")


# ----------------------------------------------------------------------- dashboard
# ----------------------------------------------------------------------- dashboard

def _meters(usage):
    out = []
    for m in usage["meters"]:
        limit = m["limit"] or 1
        pct = min(100, round(float(m["used"]) / float(limit) * 100))
        out.append(
            f'<div class="card stat"><div class="k">{esc(m["label"])}</div>'
            f'<div class="v">{esc(m["used"])} <span class="small muted">/ '
            f'{esc(m["limit"])}</span></div>'
            f'<div class="meter"><i class="{"full" if pct >= 100 else ""}" '
            f'style="width:{pct}%"></i></div></div>')
    return "".join(out)


def _snippets(connect):
    """Collapsed by default, one open. Six code blocks stacked is a wall; a person has
    exactly one client and wants exactly one block."""
    out = []
    for i, c in enumerate(connect):
        out.append(f'<details{" open" if i == 0 else ""}><summary>{esc(c["label"])}'
                   f'</summary><pre>{esc(c["body"])}</pre></details>')
    return f'<div class="snips">{"".join(out)}</div>'


def overview(v):
    """The signed-in home. One job: get the key into the chat app.

    With no key, the page is the button. With a key, the page is the one thing they
    paste. Everything else -- usage, activity, reports -- is below the fold on purpose,
    because none of it matters until the first backtest has run.
    """
    msg = f'<div class="note">{esc(v["message"])}</div>' if v.get("message") else ""
    if not v["has_key"]:
        first = (f'<div class="card keycta q1">'
                 f'<p class="eyebrow" style="color:#131313;opacity:.7;margin:0">Step 2 of 3</p>'
                 f'<h2>Make your key</h2>'
                 f'<p>One click. It is shown once, so copy it straight into your chat '
                 f'app. If you lose it, make another — the old one just stops working.</p>'
                 f'<form method="post" action="/app/keys/new">'
                 f'<button class="btn big" type="submit" style="background:#131313;'
                 f'color:#fff;border-color:#131313">Generate my key →</button>'
                 f'</form></div>')
    else:
        first = (f'<div class="card keycta q0">'
                 f'<p class="eyebrow" style="color:#131313;opacity:.7;margin:0">Step 3 of 3</p>'
                 f'<h2>Paste it into your chat app</h2>'
                 f'<p>You have {esc(v["key_count"])} active key(s). Pick your app below, '
                 f'copy the block, and put your key where it says <code>sk_live_...</code>. '
                 f'Then just describe a trade.</p>'
                 f'<div class="row"><a class="btn small" href="/app/keys">Manage keys</a>'
                 f'<a class="btn small" href="/explore">What can I ask it?</a></div></div>')
    calls = _call_table(v["recent_calls"], compact=True) if v["recent_calls"] else \
        '<div class="empty small">Nothing yet. Your first backtest will show up here.</div>'
    reports = _report_table(v["recent_reports"]) if v["recent_reports"] else \
        '<div class="empty small">No reports yet.</div>'
    body = f"""
{msg}
<div class="hello"><h1>Hello, {esc(v["you"]["name"])}</h1>
<span class="small subtle">{esc(v["you"]["tier"])} tier · since {esc(v["you"]["member_since"])}</span></div>
{first}
<h2>Connect your app</h2>
<p class="small muted">Server address: <code>{esc(v["mcp_url"])}</code> — in ChatGPT, Gemini
or Claude on the web, that address is all you paste; you sign in instead of using a key.</p>
{_snippets(v["connect"])}
<h2>This hour</h2>
<section class="grid four">{_meters(v["usage"])}</section>
<h2>Recent activity</h2>
<div class="card">{calls}
<p class="small"><a href="/app/logs">All activity →</a></p></div>
<h2>Recent reports</h2>
<div class="card">{reports}
<p class="small"><a href="/app/reports">All reports →</a></p></div>
"""
    return shell("Dashboard — Stratify", body, v["nav"], v["you"], "overview")


def keys(v):
    msg = f'<div class="note">{esc(v["message"])}</div>' if v.get("message") else ""
    # SHOWN ONCE. Nothing re-renders this; there is no route that can fetch it again.
    secret = ""
    if v.get("new_key"):
        secret = (f'<div class="secret"><strong>Your new key</strong>'
                  f'<code>{esc(v["new_key"])}</code>'
                  f'<p class="small muted">Copy it now. It is hashed the moment this page '
                  f'is rendered and cannot be shown again — if you lose it, revoke it and '
                  f'generate another.</p></div>')
    rows = "".join(
        f'<tr><td><code>{esc(k["key_id"])}</code></td>'
        f'<td>…{esc(k["last4"])}</td>'
        f'<td>{esc(k["created"])}<div class="small muted">{esc(k["created_ago"])}</div></td>'
        f'<td>' + ('<span class="tag bad">revoked</span>' if k["revoked"]
                   else '<span class="tag ok">active</span>') + '</td>'
        f'<td>' + ('' if k["revoked"] else
                   f'<form method="post" action="/app/keys/revoke">'
                   f'<input type="hidden" name="key_id" value="{esc(k["key_id"])}">'
                   f'<button class="btn small danger" type="submit">Revoke</button></form>')
        + '</td></tr>' for k in v["keys"])
    table = (f'<div class="scroll"><table><thead><tr><th>Key</th><th>Ends</th>'
             f'<th>Created</th><th>Status</th><th></th></tr></thead>'
             f'<tbody>{rows}</tbody></table></div>') if v["keys"] else \
        '<div class="empty">No keys yet.</div>'

    # CONNECTED APPLICATIONS. Same page as keys because they are the same thing from the
    # user's side: something else holding a credential for this account. The section is
    # omitted entirely when nothing is connected rather than showing an empty table --
    # most people will never use OAuth and do not need to be told what it is.
    conns = v.get("connections") or []
    if conns:
        crows = "".join(
            f'<tr><td><strong>{esc(c["name"])}</strong>'
            + (f'<div class="small muted">{esc(c["uri"])}</div>' if c.get("uri") else "")
            + f'</td><td>{esc(c["last"])}'
              f'<div class="small muted">{esc(c["last_ago"])}</div></td>'
              f'<td><form method="post" action="/app/connections/revoke">'
              f'<input type="hidden" name="client_id" value="{esc(c["client_id"])}">'
              f'<button class="btn small danger" type="submit">Disconnect</button>'
              f'</form></td></tr>' for c in conns)
        connections = f"""
<h2 style="margin-top:28px">Connected applications</h2>
<p class="muted small">Apps you signed in to with Stratify — ChatGPT, Gemini, Claude and
anything else that connected through OAuth. They spend the same quota as an API key.
Disconnecting takes effect immediately and cannot be undone from the app's side.</p>
<div class="card"><div class="scroll"><table><thead><tr><th>Application</th>
<th>Connected</th><th></th></tr></thead><tbody>{crows}</tbody></table></div></div>"""
    else:
        connections = ""
    body = f"""
{msg}{secret}
<h1>API keys</h1>
<p class="muted small">A key is a bearer token. Treat it like a password: anything holding
it can spend this account's quota. Revoking one does not affect the others, and does not
sign you out of this dashboard.</p>
<form method="post" action="/app/keys/new" style="margin:0 0 16px">
<button class="btn primary" type="submit">Generate a new key</button></form>
<div class="card">{table}</div>
{connections}
"""
    return shell("API keys — Stratify", body, v["nav"], v["you"], "keys")


_OUTCOME_TAG = {"ok": "ok", "refused": "warn", "error": "bad", "capacity": "warn"}


def _call_table(rows, compact=False):
    body = "".join(
        f'<tr><td>{esc(r["ts"])}<div class="small muted">{esc(r["ago"])}</div></td>'
        f'<td><code>{esc(r["tool"])}</code></td>'
        f'<td><span class="tag {_OUTCOME_TAG.get(r["outcome"], "")}">'
        f'{esc(r["outcome"])}</span></td>'
        f'<td class="num">{esc(r["cpu"])}</td>'
        f'<td class="num">{esc(r["prices"])}</td>'
        + ('' if compact else f'<td class="small muted">{esc(r["detail"])}</td>')
        + '</tr>' for r in rows)
    head = ('<tr><th>When</th><th>Tool</th><th>Outcome</th><th class="num">CPU s</th>'
            '<th class="num">Prices</th>'
            + ('' if compact else '<th>Detail</th>') + '</tr>')
    return (f'<div class="scroll"><table><thead>{head}</thead>'
            f'<tbody>{body}</tbody></table></div>')


def logs(v):
    t = v["totals"]
    by = " · ".join(f'{esc(k)} {esc(n)}' for k, n in t["by_outcome"]) or "—"
    table = _call_table(v["rows"]) if v["rows"] else \
        '<div class="empty">No calls yet.</div>'
    body = f"""
<h1>Activity</h1>
<p class="muted small">{esc(v["note"])}</p>
<section class="grid four">
<div class="card stat"><div class="k">Calls shown</div><div class="v">{esc(t["calls"])}</div></div>
<div class="card stat"><div class="k">CPU-seconds</div><div class="v">{esc(t["cpu"])}</div></div>
<div class="card stat"><div class="k">Prices released</div><div class="v">{esc(t["prices"])}</div></div>
<div class="card stat"><div class="k">Outcomes</div><div class="v small">{by}</div></div>
</section>
<div class="card">{table}</div>
"""
    return shell("Activity — Stratify", body, v["nav"], v["you"], "logs", wide=True)


def _report_table(rows):
    body = "".join(
        f'<tr><td>{esc(r["created"])}<div class="small muted">{esc(r["ago"])}</div></td>'
        f'<td>{esc(r["name"])}<div class="small muted">{esc(r["summary"])}</div></td>'
        f'<td><code class="small">{esc(r["backtest_id"])}</code></td>'
        f'<td>' + (f'<a class="btn small" href="{esc(r["url"])}">Open</a>'
                   if r["url"] else '') + '</td></tr>' for r in rows)
    return (f'<div class="scroll"><table><thead><tr><th>When</th><th>Strategy</th>'
            f'<th>ID</th><th></th></tr></thead><tbody>{body}</tbody></table></div>')


def reports(v):
    table = _report_table(v["rows"]) if v["rows"] else \
        '<div class="empty">No reports yet. Run a backtest and it will appear here.</div>'
    body = f"""
<h1>Reports</h1>
<p class="muted small">{esc(v["note"])}</p>
<div class="card">{table}</div>
"""
    return shell("Reports — Stratify", body, v["nav"], v["you"], "reports", wide=True)


# ---------------------------------------------------------------------------- docs

def _dl(rows):
    return ('<div class="scroll"><table><tbody>' + "".join(
        f'<tr><td><code>{esc(r["name"])}</code></td>'
        f'<td class="small muted">{esc(r["doc"])}</td></tr>' for r in rows)
        + '</tbody></table></div>')


def docs(v):
    toc = "".join(f'<a href="#{esc(s["key"])}">{esc(s["title"])}</a>'
                  for s in v["sections"])
    ex = "".join(
        f'<h3>{esc(e["title"])}</h3><p class="small muted">{esc(e["ask"])}</p>'
        f'<pre>{_json(e["spec"])}</pre>' for e in v["examples"])
    fields = "".join(f'<h3>{esc(g["title"])}</h3>{_dl(g["rows"])}' for g in v["fields"])
    tiers = "".join(
        f'<tr><td>{esc(t["name"])}</td><td>{esc(t["window"])}</td>'
        f'<td class="num">{esc(t["requests"])}/h</td>'
        f'<td class="num">{esc(t["metadata"])}/h</td>'
        f'<td class="num">{esc(t["cpu"])}</td>'
        f'<td class="num">{esc(t["concurrent"])}</td></tr>' for t in v["tiers"])
    honesty = "".join(f'<li class="small muted">{esc(h)}</li>' for h in v["honesty"])
    L, E, R, P = v["legs"], v["entry"], v["rules"], v["portfolio"]
    body = f"""
<div class="docs">
<aside><div class="toc">{toc}</div></aside>
<article>
<h1>Documentation</h1>

<h2 id="start">Getting started</h2>
<p>Stratify speaks <a href="https://modelcontextprotocol.io">MCP</a> over HTTP. Sign in,
generate a key, and point a client at <code>{esc(v["mcp_url"])}</code>.</p>
{_snippets(v["connect"])}

<h2 id="shape">How a strategy is written</h2>
<p>{esc(v["shape"]["body"])}</p>
<pre>{_json(v["shape"]["skeleton"])}</pre>

<h2 id="legs">Legs</h2>
<p>{esc(L["body"])}</p>
<h3>Strike selectors</h3>{_dl(L["strikes"])}
<h3>Expiry</h3>{_dl(L["expiries"])}

<h2 id="entry">Entry</h2>
<p>{esc(E["body"])}</p>{_dl(E["cadences"])}
<p class="small muted">{esc(E["note"])}</p>

<h2 id="rules">Rules</h2>
<p>{esc(R["body"])}</p>
<h3>Actions</h3>{_dl(R["actions"])}
<p class="small muted">Comparators: <code>{esc(", ".join(R["comparators"]))}</code>.
Combine with <code>{esc(", ".join(R["combinators"]))}</code>.
Up to {esc(R["caps"]["legs"])} legs, {esc(R["caps"]["rules"])} rules and
{esc(R["caps"]["adjustments"])} adjustments per trade.</p>

<h2 id="portfolio">Book rules</h2>
<p>{esc(P["body"])}</p>
<p><code>{esc(", ".join(P["keys"]))}</code></p>

<h2 id="fields">Field reference</h2>
<p class="small muted">Every quantity a condition can test.</p>
{fields}

<h2 id="examples">Worked examples</h2>
{ex}

<h2 id="limits">Limits and honesty</h2>
<div class="scroll"><table><thead><tr><th>Tier</th><th>History</th>
<th class="num">Backtests</th><th class="num">Other calls</th>
<th class="num">CPU s/h</th><th class="num">Concurrent</th>
</tr></thead><tbody>{tiers}</tbody></table></div>
<p class="small muted" style="margin-top:10px">Backtests and built reports spend the
first column. Reading coverage, methodology, search or your own history spends the
second — so checking what the data covers before you run never costs you a run.</p>
<ul style="margin-top:16px">{honesty}</ul>
</article></div>
"""
    return shell("Docs — Stratify", body, v["nav"], None, "docs", wide=True)


def message_page(title, message, detail=None, status_link=("/", "Back to the homepage")):
    body = (f'<h1>{esc(title)}</h1><p>{esc(message)}</p>'
            + (f'<p class="small muted">{esc(detail)}</p>' if detail else "")
            + f'<p><a class="btn" href="{esc(status_link[0])}">{esc(status_link[1])}</a></p>')
    return shell(title + " — Stratify", body)


def legal(v):
    """Privacy and terms. One renderer, because they are the same shape."""
    d = v["doc"]
    body_sections = "".join(
        f'<h2>{esc(s["h"])}</h2><ul>'
        + "".join(f'<li>{esc(i)}</li>' for i in s["items"]) + '</ul>'
        for s in d["sections"])
    other_key, other_label = v["other"]
    external = 'rel="me noopener" target="_blank"'
    people = "".join(
        f'<h2>{esc(p["h"])}</h2><p>{esc(p["lede"])}</p><dl class="reach">'
        + "".join(f'<dt>{esc(label)}</dt><dd><a href="{esc(href)}" '
                  f'{external if href.startswith("http") else ""}>'
                  f'{esc(text)}</a></dd>' for label, href, text in p["links"])
        + "</dl>"
        for p in d.get("people", []))
    return shell(f'{d["title"]} — Stratify', f"""
<h1>{esc(d["title"])}</h1>
<p class="muted small">Last updated {esc(d["updated"])}</p>
<p>{esc(d["intro"])}</p>
{body_sections}
{people}
<h2>Contact</h2>
<p>{esc(d["contact"])}</p>
<p class="small muted"><a href="/{esc(other_key)}">{esc(other_label)}</a> ·
<a href="/docs">Docs</a></p>
""", v["nav"], None, v["page"])


def login(v):
    """Sign-in chooser. The password form is inside <details>, closed by default, so the
    page a Google user sees is one button and one sentence."""
    nxt = esc(v["next"])
    google = (f'<a class="btn big gbtn" href="/auth/google?next={nxt}">{GOOGLE_G}'
              f'Sign in with Google</a>' if v["google_ready"] else
              '<p class="small muted">Google sign-in is not configured on this deployment.</p>')
    error = f'<p class="err" role="alert">{esc(v["error"])}</p>' if v.get("error") else ""
    body = f"""
<div class="narrow">
  <h1>{esc(v["title"])}</h1>
  <p>{esc(v["lede"])}</p>
  {error}
  <p>{google}</p>
  <details class="pwd"{" open" if v.get("error") else ""}>
    <summary>Sign in with a password</summary>
    <p class="small muted">{esc(v["password_note"])}</p>
    <form method="post" action="/login" class="stack">
      <input type="hidden" name="next" value="{nxt}">
      <label>Email <input type="email" name="email" autocomplete="username" required maxlength="254"></label>
      <label>Password <input type="password" name="password" autocomplete="current-password" required></label>
      <button class="btn" type="submit">Sign in</button>
    </form>
  </details>
</div>
"""
    return shell(v["title"] + " — Stratify", body, active="login")


def consent(v):
    """The OAuth consent screen.

    Deliberately plain, and deliberately specific. A consent screen that says "this app
    wants to access your account" teaches people to click Allow without reading; one that
    names what it can and cannot do gives them something to actually decide about.
    """
    grants = "".join(f'<li>{esc(g)}</li>' for g in v["grants"])
    cannot = "".join(f'<li class="small muted">{esc(c)}</li>' for c in v["cannot"])
    fields = "".join(
        f'<input type="hidden" name="{esc(k)}" value="{esc(val)}">'
        for k, val in v["fields"].items())

    if v["verified"]:
        who = f'<strong>{esc(v["client_display"])}</strong>'
        provenance = ""
    else:
        # The host is the fact; the name is a claim. Presented in that order, and labelled.
        who = f'<strong>{esc(v["client_display"])}</strong>'
        claimed = v.get("client_claimed_name")
        provenance = (
            f'<p class="small muted">This application identifies itself as '
            f'{esc(claimed)} at <code>{esc(v["client_display"])}</code>. That name is '
            f'self-declared; the address is not.</p>' if claimed else
            f'<p class="small muted">This application is identified only by its address, '
            f'<code>{esc(v["client_display"])}</code>.</p>')

    body = f"""
<div class="narrow">
  <h1>Connect {who}?</h1>
  <p>Signed in as <strong>{esc(v["you"].get("email", ""))}</strong>.</p>
  {provenance}
  <div class="card">
    <h2>What it will be able to do</h2>
    <ul>{grants}</ul>
    <h2>What it cannot do</h2>
    <ul>{cannot}</ul>
    <p class="small muted">Sending you back to <code>{esc(v["redirect_host"])}</code>.</p>
  </div>
  <form method="post" action="/oauth/authorize" class="row" style="gap:10px;margin-top:18px">
    {fields}
    <button class="btn" type="submit" name="decision" value="allow">Allow</button>
    <button class="btn ghost" type="submit" name="decision" value="deny">Cancel</button>
  </form>
  <p class="small muted" style="margin-top:14px">You can disconnect this at any time from
  your dashboard. Disconnecting revokes its access immediately.</p>
</div>
"""
    return shell("Connect an application — Stratify", body)
