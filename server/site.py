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
import time

from engine import strategy as strategy_mod
from . import quota, store

MCP_PATH = "/mcp"


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

STEPS = [
    {"n": 1, "title": "Sign in with Google", "body": "No password, no waitlist, no card."},
    {"n": 2, "title": "Generate a key", "body": "One click. It is shown once."},
    {"n": 3, "title": "Paste it into your client",
     "body": "Claude, or anything that speaks MCP. Then just describe a strategy."},
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


def landing_view(base_url, signed_in, google_ready, error=None):
    free = quota.TIERS["free"]
    return {
        "onetap": onetap(base_url, signed_in),
        "page": "landing",
        "signed_in": signed_in,
        "google_ready": google_ready,
        "error": error,
        "hero": {
            "eyebrow": "NIFTY options, backtested properly",
            "title": "Describe any options strategy in plain English. "
                     "Get it backtested on real one-minute data.",
            "lede": "Stratify is an MCP server. Connect it to Claude, describe the trade "
                    "the way you would to a colleague, and get back a costed, "
                    "slippage-adjusted, margin-aware result in seconds.",
            "example": HERO_EXAMPLE,
            "cta": "Sign in with Google" if not signed_in else "Open your dashboard",
            "cta_href": "/auth/google?next=/app" if not signed_in else "/app",
            "secondary": {"label": "Read the docs", "href": "/docs"},
        },
        "pillars": PILLARS,
        "steps": STEPS,
        "free_tier": {
            "window": "1 year of history",
            "requests": f"{free.requests_per_hour} calls an hour",
            "cpu": f"{free.cpu_seconds_per_hour:.0f} CPU-seconds an hour",
            "note": "Every strategy feature is available on every tier. The window of "
                    "history is the only thing a paid plan changes.",
        },
        "mcp_url": base_url.rstrip("/") + MCP_PATH,
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
            {"key": "requests", "label": "Calls this hour",
             "used": snap["requests_used"], "limit": snap["requests_limit"]},
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
        "mcp_url": base_url.rstrip("/") + MCP_PATH,
        "connect": connect_snippets(base_url.rstrip("/") + MCP_PATH),
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
    return {
        "page": "keys", "nav": NAV, "you": identity(account), "message": message,
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
    return {
        "backtest_id": get("backtest_id"),
        "created": _stamp(get("created_at")),
        "ago": _ago(get("created_at")),
        "name": spec.get("name") or _describe_spec(spec),
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
    return ", ".join(str(spec.get(k)) for k in ("structure", "cadence") if spec.get(k)) or "—"


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
    mcp_url = base_url.rstrip("/") + MCP_PATH
    return {
        "page": "docs", "nav": NAV, "signed_in": signed_in,
        "mcp_url": mcp_url,
        "connect": connect_snippets(mcp_url),
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
    "contact": "Questions, corrections, or a deletion request: use the feedback tool "
               "inside the product, or write to the address on the contact page.",
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
    "contact": "Questions about these terms: use the feedback tool inside the product, or "
               "write to the address on the contact page.",
}


def legal_view(which, signed_in=False):
    """A static document. Kept in site.py with everything else the pages say, so a
    redesign of render.py cannot silently drop a clause."""
    doc = {"privacy": PRIVACY, "terms": TERMS}[which]
    return {"page": which, "nav": NAV, "signed_in": signed_in, "doc": doc,
            "other": ("terms", "Terms") if which == "privacy" else ("privacy", "Privacy")}
