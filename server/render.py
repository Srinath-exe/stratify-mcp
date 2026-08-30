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
:root{
  --bg:#fbfaf9; --surface:#fff; --line:#e6e3de; --fg:#1a1a19; --muted:#6b6b67;
  --accent:#2f5d8a; --good:#1f7a4d; --bad:#a3352c; --warn:#8a6a1f;
  --radius:10px; --gap:16px; --measure:68ch;
}
@media (prefers-color-scheme:dark){:root{
  --bg:#141413; --surface:#1c1c1a; --line:#2e2d2a; --fg:#e9e7e3; --muted:#9b9993;
  --accent:#7fb0dd; --good:#4caf7d; --bad:#e0796d; --warn:#d0a94a;
}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:15px/1.6 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif;
  -webkit-font-smoothing:antialiased}
a{color:var(--accent)}
code,pre{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px}
pre{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);
  padding:12px 14px;overflow-x:auto;margin:0}
h1{font-size:26px;line-height:1.25;margin:0 0 8px}
h2{font-size:19px;margin:32px 0 10px}
h3{font-size:15px;margin:20px 0 6px}
p{margin:0 0 12px;max-width:var(--measure)}
.muted{color:var(--muted)}
.small{font-size:13px}
.wrap{max-width:960px;margin:0 auto;padding:24px 20px 72px}
.wrap.wide{max-width:1180px}
header.top{border-bottom:1px solid var(--line);background:var(--surface)}
header.top .inner{max-width:1180px;margin:0 auto;padding:12px 20px;
  display:flex;align-items:center;gap:20px;flex-wrap:wrap}
.brand{font-weight:650;letter-spacing:-.01em;text-decoration:none;color:var(--fg)}
nav.main{display:flex;gap:14px;flex-wrap:wrap}
nav.main a{text-decoration:none;color:var(--muted);font-size:14px;padding:4px 0}
nav.main a.on{color:var(--fg);box-shadow:inset 0 -2px 0 var(--accent)}
.spacer{flex:1}
.btn{display:inline-block;border:1px solid var(--line);background:var(--surface);
  color:var(--fg);border-radius:8px;padding:9px 14px;font-size:14px;cursor:pointer;
  text-decoration:none}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.btn.danger{color:var(--bad)}
.btn.small{padding:5px 9px;font-size:13px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);
  padding:16px 18px;margin:0 0 var(--gap)}
.grid{display:grid;gap:var(--gap)}
@media(min-width:720px){.grid.two{grid-template-columns:1fr 1fr}
  .grid.three{grid-template-columns:repeat(3,1fr)}
  .grid.four{grid-template-columns:repeat(4,1fr)}}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);
  vertical-align:top}
th{color:var(--muted);font-weight:500;font-size:12px;text-transform:uppercase;
  letter-spacing:.06em}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.scroll{overflow-x:auto}
.tag{display:inline-block;border:1px solid var(--line);border-radius:999px;
  padding:1px 8px;font-size:12px;color:var(--muted)}
.tag.ok{color:var(--good);border-color:currentColor}
.tag.bad{color:var(--bad);border-color:currentColor}
.tag.warn{color:var(--warn);border-color:currentColor}
.note{border-left:3px solid var(--accent);padding:8px 0 8px 12px;margin:0 0 var(--gap);
  color:var(--muted);font-size:13.5px;max-width:var(--measure)}
.note.bad{border-color:var(--bad);color:var(--bad)}
.meter{height:5px;background:var(--line);border-radius:3px;overflow:hidden;margin-top:8px}
.meter i{display:block;height:100%;background:var(--accent)}
.meter i.full{background:var(--bad)}
.stat .k{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.stat .v{font-size:22px;font-variant-numeric:tabular-nums;margin-top:2px}
.secret{background:var(--surface);border:2px dashed var(--accent);border-radius:var(--radius);
  padding:14px 16px;margin:0 0 var(--gap)}
.secret code{font-size:14px;word-break:break-all;display:block;margin:8px 0}
.hero{padding:56px 0 32px}
.hero h1{font-size:34px;max-width:22ch}
.hero .lede{font-size:17px;color:var(--muted);max-width:56ch}
.hero .cta{display:flex;gap:10px;flex-wrap:wrap;margin:22px 0 0}
.eyebrow{font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:var(--accent);
  margin:0 0 12px}
.step .n{font-variant-numeric:tabular-nums;color:var(--accent);font-size:13px}
.empty{text-align:center;padding:36px 20px;color:var(--muted)}
footer.site{border-top:1px solid var(--line);margin-top:48px}
footer.site .inner{max-width:1180px;margin:0 auto;padding:20px;font-size:13px;
  color:var(--muted);display:flex;gap:16px;flex-wrap:wrap}
.toc{position:sticky;top:16px}
.toc a{display:block;padding:3px 0;font-size:13.5px;text-decoration:none;color:var(--muted)}
@media(min-width:900px){.docs{display:grid;grid-template-columns:200px 1fr;gap:32px}}
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


def shell(title, body, nav=None, you=None, active=None, wide=False):
    """The page frame. Every page on the site goes through here."""
    links = ""
    if nav:
        links = "".join(
            f'<a href="{esc(n["href"])}" class="{"on" if n["key"] == active else ""}">'
            f'{esc(n["label"])}</a>' for n in nav)
    right = ('<a class="btn small" href="/logout">Sign out</a>' if you else
             '<a class="btn small primary" href="/auth/google">Sign in</a>')
    who = (f'<span class="small muted">{esc(you["email"])}</span>' if you else "")
    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><style>{STYLE}</style></head><body>
<header class="top"><div class="inner">
<a class="brand" href="/">Stratify</a>
<nav class="main">{links}</nav>
<span class="spacer"></span>{who}{right}
</div></header>
<main class="wrap{' wide' if wide else ''}">{body}</main>
<footer class="site"><div class="inner">
<a href="/docs">Docs</a><a href="/app">Dashboard</a><a href="/privacy">Privacy</a>
<a href="/terms">Terms</a><a href="/healthz">Status</a>
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
    h = v["hero"]
    err = f'<div class="note bad">{esc(v["error"])}</div>' if v.get("error") else ""
    signin = (f'<a class="btn primary" href="{esc(h["cta_href"])}">{esc(h["cta"])}</a>'
              if v["google_ready"] or v["signed_in"] else
              '<span class="tag warn">Google sign-in is not configured on this server</span>')
    pillars = "".join(
        f'<div class="card"><h3>{esc(p["title"])}</h3>'
        f'<p class="small muted">{esc(p["body"])}</p></div>' for p in v["pillars"])
    steps = "".join(
        f'<div class="card step"><div class="n">{p["n"]}</div>'
        f'<h3>{esc(p["title"])}</h3><p class="small muted">{esc(p["body"])}</p></div>'
        for p in v["steps"])
    ft = v["free_tier"]
    body = f"""
{onetap_widget(v.get("onetap"))}
{err}
<section class="hero">
  <p class="eyebrow">{esc(h["eyebrow"])}</p>
  <h1>{esc(h["title"])}</h1>
  <p class="lede">{esc(h["lede"])}</p>
  <div class="cta">{signin}
    <a class="btn" href="{esc(h["secondary"]["href"])}">{esc(h["secondary"]["label"])}</a>
  </div>
</section>
<section class="grid two">
  <div class="card"><h3>You say</h3>
    <p class="small">{esc(h["example"]["ask"])}</p></div>
  <div class="card"><h3>It runs this</h3>
    <pre>{_json(h["example"]["spec"])}</pre></div>
</section>
<h2>What makes it different</h2>
<section class="grid two">{pillars}</section>
<h2>Three steps</h2>
<section class="grid three">{steps}</section>
<h2>Free tier</h2>
<div class="card"><div class="grid three">
  <div class="stat"><div class="k">History</div><div class="v">{esc(ft["window"])}</div></div>
  <div class="stat"><div class="k">Rate</div><div class="v">{esc(ft["requests"])}</div></div>
  <div class="stat"><div class="k">Compute</div><div class="v">{esc(ft["cpu"])}</div></div>
</div><p class="small muted" style="margin-top:14px">{esc(ft["note"])}</p></div>
"""
    return shell("Stratify — backtest any NIFTY options strategy", body)


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
    return "".join(f'<h3>{esc(c["label"])}</h3><pre>{esc(c["body"])}</pre>'
                   for c in connect)


def overview(v):
    msg = f'<div class="note">{esc(v["message"])}</div>' if v.get("message") else ""
    if not v["has_key"]:
        first = ('<div class="card"><h3>Start here</h3>'
                 '<p class="small muted">You have no API key yet. One click and you are '
                 'connected.</p>'
                 '<form method="post" action="/app/keys/new">'
                 '<button class="btn primary" type="submit">Generate my key</button>'
                 '</form></div>')
    else:
        first = (f'<div class="card"><h3>Connected</h3><p class="small muted">'
                 f'{esc(v["key_count"])} active key(s). Paste one into your client as a '
                 f'bearer token.</p>'
                 f'<a class="btn small" href="/app/keys">Manage keys</a></div>')
    calls = _call_table(v["recent_calls"], compact=True) if v["recent_calls"] else \
        '<div class="empty small">Nothing yet. Your first backtest will show up here.</div>'
    reports = _report_table(v["recent_reports"]) if v["recent_reports"] else \
        '<div class="empty small">No reports yet.</div>'
    body = f"""
{msg}
<h1>Hello, {esc(v["you"]["name"])}</h1>
<p class="muted small">{esc(v["you"]["tier"])} tier · member since {esc(v["you"]["member_since"])}</p>
{first}
<h2>This hour</h2>
<section class="grid four">{_meters(v["usage"])}</section>
<h2>Connect</h2>
<div class="card"><p class="small muted">Server URL: <code>{esc(v["mcp_url"])}</code></p>
{_snippets(v["connect"])}</div>
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
    body = f"""
{msg}{secret}
<h1>API keys</h1>
<p class="muted small">A key is a bearer token. Treat it like a password: anything holding
it can spend this account's quota. Revoking one does not affect the others, and does not
sign you out of this dashboard.</p>
<form method="post" action="/app/keys/new" style="margin:0 0 16px">
<button class="btn primary" type="submit">Generate a new key</button></form>
<div class="card">{table}</div>
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
<th class="num">Calls</th><th class="num">CPU s/h</th><th class="num">Concurrent</th>
</tr></thead><tbody>{tiers}</tbody></table></div>
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
    return shell(f'{d["title"]} — Stratify', f"""
<h1>{esc(d["title"])}</h1>
<p class="muted small">Last updated {esc(d["updated"])}</p>
<p>{esc(d["intro"])}</p>
{body_sections}
<h2>Contact</h2>
<p>{esc(d["contact"])}</p>
<p class="small muted"><a href="/{esc(other_key)}">{esc(other_label)}</a> ·
<a href="/docs">Docs</a></p>
""", v["nav"], None, v["page"])
