"""View models for the website. Data only -- not one byte of HTML lives here.

WHY THE SPLIT. The visual design of this site is going to be replaced wholesale, more than
once. If markup and data are tangled together, every redesign is a rewrite of the logic
that decides what a page says, and every one of those rewrites is a chance to lose a
guard -- to stop masking a key, to start showing another account's rows.

So the boundary is drawn hard: this module answers "what is true, and what may this person
see", and `render.py` answers "what does it look like". A new design is a new render.py and
nothing else. Every function here returns plain dicts and lists, is pure apart from reads,
and can be asserted on in a test without parsing HTML.

The documentation is built from the protocol's own definitions -- FIELDS, ACTIONS,
STRIKE_KEYS, the tier table -- rather than retyped into prose. Docs that are typed out
separately are wrong within a month; these cannot drift without a test failing.
"""
import json
import os
import time

from engine import spec as spec_mod, strategy as strategy_mod
from . import quota, rulecard, store

MCP_PATH = "/mcp"


def mcp_url(base_url):
    """The endpoint a client should be pointed at.

    NOT the web host with /mcp appended. Every page used to derive it that way, and the
    web host does answer on /mcp -- so the wrong URL worked well enough to go unnoticed
    while quietly breaking two things: OAuth discovery, whose protected-resource metadata
    names the MCP host as the `resource` and rejects a mismatch; and the nginx zones, which
    are tuned per host. The pinned public MCP URL is authoritative; the web host is the
    fallback only for a deployment that has not set one.
    """
    pinned = os.getenv("STRATIFY_PUBLIC_MCP_URL", "").rstrip("/")
    return pinned or (base_url.rstrip("/") + MCP_PATH)


def _ago(ts):
    if not ts:
        return "—"
    secs = max(0, time.time() - float(ts))
    for cut, unit, name in ((60, 1, "second"), (3600, 60, "minute"),
                            (86400, 3600, "hour"), (86400 * 30, 86400, "day")):
        if secs < cut:
            n = int(secs // unit) or 1
            return f"{n} {name}{'s' if n != 1 else ''} ago"
    return time.strftime("%d %b %Y", time.localtime(float(ts)))


def _stamp(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts))) if ts else "—"


def identity(account):
    """Who is signed in. Never includes the session token or any key material."""
    if account is None:
        return None
    keys = account.keys() if hasattr(account, "keys") else []
    get = (lambda k, d=None: account[k] if k in keys else d)
    return {"account_id": account["account_id"], "email": account["email"],
            "name": get("display_name") or account["email"].split("@")[0],
            "avatar": get("avatar_url"), "tier": account["tier"],
            "is_admin": bool(get("is_admin", 0)),
            "member_since": _stamp(account["created_at"])}


# ------------------------------------------------------------------------- landing

# The hero has one job: say what this is before anyone scrolls. The example is real --
# it is a spec the engine accepts today, and the numbers beside it are what that spec
# actually returns, not a mock-up.
HERO_EXAMPLE = {
    "ask": "Sell a 20-delta NIFTY strangle three days out, but only when India VIX is "
           "above 15. Roll the tested side if it doubles, and stop the book after a "
           "15% drawdown.",
    "spec": {
        "legs": [{"side": "sell", "type": "CE", "strike": {"delta_near": 0.2}},
                 {"side": "sell", "type": "PE", "strike": {"delta_near": 0.2}}],
        "entry": {"cadence": "weekly", "dte": 3, "time": "09:30",
                  "when": {"vix": {"gte": 15}}},
        "rules": [{"when": {"leg_mark_mult": {"gte": 2.0, "leg": 0}},
                   "then": {"roll": {"legs": [0], "to": {"delta_near": 0.2}}}}],
        "portfolio": {"stop_after_drawdown_pct": 15},
    },
}

PILLARS = [
    {"key": "protocol",
     "title": "Describe the strategy, not a preset",
     "body": "Any number of legs, any side, unequal quantities, any expiry. Strikes named "
             "the way you name them — percent, points, premium, delta. Rules that close, "
             "roll, or open new legs mid-trade, checked every minute."},
    {"key": "why",
     "title": "Say why you took the trade",
     "body": "Gate a cycle on India VIX, on yesterday's move, on the opening gap, on "
             "20-day realised volatility, or on the day of the week. Every one of them is "
             "settled before the session opens — none can see the day's own close."},
    {"key": "honest",
     "title": "Costs, slippage and margin, priced in",
     "body": "Real one-minute option prints, brokerage and taxes per leg on real turnover, "
             "slippage by moneyness, and SPAN-calibrated margin that scales with the size "
             "of the position. The report says what is measured and what is approximated."},
    {"key": "private",
     "title": "Your data never leaves, ours never does either",
     "body": "You send a config; we run it here and send back results. No option chain is "
             "downloadable through this API, and nothing you test is visible to anyone else."},
]

# THE WHOLE PRODUCT IN THREE STEPS, written for somebody whose only prior software is a
# chat box. Each step names the one thing they do and the one thing that happens. There
# is no fourth step and no "advanced" branch: if it needs one, it belongs on /docs.
STEPS = [
    {"n": 1, "q": 0, "title": "Sign in with Google",
     "body": "The same button you have pressed a hundred times. No password to invent, "
             "no card, nothing to install."},
    {"n": 2, "q": 1, "title": "Copy your key",
     "body": "One click makes it. It is a long string that proves it's you — paste it "
             "into the chat app you already use, once."},
    {"n": 3, "q": 2, "title": "Describe a trade",
     "body": "In plain words, to Claude or ChatGPT: “sell a NIFTY strangle every week, "
             "1% out, but only when VIX is above 15.” The backtest comes back in "
             "seconds, with a report you can send to anyone."},
]

# The three sentences a first-time visitor needs, in the order they will ask them.
WHAT_IT_IS = [
    ("What is it?", "A backtester for Indian index options that you talk to. It plugs "
                    "into Claude, ChatGPT or Gemini as a tool, so you describe a "
                    "strategy in a sentence and get a costed, honest result back."),
    ("Do I need to code?", "No. If you can type a message, you can use it. The chat app "
                    "does the talking; Stratify does the arithmetic on real one-minute "
                    "prices."),
    ("What does it cost?", "Nothing. A full year of NIFTY weekly options, a hundred "
                    "backtests an hour, every feature. There is no trial that expires."),
]


def onetap(base_url, signed_in, next_path="/app"):
    """Config for the "Continue as ..." prompt, or None when it should not be shown.

    Never shown to somebody already signed in: the prompt exists to remove a click from a
    stranger, and showing it to a customer is just a box over their dashboard.
    """
    from . import oauth
    if signed_in or not oauth.enabled():
        return None
    return {"client_id": oauth.client_id(),
            "login_uri": base_url.rstrip("/") + oauth.ONETAP_PATH,
            "script": oauth.GSI_SCRIPT,
            "next": next_path}


def login_view(base_url, next_path, google_ready=True, error=None):
    """The sign-in chooser. Google is the door; the password form is the side entrance,
    folded closed, for the few addresses the admin has issued one to."""
    return {
        "page": "login",
        "google_ready": google_ready,
        "next": next_path,
        "error": error,
        "title": "Sign in to Stratify",
        "lede": "Most people sign in with Google. It is the only account we keep; "
                "Stratify never sees your Google password.",
        "password_note": "Only for addresses Stratify has issued a password to, such as "
                         "a review account. There is no way to set one yourself, and no "
                         "reset link: the person who issued it can issue it again.",
    }


def landing_view(base_url, signed_in, google_ready, error=None):
    free = quota.TIERS["free"]
    return {
        "onetap": onetap(base_url, signed_in),
        "page": "landing",
        "signed_in": signed_in,
        "google_ready": google_ready,
        "error": error,
        "hero": {
            "eyebrow": "NIFTY options · real one-minute data · free",
            "title": "Describe an options strategy. Get it backtested.",
            "lede": "Say it to Claude or ChatGPT the way you'd say it to a friend. "
                    "Stratify runs it on a year of real prices, charges it what a broker "
                    "would, and tells you honestly whether it held up.",
            "example": HERO_EXAMPLE,
            "cta": "Sign in with Google" if not signed_in else "Open your dashboard",
            "cta_href": "/auth/google?next=/app" if not signed_in else "/app",
            "secondary": {"label": "Read the docs", "href": "/docs"},
        },
        "pillars": PILLARS,
        "steps": STEPS,
        "what": WHAT_IT_IS,
        "free_tier": {
            "window": "1 year of history",
            "requests": f"{free.requests_per_hour} backtests an hour",
            "cpu": f"{free.cpu_seconds_per_hour:.0f} CPU-seconds an hour",
            "note": "Every strategy feature is available on every tier. The window of "
                    "history is the only thing a paid plan changes.",
        },
        "mcp_url": mcp_url(base_url),
    }


# ----------------------------------------------------------------------- dashboard

NAV = [
    {"key": "overview", "label": "Overview", "href": "/app"},
    {"key": "keys", "label": "API keys", "href": "/app/keys"},
    {"key": "logs", "label": "Activity", "href": "/app/logs"},
    {"key": "reports", "label": "Reports", "href": "/app/reports"},
    {"key": "docs", "label": "Docs", "href": "/docs"},
]


def _usage(account):
    tier = quota.tier_for(account)
    snap = quota.snapshot(account["account_id"], tier)
    return {
        "tier": tier.name,
        "meters": [
            # "Calls" was the honest label when every tool shared one counter. It no
            # longer is: this counter now holds backtests and built reports only, and
            # calling it "calls" would understate the allowance a user actually has.
            {"key": "requests", "label": "Backtests this hour",
             "used": snap["requests_used"], "limit": snap["requests_limit"]},
            {"key": "metadata", "label": "Other calls this hour",
             "used": snap["metadata_requests_used"],
             "limit": snap["metadata_requests_limit"]},
            {"key": "cpu", "label": "CPU-seconds this hour",
             "used": round(snap["cpu_seconds_used"], 1),
             "limit": snap["cpu_seconds_limit"]},
            {"key": "prices", "label": "Option prices released this hour",
             "used": snap["price_points_used"], "limit": snap["price_points_limit"]},
            {"key": "concurrent", "label": "Running now",
             "used": snap["concurrent_active"], "limit": snap["concurrent_limit"]},
        ],
        "exhausted": snap["exhausted"],
    }


def overview_view(account, base_url, message=None):
    keys = [k for k in store.list_keys(account["account_id"]) if not k["revoked_at"]]
    calls = store.recent_calls(account["account_id"], limit=5)
    results = store.recent_results_for_account(account["account_id"], limit=5)
    return {
        "page": "overview", "nav": NAV, "you": identity(account), "message": message,
        "usage": _usage(account),
        "has_key": bool(keys),
        "key_count": len(keys),
        "mcp_url": mcp_url(base_url),
        "connect": connect_snippets(mcp_url(base_url)),
        "recent_calls": [_call_row(c) for c in calls],
        "recent_reports": [_report_row(r, base_url) for r in results],
        "empty": not keys and not calls,
    }


def keys_view(account, new_key=None, message=None):
    rows = []
    for k in store.list_keys(account["account_id"]):
        rows.append({"key_id": k["key_id"], "last4": k["last4"],
                     "created": _stamp(k["created_at"]),
                     "created_ago": _ago(k["created_at"]),
                     "revoked": bool(k["revoked_at"]),
                     "revoked_at": _stamp(k["revoked_at"]) if k["revoked_at"] else None})
    live = [r for r in rows if not r["revoked"]]
    # CONNECTED APPS BELONG NEXT TO KEYS, because they are the same thing to a user: a
    # credential something else holds on their behalf. A grant nobody can see is a grant
    # nobody can withdraw, which is the half of OAuth that usually goes missing.
    connections = [{"client_id": c["client_id"],
                    "name": c["client_name"] or c["client_id"],
                    "uri": c["client_uri"],
                    "last": _stamp(c["last_at"]), "last_ago": _ago(c["last_at"])}
                   for c in store.oauth_connections(account["account_id"])]
    return {
        "page": "keys", "nav": NAV, "you": identity(account), "message": message,
        "connections": connections,
        # Shown EXACTLY once, on the response that mints it. It is not stored in a
        # readable form anywhere, so there is no second chance and the page has to say so.
        "new_key": new_key,
        "keys": rows,
        "live_count": len(live),
        "limit": quota.MAX_ACTIVE_KEYS if hasattr(quota, "MAX_ACTIVE_KEYS") else None,
    }


def _call_row(c):
    keys = c.keys() if hasattr(c, "keys") else []
    get = (lambda k, d=None: c[k] if k in keys else d)
    return {"ts": _stamp(get("ts")), "ago": _ago(get("ts")),
            "tool": get("tool") or get("method") or "—",
            "outcome": get("outcome") or "ok",
            "cpu": round(float(get("cpu_seconds") or 0), 2),
            "prices": int(get("price_points") or 0),
            "detail": (get("refusal") or "")[:300],
            "backtest_id": get("backtest_id")}


def logs_view(account, limit=100):
    calls = store.recent_calls(account["account_id"], limit=limit)
    rows = [_call_row(c) for c in calls]
    counts = {}
    for r in rows:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    return {
        "page": "logs", "nav": NAV, "you": identity(account),
        "rows": rows,
        "totals": {"calls": len(rows),
                   "cpu": round(sum(r["cpu"] for r in rows), 1),
                   "prices": sum(r["prices"] for r in rows),
                   "by_outcome": sorted(counts.items())},
        # Precise on purpose. Protocol handshakes (initialize, tools/list) are not
        # audited -- they are unbilled and carry nothing worth keeping -- so claiming
        # "every call" would be a promise this page cannot keep.
        "note": "Every TOOL CALL this account has made, with what it asked for and what "
                "came back — including the ones that were refused. A log that only holds "
                "the calls that worked cannot answer the one question you ask a log. "
                "Protocol handshakes are not listed; they are unbilled and carry nothing.",
    }


def _report_row(r, base_url):
    keys = r.keys() if hasattr(r, "keys") else []
    get = (lambda k, d=None: r[k] if k in keys else d)
    spec = {}
    try:
        spec = json.loads(get("spec_json") or "{}")
    except ValueError:
        pass
    # The NAME comes from the same card the report itself opens with, so a row and the
    # page it links to agree. "2 legs, weekly, 1 rule" described four different rows.
    try:
        card = rulecard.describe(spec)
        name = None if card.get("error") else card["title"]
    except Exception:                                              # noqa: BLE001
        name = None
    return {
        "backtest_id": get("backtest_id"),
        "created": _stamp(get("created_at")),
        "ago": _ago(get("created_at")),
        "name": spec.get("name") or name or _describe_spec(spec),
        "summary": _describe_spec(spec),
        "url": f"{base_url.rstrip('/')}/r/{get('report_token')}" if get("report_token") else None,
    }


def _describe_spec(spec):
    """A one-line English gloss, from the spec itself rather than from a stored string."""
    if not isinstance(spec, dict):
        return "—"
    if "legs" in spec:
        n = len(spec.get("legs") or [])
        entry = spec.get("entry") or {}
        cadence = entry.get("cadence", "weekly")
        bits = [f"{n} leg{'s' if n != 1 else ''}", cadence]
        if spec.get("rules"):
            bits.append(f"{len(spec['rules'])} rule"
                        f"{'s' if len(spec['rules']) != 1 else ''}")
        if entry.get("when"):
            bits.append("gated entry")
        return ", ".join(bits)
    # Structure and cadence alone are NOT an identity: three weekly iron condors at
    # different offsets all rendered as "iron_condor, weekly", so the reports page listed
    # rows nobody could tell apart. Include the parameters that actually distinguish them.
    bits = [str(spec["structure"])] if spec.get("structure") else []
    pr = spec.get("params") or {}
    if pr.get("pct_offset") is not None:
        bits.append(f"{pr['pct_offset']}% out")
    if pr.get("pct_width") is not None:
        bits.append(f"{pr['pct_width']}% wide")
    if pr.get("direction"):
        bits.append(str(pr["direction"]))
    if pr.get("entry_dte") is not None:
        bits.append(f"{pr['entry_dte']} DTE")
    if spec.get("entry_time"):
        bits.append(str(spec["entry_time"]))
    if spec.get("exit_time"):
        bits.append(f"out {spec['exit_time']}")
    if spec.get("cadence"):
        bits.append(str(spec["cadence"]))
    if spec.get("gate") and spec["gate"] != "always":
        bits.append(f"gate: {spec['gate']}")
    return ", ".join(bits) or "—"


def reports_view(account, base_url, limit=50):
    rows = [_report_row(r, base_url)
            for r in store.recent_results_for_account(account["account_id"], limit=limit)]
    return {"page": "reports", "nav": NAV, "you": identity(account), "rows": rows,
            "note": "Every backtest this account has run keeps its own permanent link. "
                    "Reports are private to this account."}


# ---------------------------------------------------------------------------- docs

def connect_snippets(mcp_url):
    """How to point a client at this server. The URL is the deployment's own."""
    return [
        {"key": "claude-code", "label": "Claude Code",
         "lang": "bash",
         "body": f"claude mcp add --transport http stratify {mcp_url} \\\n"
                 f"  --header \"Authorization: Bearer sk_live_...\""},
        {"key": "claude-desktop", "label": "Claude Desktop / claude.ai",
         "lang": "json",
         "body": json.dumps({"mcpServers": {"stratify": {
             "type": "http", "url": mcp_url,
             "headers": {"Authorization": "Bearer sk_live_..."}}}}, indent=2)},
        # OpenCode, Gemini CLI and Codex all speak streamable HTTP with a caller-supplied
        # header, which is exactly what this server authenticates with -- so they work
        # today, with no OAuth and no shim. They are listed explicitly because a user who
        # does not see their client named tends to assume it is unsupported.
        {"key": "opencode", "label": "OpenCode",
         "lang": "json",
         "body": json.dumps({"$schema": "https://opencode.ai/config.json",
                             "mcp": {"stratify": {
                                 "type": "remote", "url": mcp_url, "enabled": True,
                                 "headers": {
                                     "Authorization": "Bearer {env:STRATIFY_API_KEY}"}}}},
                            indent=2)},
        {"key": "gemini-cli", "label": "Gemini CLI",
         "lang": "json",
         "body": json.dumps({"mcpServers": {"stratify": {
             "httpUrl": mcp_url,
             "headers": {"Authorization": "Bearer sk_live_..."},
             "timeout": 120000}}}, indent=2)},
        {"key": "codex", "label": "Codex",
         "lang": "toml",
         "body": ('[mcp_servers.stratify]\n'
                  f'url = "{mcp_url}"\n'
                  'http_headers = { Authorization = "Bearer sk_live_..." }\n')},
        # THE BROWSER ASSISTANTS TAKE A URL AND NOTHING ELSE. ChatGPT, the Gemini web app
        # and Claude's connector UI all refuse a bearer key and run OAuth instead, so the
        # "config" for them is the URL plus a sign-in click. Listed here because a user
        # looking for their client and finding only header snippets concludes it is
        # unsupported.
        {"key": "browser", "label": "ChatGPT · Gemini · Claude (web)",
         "lang": "text",
         "body": (f"{mcp_url}\n\n"
                  "Paste that URL as a custom connector and sign in when prompted — no "
                  "API key needed, and no key to leak.\n\n"
                  "  ChatGPT   Settings > Apps > Advanced > Developer mode, then add it\n"
                  "  Gemini    Settings & help > Connected Apps > custom app\n"
                  "  Claude    Settings > Connectors > Add custom connector\n\n"
                  "You can disconnect it again from your Stratify dashboard at any time.")},
        {"key": "curl", "label": "Anything else",
         "lang": "bash",
         "body": f"curl -s {mcp_url} \\\n"
                 f"  -H 'Authorization: Bearer sk_live_...' \\\n"
                 f"  -H 'Content-Type: application/json' \\\n"
                 f"  -d '{{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}}'"},
    ]


# Worked examples. Each one is a spec the engine accepts -- a test runs every one of them
# through the parser, so a doc page cannot show a strategy that would be refused.
EXAMPLES = [
    {"key": "condor", "title": "A weekly iron condor",
     "ask": "Sell a 1%-wide iron condor every week, three days before expiry.",
     "spec": {"legs": [{"side": "sell", "type": "CE", "strike": {"pct_offset": 1.0}},
                       {"side": "buy", "type": "CE", "strike": {"pct_offset": 2.0}},
                       {"side": "sell", "type": "PE", "strike": {"pct_offset": -1.0}},
                       {"side": "buy", "type": "PE", "strike": {"pct_offset": -2.0}}],
              "entry": {"cadence": "weekly", "dte": 3, "time": "09:30"}}},
    {"key": "gated", "title": "Only when volatility is worth selling",
     "ask": "The same idea, but skip the week unless India VIX is above 15.",
     "spec": {"legs": [{"side": "sell", "type": "CE", "strike": {"delta_near": 0.2}},
                       {"side": "sell", "type": "PE", "strike": {"delta_near": 0.2}}],
              "entry": {"cadence": "weekly", "dte": 3, "time": "09:30",
                        "when": {"vix": {"gte": 15}}}}},
    {"key": "managed", "title": "Managed while it is open",
     "ask": "Take profit at 60% of the credit, roll the tested side if it doubles, and "
            "stop the book after three losers.",
     "spec": {"legs": [{"side": "sell", "type": "CE", "strike": {"pct_offset": 1.2}},
                       {"side": "sell", "type": "PE", "strike": {"pct_offset": -1.2}}],
              "entry": {"cadence": "weekly", "dte": 4, "time": "09:20"},
              "rules": [{"when": {"pnl_pct_of_credit": {"gte": 0.6}}, "then": "close"},
                        {"when": {"leg_mark_mult": {"gte": 2.0, "leg": 0}},
                         "then": {"roll": {"legs": [0], "to": {"pct_offset": 2.0}}},
                         "max_times": 2}],
              "max_adjustments": 3,
              "portfolio": {"stop_after_losses": 3, "resume_after_days": 30}}},
    {"key": "intraday", "title": "An intraday 0-DTE trade",
     "ask": "Sell the at-the-money straddle on expiry morning and square off at 15:10, "
            "with a 25% target and a 60% stop.",
     "spec": {"legs": [{"side": "sell", "type": "CE", "strike": "atm"},
                       {"side": "sell", "type": "PE", "strike": "atm"}],
              "entry": {"cadence": "daily", "max_dte": 0, "time": "09:16"},
              "rules": [{"when": {"pnl_pct_of_credit": {"gte": 0.25}}, "then": "close"},
                        {"when": {"pnl_pct_of_credit": {"lte": -0.6}}, "then": "close"}],
              "exit": {"time": "15:10"}}},
    {"key": "calendar", "title": "Two expiries at once",
     "ask": "Sell this week's at-the-money call and buy next week's against it.",
     "spec": {"legs": [{"side": "sell", "type": "CE", "strike": "atm"},
                       {"side": "buy", "type": "CE", "strike": "atm",
                        "expiry": "next"}],
              "entry": {"cadence": "weekly", "dte": 4, "time": "09:30"}}},
]


def _field_rows():
    """The rule vocabulary, straight out of the protocol. Grouped the way a person thinks
    about it rather than the way the dict is ordered."""
    groups = [
        ("The position", ["pnl_pts", "pnl_rupees", "pnl_pct_of_credit", "pnl_pct_of_max",
                          "combined_premium", "credit_kept_pct"]),
        ("A single leg", sorted(strategy_mod.LEG_FIELDS)),
        ("The market", sorted(strategy_mod.MARKET_FIELDS)),
        ("The underlying", ["spot", "spot_move_pct", "spot_move_pts"]),
        ("The clock", ["time", "minutes_held", "dte"]),
        ("Path", ["drawdown_from_peak", "runup_from_trough", "adjustments_done"]),
    ]
    out, seen = [], set()
    for title, names in groups:
        rows = [{"name": n, "doc": strategy_mod.FIELDS[n]}
                for n in names if n in strategy_mod.FIELDS]
        if title == "The market":
            # The indicator family is parametric, so its documentation is a pattern
            # rather than a fixed name. Listed with the market fields because that is
            # what it is: a fact about the index, settled before the open.
            rows += [{"name": n, "doc": d} for n, d in strategy_mod.INDICATOR_DOC.items()]
        seen.update(r["name"] for r in rows)
        out.append({"title": title, "rows": rows})
    # Anything added to FIELDS and not placed in a group above still gets documented --
    # the failure mode of a hand-maintained list is a field nobody can discover.
    rest = [{"name": n, "doc": d} for n, d in sorted(strategy_mod.FIELDS.items())
            if n not in seen]
    if rest:
        out.append({"title": "Other", "rows": rest})
    return out


def docs_view(base_url, signed_in=False):
    endpoint = mcp_url(base_url)
    return {
        "page": "docs", "nav": NAV, "signed_in": signed_in,
        "mcp_url": endpoint,
        "connect": connect_snippets(endpoint),
        "sections": [
            {"key": "start", "title": "Getting started"},
            {"key": "shape", "title": "How a strategy is written"},
            {"key": "legs", "title": "Legs"},
            {"key": "entry", "title": "Entry"},
            {"key": "rules", "title": "Rules"},
            {"key": "portfolio", "title": "Book rules"},
            {"key": "fields", "title": "Field reference"},
            {"key": "examples", "title": "Worked examples"},
            {"key": "limits", "title": "Limits and honesty"},
        ],
        "shape": {
            "body": "A strategy is three things: what to open (`legs`), when to open it "
                    "(`entry`), and what to do about it while it is open (`rules`). "
                    "Everything else is optional. It is JSON, drawn from a closed "
                    "vocabulary — nothing is evaluated as code, so a strategy is data you "
                    "can store, diff and share.",
            "skeleton": {
                "name": "my strategy",
                "legs": ["..."],
                "entry": {"cadence": "weekly | daily | monthly", "dte": 3,
                          "time": "09:30", "when": "optional condition"},
                "rules": ["optional, up to 24"],
                "exit": {"time": "optional hard square-off", "when": "optional condition"},
                "max_adjustments": 4,
                "portfolio": "optional rules over the sequence of trades",
            },
        },
        "legs": {
            "body": "Up to 12. Each names a side, a type, an optional quantity, an "
                    "optional expiry, and a strike. Leg order defines the indices that "
                    "rules refer to.",
            "sides": list(strategy_mod.SIDES),
            "types": list(strategy_mod.TYPES),
            "expiries": [
                {"name": "near", "doc": "the nearest expiry at entry (the default)"},
                {"name": "next", "doc": "the one after it — this is how a calendar is written"},
                {"name": "far", "doc": "the one after that"}],
            "strikes": [
                {"name": "atm", "doc": "the at-the-money strike"},
                {"name": "24000", "doc": "an absolute strike, as a bare number"},
                {"name": '{"pct_offset": 1.5}', "doc": "1.5% from spot"},
                {"name": '{"points_offset": 300}', "doc": "300 index points from spot"},
                {"name": '{"premium_near": 50}', "doc": "the strike trading nearest ₹50"},
                {"name": '{"delta_near": 0.2}', "doc": "the 20-delta strike, solved from "
                                                       "the real traded price"},
                {"name": '{"from_leg": {"leg": 0, "pct": 1.0}}',
                 "doc": "1% beyond another leg's strike"}],
        },
        "entry": {
            "body": "One entry per cycle. `when` is the gate — the reason for taking the "
                    "trade — and it is the only place the market fields below mean "
                    "anything, since nothing else exists yet.",
            "cadences": [
                {"name": "weekly", "doc": "one entry per weekly expiry, `dte` days out "
                                          "(default 4)"},
                {"name": "monthly", "doc": "one per monthly expiry — the last of its "
                                           "calendar month (default `dte` 21)"},
                {"name": "daily", "doc": "one per session; `max_dte` skips sessions "
                                         "further than that from expiry"}],
            "note": "Entry and exit may be ANY minute of the session, not a fixed grid.",
        },
        "rules": {
            "body": "Checked every minute while the position is open, in order; the first "
                    "match fires. A rule is a condition and an action.",
            "comparators": list(strategy_mod.COMPARATORS),
            "combinators": ["all", "any", "not"],
            "actions": [
                {"name": '"close"', "doc": "end the trade"},
                {"name": '{"close_legs": [0, 1]}', "doc": "close some legs, keep the rest"},
                {"name": '{"open": [leg, ...]}', "doc": "add legs to the live position"},
                {"name": '{"roll": {"legs": [0], "to": strike}}',
                 "doc": "close those legs and reopen them at a new strike"},
                {"name": '{"close_and_open": {"close": [0], "open": [leg]}}',
                 "doc": "both at once — how a position changes shape mid-trade"}],
            "caps": {"rules": strategy_mod.MAX_RULES,
                     "legs": strategy_mod.MAX_LEGS,
                     "adjustments": strategy_mod.MAX_ADJUSTMENTS_CAP},
        },
        "portfolio": {
            "body": "Rules over the SEQUENCE of trades, which no per-trade condition can "
                    "express because they depend on trades that have already closed.",
            "keys": sorted(strategy_mod.PORTFOLIO_KEYS),
        },
        "fields": _field_rows(),
        "examples": EXAMPLES,
        "tiers": [
            {"name": t.name,
             "requests": t.requests_per_hour, "cpu": t.cpu_seconds_per_hour,
             "metadata": t.metadata_requests_per_hour,
             "concurrent": t.max_concurrent,
             "window": _tier_window(t.name)}
            for t in (quota.TIERS["free"], quota.TIERS["plus"], quota.TIERS["pro"])],
        "honesty": [
            "Option prices are real one-minute prints. Nothing is modelled — Black-Scholes "
            "is run backwards only to turn 'the 20-delta strike' into a strike.",
            "Brokerage and taxes are charged per leg on real turnover. Slippage is charged "
            "by moneyness, and twice on a trade that is closed rather than settled.",
            "Naked margin is a Fyers SPAN ratio calibrated today and applied to the past, "
            "so return on margin for an uncovered short is optimistic by an unknown "
            "amount. Every response that uses it says so.",
            "Market gates read only what was settled before the session opened. None of "
            "them can see the day's own close.",
            "A cycle whose option chain is too thin to locate the money is skipped, and "
            "the count of skipped cycles is reported with the result.",
        ],
    }


def _tier_window(name):
    from engine import spec as spec_mod
    a, b = spec_mod.window_for(name)
    years = round((b - a).days / 365.25)
    return f"{years} year{'s' if years != 1 else ''} ({a} to {b})"


# ------------------------------------------------------------------------- legal
#
# WRITTEN FROM THE CODE, NOT FROM A TEMPLATE. Every claim below is one this repository can
# be checked against: the retention windows are store.purge()'s own defaults, the hashing
# is store._hash and store.hash_ip, and "we never see your prompts" is true because the
# MCP boundary carries tool arguments and nothing else. If a claim here stops matching the
# code, the code is not the thing that should win -- the promise was made to somebody.
#
# Google's OAuth consent screen requires a privacy policy and a terms link on a domain the
# operator owns before an app can be published externally. These are those pages.

LAST_UPDATED = "30 August 2026"

# THE PUBLIC SUPPORT ADDRESS, in one place because it is published on three pages and a
# directory listing, and an address that disagrees with itself across them is worse than
# none. It is read from the environment rather than hard-coded because it cannot be
# switched on until the domain can actually receive mail: aeon-labs.site has no MX record
# today, so publishing an address there would bounce silently -- which on a privacy policy
# is worse than saying "use the feedback tool", since a reader would believe they had
# written to someone.
#
# TO TURN IT ON: add MX records for the domain, then set STRATIFY_SUPPORT_EMAIL in the
# service environment. Every page below picks it up with no further change.
SUPPORT_EMAIL = os.getenv("STRATIFY_SUPPORT_EMAIL", "").strip()



def _contact_line(subject):
    """Honest either way: it names an address when one exists, and does not invent a
    'contact page' when one does not."""
    if SUPPORT_EMAIL:
        return (f"{subject} Write to {SUPPORT_EMAIL}, or use the feedback tool inside the "
                f"product — it reaches the same place and carries the context of what you "
                f"were doing.")
    return (f"{subject} Use the feedback tool inside the product (the submit_feedback "
            f"tool, or the feedback form in your dashboard). It is read directly by the "
            f"people who build Stratify, and it carries the context of what you were "
            f"doing, which an email cannot.")


# A directory reviewer, and anyone reading the privacy policy, expects one page that says
# how to reach a human. Serving it only when an address exists would leave a dead link in
# the footer, so it is always served and simply describes whichever route is real.
CONTACT = {
    "title": "Contact",
    "updated": LAST_UPDATED,
    "intro": ("Stratify is built and run by a small team. There is no ticket queue and no "
              "outsourced support desk — a message here reaches the people who wrote the "
              "code."),
    "sections": [
        {"h": "The fastest route",
         "items": [
             "Use the `submit_feedback` tool from inside your MCP client, or the feedback "
             "form in your dashboard. It automatically carries the backtest id, the spec "
             "you ran and the error you saw, which is the context that makes a bug "
             "fixable on the first reply rather than the third.",
             "You can check what you have filed, and where it stands, with the "
             "`my_feedback` tool.",
         ]},
        {"h": "Security",
         "items": [
             "If you have found a vulnerability, please report it through the feedback "
             "tool and mark it as a security issue rather than posting it publicly. We "
             "will acknowledge it and tell you when it is fixed.",
             "Please do not run load or penetration tests against the live service. Ask "
             "first and we will arrange it.",
         ]},
        {"h": "Account and data requests",
         "items": [
             "Deletion of your account and everything attached to it, or a copy of what "
             "we hold about you: ask through the feedback tool and it will be done. See "
             "the privacy page for what is held and for how long.",
         ]},
    ],
    "contact": _contact_line("General questions."),
}


PRIVACY = {
    "title": "Privacy",
    "updated": LAST_UPDATED,
    "intro": "Stratify runs options backtests. This page says exactly what is kept, for "
             "how long, and what leaves the server. It describes the software in this "
             "repository rather than a category of service.",
    "sections": [
        {"h": "What we collect when you sign in",
         "items": [
             "Your Google account's subject identifier — a stable, opaque id. This is what "
             "identifies your account, not your email, so changing your address does not "
             "move or lose it.",
             "Your email address, display name and avatar URL, as Google reports them. "
             "The email is used to identify the account to you and to contact you about "
             "the service.",
             "Nothing else. We request the `openid email profile` scopes only, so we "
             "cannot read your mail, files, calendar or contacts even if we wanted to.",
         ]},
        {"h": "What we collect when you use it",
         "items": [
             "A hash of each API key. Keys are hashed with scrypt and a server-side "
             "pepper before storage, so a key cannot be recovered from our database — "
             "including by us. That is why a key is shown once and never again.",
             "If you connect an application — ChatGPT, Claude, Gemini or anything else "
             "that signs in through OAuth — a hash of the tokens issued to it, the name "
             "it registered under, and when it last connected. Hashed the same way as an "
             "API key, and deleted when you disconnect it.",
             "Per-call metering: CPU-seconds and the number of option prices returned.",
             "A call log: which tool you called, the arguments you sent, whether it "
             "succeeded, and the reason if it was refused.",
             "Your backtest specifications and their results, so your reports keep working.",
             "A peppered hash of your IP address at signup, used to throttle bulk account "
             "creation. The raw address is never written down.",
         ]},
        {"h": "What we never collect",
         "items": [
             "Your conversations. Stratify is an MCP server: it receives the tool "
             "arguments your client sends and nothing of the discussion around them.",
             "Payment card details. Nothing on this site takes a card.",
             "Analytics, advertising or tracking of any kind. There is no third-party "
             "script on this website. The only cookies are your sign-in session and a "
             "ten-minute cookie used to complete the Google handshake.",
         ]},
        {"h": "How long it is kept",
         "items": [
             "Usage records, the call log, and the bodies of your specifications and "
             "results: 30 days, then deleted or replaced with a tombstone. Report links "
             "keep resolving afterwards and say the content was purged on schedule.",
             "Signup records: 7 days.",
             "Your account, and the hash of each key: until you ask us to delete them.",
             "A shortlist of strategies you chose to keep: until you remove it. It is "
             "small, it is the durable artefact, and a shortlist that silently forgets "
             "things is worse than none.",
         ]},
        {"h": "Who it is shared with",
         "items": [
             "Nobody. Your specifications, results and reports are private to your "
             "account and are not sold, licensed, syndicated or used to train anything.",
             "Google sees that you signed in, because you signed in with Google. It does "
             "not see what you do here.",
             "We will disclose data if a valid legal order compels it, and we will tell "
             "you unless we are forbidden from doing so.",
         ]},
        {"h": "Your control",
         "items": [
             "Disconnect any connected application from your dashboard. Access ends "
             "immediately, including any refresh token it holds — it cannot quietly mint "
             "itself a new session afterwards.",
             "Revoke any API key at any time from your dashboard; it stops working "
             "immediately.",
             "Sign out to invalidate your browser session server-side, not just locally.",
             "Ask us to delete your account and everything attached to it. Write to the "
             "address below and it will be done.",
             "Ask for a copy of what we hold about you and we will send it.",
         ]},
        {"h": "Where it lives",
         "items": [
             "On servers we operate. Market data and account records are not replicated "
             "to third-party platforms.",
             "Transport is HTTPS throughout. Session cookies are http-only, secure, and "
             "not readable by any script.",
         ]},
    ],
    "contact": _contact_line("Questions, corrections, or a deletion request."),
}

TERMS = {
    "title": "Terms",
    "updated": LAST_UPDATED,
    "intro": "Plain terms for using Stratify. If any of this is unacceptable to you, "
             "please do not use the service.",
    "sections": [
        {"h": "What this is",
         "items": [
             "A historical simulator. It replays options strategies against recorded "
             "one-minute market data and reports what would have happened, net of "
             "modelled costs.",
             "It is NOT investment advice, a recommendation, a solicitation, or a "
             "forecast. Nobody here knows your circumstances and nothing here is tailored "
             "to them.",
             "Past behaviour of a strategy does not predict its future behaviour. A good "
             "backtest is evidence about the past and nothing more.",
         ]},
        {"h": "Accuracy, and its limits",
         "items": [
             "We take accuracy seriously and we say where the numbers are approximate. "
             "Costs, slippage and margin are modelled, not observed: a real fill is not a "
             "historical print, and margin for an uncovered short uses a broker ratio "
             "calibrated today and applied to the past.",
             "The service reports what it skipped and why, rather than quietly omitting "
             "it. Read those notes; they are part of the result.",
             "We do not warrant that results are free of error. If you find one, tell us "
             "— that is what the feedback tool is for.",
         ]},
        {"h": "Your account",
         "items": [
             "You are responsible for your API keys. Anything holding one can spend your "
             "quota. Revoke a key you no longer control.",
             "One person, one account. Creating accounts to evade rate limits is not "
             "permitted.",
             "Do not use the service to reconstruct or redistribute the underlying market "
             "data. Results are yours; the data behind them is licensed to us and stays "
             "here.",
         ]},
        {"h": "Fair use",
         "items": [
             "Rate limits and quotas apply and are published in the documentation. They "
             "exist so one caller cannot degrade the service for everyone else.",
             "Do not attempt to disrupt, overload, or gain unauthorised access to the "
             "service or to other accounts.",
             "We may suspend an account that is damaging the service, and will say why.",
         ]},
        {"h": "Liability",
         "items": [
             "The service is provided as is, without warranty of any kind.",
             "You are solely responsible for any trading decision you make. We accept no "
             "liability for trading losses, lost profits, or any consequential loss "
             "arising from use of the service.",
             "Nothing here limits liability that cannot lawfully be limited.",
         ]},
        {"h": "Changes and ending",
         "items": [
             "You may stop using the service at any time and ask for your data to be "
             "deleted.",
             "We may change these terms. Material changes will be dated here, and the "
             "date above tells you when they last moved.",
             "We may discontinue the service. If we do, we will give notice and time to "
             "export anything you want to keep.",
         ]},
    ],
    "contact": _contact_line("Questions about these terms."),
}


def legal_view(which, signed_in=False):
    """A static document. Kept in site.py with everything else the pages say, so a
    redesign of render.py cannot silently drop a clause."""
    doc = {"privacy": PRIVACY, "terms": TERMS, "contact": CONTACT}[which]
    return {"page": which, "nav": NAV, "signed_in": signed_in, "doc": doc,
            "other": ("terms", "Terms") if which == "privacy" else ("privacy", "Privacy")}


# ---------------------------------------------------------------- OAuth consent

def consent_view(account, ctx, params, offline=False):
    """What the consent screen shows.

    THE HARD PART IS NAMING WHO IS ASKING. A registered client supplies its own
    client_name, and a CIMD client's document is entirely self-asserted -- either can claim
    to be anything. So for CIMD the identity shown is the HOST of the client_id URL, which
    is the one fact the client cannot lie about, and the name it gave itself is shown only
    as a secondary label. A consent screen that presents a self-chosen name as though it
    were verified is a phishing surface, not a security control.
    """
    client = ctx["client"]
    grants = [
        "Run backtests on your account, using your quota",
        "Read the results and reports this account has produced",
    ]
    if offline:
        grants.append("Stay connected without asking again, until you disconnect it")
    return {
        "page": "consent",
        "you": identity(account),
        "client_display": client.get("display") or client["client_id"],
        "client_claimed_name": (client.get("client_name")
                                if client.get("is_cimd") else None),
        "verified": not client.get("is_cimd"),
        "redirect_host": _host_of(ctx["redirect_uri"]),
        "grants": grants,
        "cannot": [
            "It cannot see your API keys — those are hashed and are never readable, "
            "including by us.",
            "It cannot change your tier, your billing, or delete your account.",
            "It cannot reach any market data directly. Results are computed here and only "
            "results are returned.",
        ],
        "scope": ctx["scope"],
        "offline": offline,
        # Echoed straight back on the POST so the decision applies to the SAME request that
        # was displayed. Re-deriving it from anything else would let the parameters change
        # between what the user read and what they approved.
        "fields": {k: v for k, v in params.items() if k != "decision"},
    }


def _host_of(url):
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc or url
    except Exception:                                              # noqa: BLE001
        return url


# ----------------------------------------------------------------------- explore
#
# What you can actually do. Every list here is drawn from the engine's own vocabulary --
# STRIKE_KEYS, ACTIONS, MARKET_FIELDS, PORTFOLIO_KEYS -- so the page cannot advertise a
# capability the parser would refuse. A test asserts that.

EXPLORE = {
    "title": "Everything you can do",
    "lede": ("You describe a trade the way you would to a colleague. Stratify turns it "
             "into a simulation over real one-minute option prices, charges it what a "
             "broker would, and tells you honestly whether it held up."),
    "groups": [
        {"q": 0, "key": "describe", "title": "Describe any position",
         "sub": "Not a menu of five presets. Any number of legs, any way of naming them.",
         "items": [
             ("Any legs", "Sell or buy, calls or puts, unequal quantities, one expiry or "
                          "two. A single bought put or a six-leg double condor are the "
                          "same sentence to the engine."),
             ("Strikes named your way", "By percent from spot, by points, at the money, "
                          "a fixed strike, by premium (“the call trading near ₹50”), "
                          "by delta, or relative to another leg."),
             ("Any entry", "Weekly on the day you choose before expiry, or daily on the "
                          "nearest one. At the open, at 09:30, 11:00, 14:00 — or the close."),
             ("Or the quick way", "Five common shapes — strangle, iron condor, iron fly, "
                          "credit spread, long option — as one-line presets when you "
                          "do not need the detail."),
         ]},
        {"q": 1, "key": "manage", "title": "Manage it while it is open",
         "sub": "Rules that watch the position every minute and act on it.",
         "items": [
             ("Take profit and stop", "On the percent of credit kept, on points, on rupees, "
                          "on the move in the index, on minutes held, at a clock time."),
             ("Roll and repair", "Roll a tested leg further out. Close one side. Add a "
                          "hedge. Close everything and re-open at new strikes. Up to a "
                          "cap you set."),
             ("Book-level rules", "Stand down after three losers. Stop for the month after "
                          "a drawdown. Skip the next trade after a loss. Resume after a "
                          "number of days."),
             ("Trail", "Move the stop as the trade goes your way, on run-up from the "
                          "trough or drawdown from the peak."),
         ]},
        {"q": 2, "key": "when", "title": "Say when to trade",
         "sub": "Every condition is settled before the session opens — none can see the day's own close.",
         "items": [
             ("Volatility", "India VIX, its change, and 20-day realised volatility. "
                          "“Only sell when VIX is above 15.”"),
             ("Yesterday's move", "The previous day's move, the opening gap. "
                          "“Only after a 1% down day.”"),
             ("Calendar", "Day of the week, days to expiry. “Only Thursdays, "
                          "only 0 to 2 days out.”"),
             ("Indicators on the index", "RSI, simple and exponential averages, and "
                          "crossovers — any window from 2 to 250 days, all computed on "
                          "yesterday's close. “Only when RSI(14) is under 30”, “only "
                          "while the 9 is over the 21”."),
             ("Directional bias", "EMA trend, RSI momentum, MACD, Bollinger and Donchian "
                          "breakouts, ATR, rate of change — pick a side, or stay "
                          "neutral."),
         ]},
        {"q": 3, "key": "back", "title": "What comes back",
         "sub": "Numbers a broker statement would agree with, and an honest reading of them.",
         "items": [
             ("Real costs", "Brokerage per leg, STT on the sell side, exchange and SEBI "
                          "fees, GST, stamp duty. Slippage by moneyness. Margin from a "
                          "SPAN calibration."),
             ("The honesty panel", "A chronological 70/30 split, three walk-forward folds, "
                          "a bootstrap interval, and a Sharpe deflated for how many "
                          "variants you have already tried. No ratio below 30 trades."),
             ("A report you can share", "One link: the rules, what it did to your capital "
                          "with the sizing live in the page, a shaded calendar of every "
                          "trade, the equity curve, and the evidence. Works on a phone."),
             ("A replay", "Step through every entry and exit on the index, day by day, "
                          "at the speed you choose."),
         ]},
    ],
    "clients": [
        ("Claude", "Desktop, web and Claude Code"),
        ("ChatGPT", "Web, in developer mode"),
        ("Gemini", "Web app and the CLI"),
        ("Cursor · OpenCode · Codex", "Anything that speaks MCP"),
    ],
}


def explore_view(base_url, signed_in=False):
    return {"page": "explore", "signed_in": signed_in, "explore": EXPLORE,
            "cta_href": "/app" if signed_in else "/auth/google?next=/app",
            "cta": "Open your dashboard" if signed_in else "Start free with Google"}


# ----------------------------------------------------------------------- pricing
#
# THE PREMIUM TIER HAS NO PRICE, and the page says so rather than inventing one. What it
# has is a description and a list, and the only honest call to action for something that
# does not exist yet is "tell me when it does".

PREMIUM_PROMISE = [
    ("Every index", "NIFTY, BANKNIFTY and SENSEX weeklies, not NIFTY alone."),
    ("Every stock's options", "The full stock F&O universe, at the same one-minute "
                              "resolution."),
    ("Ten years, not one", "History back to 2016–2018 depending on the instrument, "
                           "so a strategy meets 2020 and 2022 before your money does."),
    ("No hourly ceiling", "Unlimited backtests. Run the grid."),
    ("Everything the free tier has", "Same engine, same honesty panel, same reports. "
                                     "Only the data changes."),
]


def pricing_view(base_url, account=None, message=None, joined=False):
    free = quota.TIERS["free"]
    win_from, win_to = spec_mod.window_for("free")
    on_list = bool(account and store.on_waitlist(account_id=account["account_id"]))
    return {
        "page": "pricing",
        "signed_in": account is not None,
        "you": identity(account) if account else None,
        "email": account["email"] if account else None,
        "message": message,
        "joined": joined,
        "on_list": on_list or joined,
        "free": {
            "name": "Free",
            "price": "₹0",
            "sub": "No card. No trial clock. Free stays free.",
            "items": [
                f"NIFTY weekly options, one-minute prices",
                f"{_span_words(win_from, win_to)} of history ({win_from} to {win_to})",
                f"{free.requests_per_hour} backtests an hour, "
                f"{free.metadata_requests_per_hour:,} other calls",
                "Every strategy feature: any legs, any rules, every signal",
                "The honesty panel, shareable reports, the replay",
                "Works in Claude, ChatGPT, Gemini, Cursor and every MCP client",
            ],
            "cta": "Open your dashboard" if account else "Start free with Google",
            "cta_href": "/app" if account else "/auth/google?next=/app",
        },
        "premium": {
            "name": "Premium",
            "price": "Not priced yet",
            "sub": ("It is being built. There is no price, no date, and no card to enter "
                    "— only a list to be on when it is ready."),
            "items": PREMIUM_PROMISE,
        },
    }


def _span_words(a, b):
    days = (b - a).days
    years = days / 365.25
    return "1 year" if 0.9 <= years <= 1.1 else f"{years:.1f} years"
