"""Account dashboard: sign up, manage keys, see usage.

Server-rendered HTML, no build step and no JavaScript framework. The whole surface is four
routes, and the thing it exists to do -- show a key exactly once and never again -- is
easier to get right without a client-side state layer in the way.

SESSIONS ARE NOT API KEYS. The dashboard cookie and the bearer token are separate secrets
with separate lifetimes. A browser session stolen from a laptop must not hand over the key
an agent is using in production, and revoking one must not require revoking the other.
"""
import html
import time

from . import quota, store

CSS = """
:root{--bg:#fbfaf9;--fg:#1a1a19;--mut:#6b6b68;--line:#e5e2dd;--card:#fff;
--good:#1f7a4d;--bad:#a3352c;--warn:#8a6a1f;--accent:#2f5d8a}
@media(prefers-color-scheme:dark){:root{--bg:#151514;--fg:#e9e7e3;--mut:#9b9994;
--line:#2e2d2a;--card:#1d1d1b;--good:#4caf7d;--bad:#e0796d;--warn:#d0a94a;--accent:#7fb0dd}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:820px;margin:0 auto;padding:36px 20px 80px}
h1{font-size:23px;margin:0 0 4px}
h2{font-size:12px;margin:30px 0 10px;text-transform:uppercase;letter-spacing:.08em;
color:var(--mut)}
.sub{color:var(--mut);font-size:13px;margin-bottom:22px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:16px 18px;margin-bottom:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px}
.stat .k{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut)}
.stat .v{font-size:21px;font-variant-numeric:tabular-nums;margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:8px;border-bottom:1px solid var(--line);
font-variant-numeric:tabular-nums}
th{color:var(--mut);font-weight:500} td.n,th.n{text-align:right}
input[type=email]{padding:9px 11px;border:1px solid var(--line);border-radius:7px;
background:var(--bg);color:var(--fg);font-size:14px;min-width:250px}
button{padding:9px 15px;border:1px solid var(--accent);background:var(--accent);
color:#fff;border-radius:7px;font-size:14px;cursor:pointer}
button.ghost{background:transparent;color:var(--mut);border-color:var(--line)}
code,pre{background:var(--line);border-radius:6px;font-size:12px}
code{padding:2px 6px} pre{padding:12px;overflow-x:auto;margin:0}
.key{font-family:ui-monospace,Menlo,monospace;font-size:13px;word-break:break-all}
.bar{height:6px;background:var(--line);border-radius:3px;overflow:hidden;margin-top:8px}
.bar i{display:block;height:100%;background:var(--accent)}
.warn{color:var(--warn)} .mut{color:var(--mut)}
.notice{border-left:3px solid var(--warn);padding-left:12px}
a{color:var(--accent)}
.scroll{overflow-x:auto}
"""


def _e(v):
    return html.escape(str(v))


def _page(title, body):
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{_e(title)}</title><style>{CSS}</style></head>'
            f'<body><div class="wrap">{body}</div></body></html>')


def landing(base_url, error=None):
    err = f'<div class="card notice">{_e(error)}</div>' if error else ""
    return _page("Stratify — sign up", f"""
      <h1>Stratify</h1>
      <div class="sub">Backtesting for Indian index options, on real 1-minute NIFTY data,
        for you or for your AI agent.</div>
      {err}
      <div class="card">
        <form method="post" action="/signup">
          <input type="email" name="email" placeholder="you@example.com" required>
          <button type="submit">Create account and key</button>
        </form>
        <div class="sub" style="margin:12px 0 0">Free tier: one year of NIFTY options,
          1-minute resolution, 100 backtests an hour. No card, no approval step.</div>
      </div>
      <h2>What you get</h2>
      <div class="card">
        <table>
          <tr><td>Data</td><td class="mut">NIFTY options, 1-minute, 246 trading days,
            58 expiries. Results only — no market data is ever returned</td></tr>
          <tr><td>Structures</td><td class="mut">short strangle, iron condor, iron fly,
            credit spread, long option</td></tr>
          <tr><td>Signals</td><td class="mut">4 entry gates x 11 directional biases</td></tr>
          <tr><td>Every result</td><td class="mut">carries an honesty panel: out-of-sample
            split, walk-forward folds, bootstrap interval, and a Sharpe deflated for how
            many variants you have tried</td></tr>
        </table>
      </div>
      <div class="sub">Educational backtesting. Not investment advice.</div>
    """)


def _money(v):
    """Rupees, or an em dash when there is nothing to show. A book entry with no folds has
    no worst fold, and rendering that as 0 would sort it alongside a strategy that actually
    broke even."""
    if v is None:
        return '<span class="mut">—</span>'
    return f"{'&minus;' if v < 0 else ''}&#8377;{abs(v):,.0f}"


def _fmt_ts(t):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))


def dashboard(account, base_url, new_key=None, message=None, mcp_url=None):
    # The connect instructions must name the PROTOCOL host, not whichever host the
    # dashboard happens to be served from.
    mcp_url = mcp_url or f"{base_url}/mcp"
    tier = quota.TIERS.get(account["tier"], quota.TIERS["free"])
    usage = quota.snapshot(account["account_id"], tier)
    keys = store.list_keys(account["account_id"])
    results = store.recent_results_for_account(account["account_id"], limit=10)

    reveal = ""
    if new_key:
        reveal = f"""
        <div class="card notice">
          <div><strong>Your new API key</strong> — shown once, and not recoverable.</div>
          <div class="key" style="margin:10px 0">{_e(new_key)}</div>
          <div class="sub" style="margin:0">Store it now. If you lose it, revoke it and
            issue another.</div>
        </div>"""

    msg = f'<div class="card">{_e(message)}</div>' if message else ""

    req_pct = min(100, 100 * usage["requests_used"] / max(1, usage["requests_limit"]))
    cpu_pct = min(100, 100 * usage["cpu_seconds_used"] / max(1e-9, usage["cpu_seconds_limit"]))

    key_rows = "".join(
        f'<tr><td><code>{_e(k["key_id"])}</code></td>'
        f'<td class="mut">…{_e(k["last4"])}</td>'
        f'<td class="mut">{_fmt_ts(k["created_at"])}</td>'
        f'<td>{"<span class=warn>revoked</span>" if k["revoked_at"] else "active"}</td>'
        f'<td class="n">' +
        ("" if k["revoked_at"] else
         f'<form method="post" action="/dashboard/revoke" style="display:inline">'
         f'<input type="hidden" name="key_id" value="{_e(k["key_id"])}">'
         f'<button class="ghost" type="submit">Revoke</button></form>') +
        '</td></tr>'
        for k in keys) or '<tr><td colspan="5" class="mut">No keys yet.</td></tr>'

    # The book, ranked the way book.py ranks it: worst walk-forward fold first. Showing it
    # sorted by P&L on the page while the tool sorts by consistency would teach two
    # different lessons about the same data.
    entries = store.list_strategies(account["account_id"], limit=15)
    book_rows = "".join(
        f'<tr><td>{_e(e["structure"])}'
        f'<div class="mut" style="font-size:11px">{_e((e["spec_json"] or "")[:110])}</div></td>'
        f'<td class="n">{e["n_trades"]}</td>'
        f'<td class="n">{_money(e["pnl_rupees"])}</td>'
        f'<td class="n">{_money(e["worst_fold_rupees"])}</td>'
        f'<td class="n">{e["folds_profitable"]}/{e["folds_total"]}</td>'
        f'<td class="n">{e["health_score"]}</td>'
        f'<td class="mut n">{e["times_seen"]}&times;</td></tr>'
        for e in entries) or (
        '<tr><td colspan="7" class="mut">Nothing has cleared the bar yet. A result is kept '
        'when it has 30+ trades, positive net P&amp;L, a held-out final 30 % that stayed '
        'profitable, a majority of profitable walk-forward folds, and a health score of at '
        'least 50.</td></tr>')

    calls = store.recent_calls(account["account_id"], limit=25)
    call_rows = "".join(
        f'<tr><td class="mut">{_fmt_ts(c["ts"])}</td>'
        f'<td><code>{_e(c["tool"] or c["method"])}</code></td>'
        f'<td class="{"warn" if c["outcome"] != "ok" else "mut"}">{_e(c["outcome"])}'
        + (f'<div style="font-size:11px">{_e((c["refusal"] or "")[:90])}</div>'
           if c["refusal"] else "") +
        f'</td><td class="n mut">{c["cpu_seconds"]:.3f}s</td>'
        f'<td class="n mut">{c["price_points"]}</td>'
        f'<td class="mut" style="font-size:11px">{_e((c["arguments_json"] or "")[:90])}</td></tr>'
        for c in calls) or '<tr><td colspan="6" class="mut">No calls yet.</td></tr>'

    result_rows = "".join(
        f'<tr><td class="mut">{_fmt_ts(r["created_at"])}</td>'
        f'<td><code>{_e(r["backtest_id"])}</code></td>'
        f'<td class="mut">{_e(r["spec_json"][:90])}</td></tr>'
        for r in results) or '<tr><td colspan="3" class="mut">No backtests yet.</td></tr>'

    return _page("Stratify — dashboard", f"""
      <h1>Dashboard</h1>
      <div class="sub">{_e(account["email"])} · {_e(account["tier"])} tier ·
        <code>{_e(account["account_id"])}</code></div>
      {reveal}{msg}

      <h2>This hour</h2>
      <div class="card"><div class="grid">
        <div class="stat"><div class="k">Backtests</div>
          <div class="v">{usage["requests_used"]}<span class="mut" style="font-size:13px">
          / {usage["requests_limit"]}</span></div>
          <div class="bar"><i style="width:{req_pct:.0f}%"></i></div></div>
        <div class="stat"><div class="k">CPU seconds</div>
          <div class="v">{usage["cpu_seconds_used"]:.1f}<span class="mut"
          style="font-size:13px"> / {usage["cpu_seconds_limit"]:.0f}</span></div>
          <div class="bar"><i style="width:{cpu_pct:.0f}%"></i></div></div>
        <div class="stat"><div class="k">Running now</div>
          <div class="v">{usage["concurrent_active"]}<span class="mut"
          style="font-size:13px"> / {usage["concurrent_limit"]}</span></div></div>
      </div>
      <div class="sub" style="margin:14px 0 0">Limits are metered on the account, not on
        the key. Issuing more keys does not increase your allowance.</div>
      </div>

      <h2>API keys</h2>
      <div class="card"><div class="scroll"><table>
        <tr><th>Key</th><th>Ends</th><th>Created</th><th>Status</th><th></th></tr>
        {key_rows}
      </table></div>
      <form method="post" action="/dashboard/keys" style="margin-top:12px">
        <button type="submit">Issue a new key</button>
        <span class="sub">Up to {quota.MAX_ACTIVE_KEYS_PER_ACCOUNT} active.</span>
      </form></div>

      <h2>Connect an agent</h2>
      <div class="card">
        <div class="sub" style="margin:0 0 8px">Claude Code</div>
        <pre>claude mcp add --transport http stratify {mcp_url} \\
  --header "Authorization: Bearer YOUR_KEY"</pre>
        <div class="sub" style="margin:14px 0 8px">Anything that speaks MCP over HTTP</div>
        <pre>POST {mcp_url}
Authorization: Bearer YOUR_KEY</pre>
      </div>

      <h2>Strategy book</h2>
      <div class="card">
        <div class="sub" style="margin:0 0 10px">Results that held up outside the data they
          were chosen on. Ranked by <strong>worst walk-forward fold</strong> — consistency,
          not size, because total P&amp;L is what a parameter sweep maximises by
          construction.</div>
        <div class="scroll"><table>
        <tr><th>Strategy</th><th class="n">Trades</th><th class="n">Net P&amp;L</th>
            <th class="n">Worst fold</th><th class="n">Folds</th><th class="n">Health</th>
            <th class="n">Seen</th></tr>
        {book_rows}
      </table></div></div>

      <h2>Recent backtests</h2>
      <div class="card"><div class="scroll"><table>
        <tr><th>When</th><th>Id</th><th>Spec</th></tr>
        {result_rows}
      </table></div></div>

      <h2>Call log</h2>
      <div class="card">
        <div class="sub" style="margin:0 0 10px">Every request this account has made, with
          what was asked and what it cost. Arguments are the call your model constructed —
          the words you typed to it never reach this server. Kept 30 days.</div>
        <div class="scroll"><table>
        <tr><th>When</th><th>Tool</th><th>Outcome</th><th class="n">CPU</th>
            <th class="n">Prices</th><th>Arguments</th></tr>
        {call_rows}
      </table></div></div>

      <div class="sub">Educational backtesting. Not investment advice, not a
        recommendation, and not a prediction.</div>
    """)
