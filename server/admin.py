"""Admin console: what is happening, who is doing it, and what they are telling us.

ONE PAGE, FOUR TABS, server-rendered. Everything is already in one SQLite file, the whole
dataset at this stage fits in a single response, and the console is read a few times a day
by one person and one triage agent. A client-side app here would be more moving parts than
the thing it displays.

WHAT IT IS FOR, in order: the feedback queue first, because that is the input to the
improvement loop; then the funnel, because signups that never call are the number most
likely to flatter; then usage and the raw call tail for debugging.

ACCESS IS THE SESSION COOKIE PLUS accounts.is_admin. It reuses the dashboard session
rather than inventing a second credential — one login, one revocation path — and the flag
is a column, so granting it is an explicit write rather than an edit to a config file.
"""
import html
import json
import time

from . import feedback as feedback_mod, store

CSS = """
:root{--bg:#f7f7f5;--fg:#17191a;--mut:#6b7070;--faint:#93999a;--line:#e3e4e0;
--card:#fff;--raised:#fbfbf9;--good:#1f6b47;--good-bg:#e8f2ec;--bad:#9e3327;
--bad-bg:#f8e8e5;--warn:#8a6118;--warn-bg:#f7efdd;--accent:#1b5e57;--accent-bg:#e4eeeb}
@media(prefers-color-scheme:dark){:root{--bg:#121413;--fg:#e9ece8;--mut:#98a09a;
--faint:#767e78;--line:#2a2e2b;--card:#191c1a;--raised:#1e221f;--good:#5ab483;
--good-bg:#17281f;--bad:#e08376;--bad-bg:#2e1c1a;--warn:#cda24f;--warn-bg:#2a2417;
--accent:#5fb3a6;--accent-bg:#17302c}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1140px;margin:0 auto;padding:26px 20px 90px}
header.top{display:flex;align-items:baseline;justify-content:space-between;gap:16px;
flex-wrap:wrap;border-bottom:2px solid var(--fg);padding-bottom:12px;margin-bottom:20px}
h1{font-size:20px;margin:0;letter-spacing:-.01em}
.who{font:12px/1 ui-monospace,Menlo,monospace;color:var(--faint)}
h2{font:500 11px/1 ui-monospace,Menlo,monospace;letter-spacing:.1em;text-transform:uppercase;
color:var(--mut);margin:26px 0 10px}
h2:first-of-type{margin-top:6px}
.tabs{display:flex;gap:2px;background:var(--line);border:1px solid var(--line);
border-radius:9px;padding:2px;margin-bottom:22px;overflow-x:auto}
.tabs button{flex:1;white-space:nowrap;padding:8px 14px;border:0;border-radius:7px;
background:transparent;color:var(--mut);font:500 13px/1 inherit;cursor:pointer}
.tabs button[aria-selected=true]{background:var(--card);color:var(--fg);
box-shadow:0 1px 2px rgba(0,0,0,.06)}
.panel[hidden]{display:none}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:10px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:9px;padding:12px 13px}
.stat .k{font:500 10px/1.3 ui-monospace,Menlo,monospace;letter-spacing:.07em;
text-transform:uppercase;color:var(--mut)}
.stat .v{font-size:22px;font-variant-numeric:tabular-nums;margin-top:3px;letter-spacing:-.02em}
.stat .s{font-size:11.5px;color:var(--faint);margin-top:3px;line-height:1.35}
.stat.alert{border-color:var(--bad)}
.scroll{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:9px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px 11px;border-bottom:1px solid var(--line);
font-variant-numeric:tabular-nums;vertical-align:top}
tbody tr:last-child td{border-bottom:0}
th{color:var(--mut);font:500 11px/1.3 ui-monospace,Menlo,monospace;letter-spacing:.05em;
text-transform:uppercase;white-space:nowrap}
td.n,th.n{text-align:right}
.pill{display:inline-block;font:500 10px/1 ui-monospace,Menlo,monospace;letter-spacing:.06em;
text-transform:uppercase;padding:4px 6px;border-radius:3px;white-space:nowrap}
.p-new,.p-reopened{background:var(--bad-bg);color:var(--bad)}
.p-triaged,.p-in_progress{background:var(--warn-bg);color:var(--warn)}
.p-fixed{background:var(--good-bg);color:var(--good)}
.p-wontfix,.p-duplicate{background:var(--line);color:var(--mut)}
.p-blocker{background:var(--bad-bg);color:var(--bad)}
.p-major{background:var(--warn-bg);color:var(--warn)}
.p-minor,.p-idea{background:var(--line);color:var(--mut)}
.p-cat{background:var(--accent-bg);color:var(--accent)}
.ttl{font-weight:500;line-height:1.35}
.body{color:var(--mut);font-size:12.5px;line-height:1.45;margin-top:4px;
max-width:56ch;white-space:pre-wrap}
.meta{color:var(--faint);font:11px/1.4 ui-monospace,Menlo,monospace;margin-top:5px}
details{margin-top:6px}
summary{cursor:pointer;font:11px/1.4 ui-monospace,Menlo,monospace;color:var(--accent)}
pre{background:var(--raised);border:1px solid var(--line);border-radius:6px;padding:9px;
font-size:11px;line-height:1.45;overflow-x:auto;margin:6px 0 0;max-height:280px}
form.tri{display:flex;gap:5px;flex-wrap:wrap;align-items:center;margin-top:8px}
form.issue{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin:8px 0 14px}
form.inline{display:inline}
form.issue button,form.inline button{padding:6px 11px;border:1px solid var(--line);
  border-radius:6px;background:var(--card);color:var(--fg);cursor:pointer;font:inherit}
select,input[type=text],input[type=email]{padding:5px 7px;border:1px solid var(--line);border-radius:6px;
background:var(--bg);color:var(--fg);font:12px/1 inherit}
input[type=text]{min-width:180px}
button.go{padding:6px 11px;border:1px solid var(--accent);background:var(--accent);
color:#fff;border-radius:6px;font:500 12px/1 inherit;cursor:pointer}
.note{background:var(--accent-bg);color:var(--accent);border-radius:8px;padding:9px 12px;
font-size:13px;margin-bottom:16px}
.empty{color:var(--faint);font-size:13px;padding:16px 12px}
.spark{display:flex;align-items:flex-end;gap:2px;height:34px;margin-top:7px}
.spark i{flex:1;background:var(--accent);border-radius:1px;min-height:1px;opacity:.75}
.lede{color:var(--mut);font-size:13px;margin:0 0 12px;max-width:70ch;line-height:1.5}
a{color:var(--accent)}
"""

TAB_JS = """
(function(){
 var bs=[].slice.call(document.querySelectorAll('.tabs button'));
 function show(id){bs.forEach(function(b){var on=b.dataset.tab===id;
  b.setAttribute('aria-selected',on);
  document.getElementById('tab-'+b.dataset.tab).hidden=!on;});
  try{location.hash=id}catch(e){}}
 bs.forEach(function(b){b.onclick=function(){show(b.dataset.tab)}});
 var h=(location.hash||'').replace('#','');
 show(bs.some(function(b){return b.dataset.tab===h})?h:bs[0].dataset.tab);
})();
"""


def _e(v):
    return html.escape("" if v is None else str(v))


def _ago(ts):
    if not ts:
        return "never"
    d = time.time() - float(ts)
    if d < 90:
        return "just now"
    for unit, size in (("m", 60), ("h", 3600), ("d", 86400)):
        if d < size * 60 or unit == "d":
            return f"{int(d // size)}{unit} ago"
    return "?"


def _pill(value, prefix=""):
    return f'<span class="pill p-{_e(prefix or value)}">{_e(value)}</span>'


def _stat(key, value, sub="", alert=False):
    cls = "stat alert" if alert else "stat"
    return (f'<div class="{cls}"><div class="k">{_e(key)}</div>'
            f'<div class="v">{_e(value)}</div><div class="s">{_e(sub)}</div></div>')


def _spark(values):
    top = max(values) if values and max(values) else 1
    bars = "".join(f'<i style="height:{max(1, round(v / top * 100))}%"></i>' for v in values)
    return f'<div class="spark">{bars}</div>'


def _table(headers, rows, empty="Nothing yet."):
    if not rows:
        return f'<div class="scroll"><div class="empty">{_e(empty)}</div></div>'
    head = "".join(f'<th class="{c}">{_e(h)}</th>' for h, c in headers)
    return (f'<div class="scroll"><table><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')


# ---------------------------------------------------------------- tabs

def _overview(o, series):
    # The funnel is shown as a rate, not a count, because "48 signups" reads as success
    # and "48 signups, 9 ever called" reads as the problem it actually is.
    activation = (100.0 * o["activated"] / o["accounts"]) if o["accounts"] else 0.0
    calls = o["calls_24h"] or 1
    error_rate = 100.0 * o["errors_24h"] / calls
    stats = "".join([
        _stat("Accounts", o["accounts"], f'{o["accounts_paid"]} paid'),
        _stat("Activated", f'{activation:.0f}%',
              f'{o["activated"]} of {o["accounts"]} ever made a call',
              alert=o["accounts"] >= 10 and activation < 40),
        _stat("Active 7d", o["active_7d"], "accounts that called this week"),
        _stat("Signups 24h", o["signups_24h"], f'{o["signups_7d"]} in 7 days'),
        _stat("Calls 24h", o["calls_24h"], f'{o["calls_7d"]} in 7 days'),
        _stat("Refused 24h", o["refusals_24h"], "guardrails firing — see Feedback"),
        _stat("Errors 24h", o["errors_24h"], f'{error_rate:.1f}% of calls',
              alert=o["errors_24h"] > 0),
        _stat("CPU 24h", f'{o["cpu_24h"]:.1f}s', "engine time, all accounts"),
        _stat("Prices released", f'{o["points_24h"]:,}', "option prints, 24h"),
        _stat("Backtests", f'{o["backtests_total"]:,}', "stored results, all time"),
        _stat("Book entries", o["book_entries"], "strategies that cleared the bar"),
        _stat("Open feedback", o["feedback_open"], f'{o["feedback_total"]} filed all time',
              alert=o["feedback_open"] > 0),
    ])
    tools = [f'<tr><td>{_e(t["tool"])}</td><td class="n">{t["n"]:,}</td>'
             f'<td class="n">{t["refused"]:,}</td><td class="n">{t["errored"]:,}</td>'
             f'<td class="n">{100.0 * t["refused"] / max(t["n"], 1):.0f}%</td></tr>'
             for t in o["tools_7d"]]
    refusals = [f'<tr><td class="n">{r["n"]}</td><td class="body">{_e(r["refusal"])}</td></tr>'
                for r in o["refusals_7d"]]
    return f"""
<h2>Last 24 hours &amp; 7 days</h2>
<div class="grid">{stats}</div>

<h2>Fourteen days</h2>
<div class="grid">
  <div class="stat"><div class="k">Calls per day</div>
    <div class="v">{sum(series['calls']):,}</div>{_spark(series['calls'])}</div>
  <div class="stat"><div class="k">Signups per day</div>
    <div class="v">{sum(series['signups']):,}</div>{_spark(series['signups'])}</div>
</div>

<h2>Tool use, 7 days</h2>
{_table([("Tool", ""), ("Calls", "n"), ("Refused", "n"), ("Errors", "n"), ("Refusal rate", "n")],
        tools, "No calls in the last 7 days.")}

<h2>What we said no to</h2>
<p class="lede">The most-repeated refusals are the shortest list of what to build next or
explain better. A guardrail firing constantly is either a missing feature or a missing
sentence in the docs.</p>
{_table([("Times", "n"), ("Refusal", "")], refusals, "No refusals in the last 7 days.")}
"""


def _feedback_row(f):
    tags = ""
    try:
        parsed = json.loads(f.get("tags_json") or "[]")
        tags = " ".join(f'<span class="pill p-minor">{_e(t)}</span>' for t in parsed)
    except (TypeError, ValueError):
        pass
    ctx = ""
    if f.get("context_json"):
        try:
            pretty = json.dumps(json.loads(f["context_json"]), indent=2)[:6000]
            ctx = (f'<details><summary>reproduction context</summary>'
                   f'<pre>{_e(pretty)}</pre></details>')
        except (TypeError, ValueError):
            pass
    affected = f.get("accounts_affected") or 1
    demand = (f'<span class="pill p-cat">{affected} accounts</span>' if affected > 1 else "")
    bt = (f' &middot; <code>{_e(f["backtest_id"])}</code>' if f.get("backtest_id") else "")
    note = (f'<div class="meta">note: {_e(f["triage_note"])}</div>'
            if f.get("triage_note") else "")
    options = "".join(f'<option value="{s}"{" selected" if s == f["status"] else ""}>{s}</option>'
                      for s in feedback_mod.STATUSES)
    sev_options = "".join(
        f'<option value="{s}"{" selected" if s == f["severity"] else ""}>{s}</option>'
        for s in sorted(feedback_mod.SEVERITIES))
    return f"""<tr>
<td>{_pill(f["status"])}<div class="meta">{_e(_ago(f["ts"]))}</div></td>
<td>{_pill(f["severity"])}<br>{_pill(f["category"], "cat")}</td>
<td>
  <div class="ttl">{_e(f["title"])}</div>
  <div class="body">{_e(f["body"][:900])}</div>
  <div class="meta">{_e(f["feedback_id"])} &middot; {_e(f.get("email") or f["account_id"])}
    &middot; {_e(f["tier"])} &middot; reported {f["times_seen"]}&times;{bt} {demand}</div>
  {tags}{note}{ctx}
  <form class="tri" method="post" action="/admin/feedback">
    <input type="hidden" name="feedback_id" value="{_e(f["feedback_id"])}">
    <select name="status">{options}</select>
    <select name="severity">{sev_options}</select>
    <input type="text" name="triage_note" placeholder="triage note" maxlength="300">
    <button class="go" type="submit">Update</button>
  </form>
</td></tr>"""


def _feedback(items, themes, counts):
    open_n = sum(counts["status"].get(s, 0)
                 for s in ("new", "reopened", "triaged", "in_progress"))
    cats = "".join(
        _stat(c["category"], c["n"], f'{c["open"]} open') for c in counts["category"])
    theme_rows = [
        f'<tr><td class="n">{t["accounts"]}</td><td class="n">{t["reports"]}</td>'
        f'<td>{_pill(t["category"], "cat")}</td><td class="ttl">{_e(t["title"])}</td>'
        f'<td class="n">{t["open_count"]}</td><td>{_e(_ago(t["last_seen"]))}</td></tr>'
        for t in themes]
    rows = [_feedback_row(f) for f in items]
    return f"""
<p class="lede">Everything users filed, newest and worst first. Each item carries the
reporter's recent calls and the spec that was running, so an item can be reproduced
without writing back to them. Closing an item that gets reported again reopens it.</p>

<h2>By category &mdash; {open_n} open</h2>
<div class="grid">{cats or '<div class="empty">No feedback filed yet.</div>'}</div>

<h2>Themes &mdash; what more than one person asked for</h2>
<p class="lede">Grouped across accounts on a normalised title, so the same complaint from
nine people is one line with a nine on it. This is the demand signal; the queue below is
the work.</p>
{_table([("Accounts", "n"), ("Reports", "n"), ("Category", ""), ("Theme", ""),
         ("Open", "n"), ("Last", "")], theme_rows, "Nothing filed yet.")}

<h2>Queue</h2>
{_table([("Status", ""), ("Priority", ""), ("Report", "")], rows, "Nothing filed yet.")}
"""


def _users(accounts):
    admin_badge = '<span class="pill p-cat">admin</span>'
    rows = [
        f'<tr><td>{_e(a["email"])} {admin_badge if a["is_admin"] else ""}'
        f'<div class="meta">{_e(a["account_id"])}</div></td>'
        f'<td>{_pill(a["tier"], "cat")}</td>'
        f'<td class="n">{a["keys"]}</td><td class="n">{a["calls"]:,}</td>'
        f'<td class="n">{a["cpu"]:.1f}s</td><td class="n">{a["reports"]}</td>'
        f'<td>{_e(_ago(a["last_seen"]))}</td><td>{_e(_ago(a["created_at"]))}</td></tr>'
        for a in accounts]
    issued = [
        f'<tr><td>{_e(p["email"])}<div class="meta">{_e(p["account_id"])}</div></td>'
        f'<td>{_e(_ago(p["password_set_at"]))}</td>'
        f'<td><form method="post" action="/admin/password" class="inline">'
        f'<input type="hidden" name="email" value="{_e(p["email"])}">'
        f'<input type="hidden" name="action" value="clear">'
        f'<button type="submit">Remove</button></form></td></tr>'
        for p in store.password_accounts()]
    return f"""
<p class="lede">Newest first. An account with zero calls signed up and never connected the
server &mdash; that is an onboarding failure, not a user.</p>
<h2>Accounts</h2>
{_table([("Account", ""), ("Tier", ""), ("Keys", "n"), ("Calls", "n"), ("CPU", "n"),
         ("Reports", "n"), ("Last call", ""), ("Joined", "")], rows, "No accounts yet.")}
<h2>Password sign-ins</h2>
<p class="lede">Issued by hand, for a directory reviewer or anyone without Google. The
address signs in at <code>/login</code>. Creates the account if the address is new.
Minimum {store.MIN_PASSWORD_LENGTH} characters; it is hashed on the way in and cannot be
shown again.</p>
<form method="post" action="/admin/password" class="issue">
  <input type="email" name="email" placeholder="reviewer@example.com" required>
  <input type="text" name="password" placeholder="password (min {store.MIN_PASSWORD_LENGTH})" autocomplete="off" required minlength="{store.MIN_PASSWORD_LENGTH}">
  <button type="submit">Issue</button>
</form>
{_table([("Address", ""), ("Set", ""), ("", "")], issued, "No passwords issued.")}
"""


def _calls(rows):
    out = []
    for c in rows:
        detail = _e(c["refusal"] or "")[:220] if c["outcome"] != "ok" else ""
        out.append(
            f'<tr><td>{_e(_ago(c["ts"]))}</td><td>{_e(c["email"] or "—")}</td>'
            f'<td>{_e(c["tool"] or "—")}</td><td>{_pill(c["outcome"], "cat" if c["outcome"] == "ok" else "new")}</td>'
            f'<td class="n">{(c["cpu_seconds"] or 0):.3f}</td>'
            f'<td class="n">{c["price_points"] or 0}</td>'
            f'<td class="body">{detail}</td></tr>')
    return f"""
<p class="lede">The raw tail. Arguments are redacted at write time; this is the shape of
traffic, not its contents.</p>
<h2>Recent calls</h2>
{_table([("When", ""), ("Account", ""), ("Tool", ""), ("Outcome", ""), ("CPU", "n"),
         ("Prices", "n"), ("Detail", "")], out, "No calls yet.")}
"""


def _waitlist(rows):
    """Who wants Premium. The one list that says whether building it is worth it, which
    is why it is here and not only in the database."""
    body = [f'<tr><td>{_e(r["email"])}</td>'
            f'<td>{_e(r["display_name"] or (r["account_id"] or "— not signed up"))}</td>'
            f'<td>{_e(r["source"] or "")}</td><td>{_e(_ago(r["created_at"]))}</td></tr>'
            for r in rows]
    return (f'<p class="sub">{len(rows)} on the list. Export: '
            f'<code>sqlite3 service.sqlite "SELECT email FROM waitlist"</code></p>'
            + _table([("Email", ""), ("Account", ""), ("Source", ""), ("Joined", "")],
                     body, empty="Nobody yet. The button is on /pricing."))


def console(account, message=None):
    overview = store.admin_overview()
    body = "".join([
        '<div class="tabs" role="tablist">',
        '<button data-tab="overview">Overview</button>',
        f'<button data-tab="feedback">Feedback ({overview["feedback_open"]})</button>',
        '<button data-tab="users">Users</button>',
        '<button data-tab="calls">Calls</button>',
        f'<button data-tab="waitlist">Waitlist ({store.waitlist_count()})</button>',
        "</div>",
        f'<div class="note">{_e(message)}</div>' if message else "",
        f'<div class="panel" id="tab-overview" hidden>'
        f'{_overview(overview, store.daily_series())}</div>',
        f'<div class="panel" id="tab-feedback" hidden>'
        f'{_feedback(store.list_feedback(limit=200), store.feedback_themes(), store.feedback_counts())}</div>',
        f'<div class="panel" id="tab-users" hidden>{_users(store.admin_accounts())}</div>',
        f'<div class="panel" id="tab-calls" hidden>{_calls(store.admin_recent_calls())}</div>',
        f'<div class="panel" id="tab-waitlist" hidden>{_waitlist(store.waitlist_rows())}</div>',
    ])
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="robots" content="noindex,nofollow">'
            f'<title>Stratify admin</title><style>{CSS}</style></head><body>'
            f'<div class="wrap"><header class="top"><h1>Stratify admin</h1>'
            f'<span class="who">{_e(account["email"])}</span></header>'
            f'{body}</div><script>{TAB_JS}</script></body></html>')
