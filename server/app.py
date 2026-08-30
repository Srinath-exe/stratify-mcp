"""Streamable HTTP MCP server, plus signup and report pages.

WHY THE PROTOCOL IS IMPLEMENTED DIRECTLY rather than through the MCP SDK: v1 authenticates
with a bearer API key and enforces per-key quotas on three dimensions, and those have to
wrap every single call. Owning the ~150 lines of JSON-RPC dispatch keeps auth, quotas and
refusals in one readable place, and removes a dependency from the deploy. When OAuth
arrives in phase 2 this is also where it plugs in.

Endpoints:
  POST /mcp          JSON-RPC 2.0 — initialize, tools/list, tools/call, ping
  POST /v1/signup    self-serve account + first key. No approval, no waitlist
  POST /v1/keys      issue another key for an existing account
  GET  /v1/coverage  public, unauthenticated — what the data covers
  POST /v1/feedback  file a bug, feature request or complaint (key or session)
  GET  /admin        operations console — session cookie + accounts.is_admin
  GET  /r/{token}    rendered report, signed token, public-readable
  GET  /healthz

NOT PRESENT, deliberately: any route that returns market data. Decision D3 — zero rows out.
"""
import ipaddress
import json
import os
import time
import traceback
import urllib.parse
from urllib.parse import urlparse

from fastapi import FastAPI, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from engine import db as engine_db

from . import (admin as admin_console, appview, artifact as artifact_report, book,
               dashboard as dash, feedback as feedback_mod, knowledge, oauth, quota,
               render, reports, site, store, tools)

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "stratify", "version": "1.0.0"}

# A locally-bound MCP server is reachable from any web page the user visits unless it
# checks Origin — the browser will happily POST to 127.0.0.1 with the user's ambient
# credentials. The MCP spec requires this check; it costs one comparison.
# Origins that may drive this endpoint from a BROWSER. Anthropic's own connector fetches
# server-side and sends no Origin at all, which the check below already allows -- these
# cover the case where a client calls from the page instead. Allowing them is low risk
# here: this endpoint has no cookie or ambient credential to steal, since a browser never
# attaches an Authorization header on its own. The check still matters for the
# locally-bound case the MCP spec warns about, so it stays closed by default.
DEFAULT_BROWSER_ORIGINS = {
    "https://claude.ai", "https://www.claude.ai", "https://api.claude.ai",
}
ALLOWED_ORIGINS = ({o.strip() for o in
                    os.getenv("STRATIFY_ALLOWED_ORIGINS", "").split(",") if o.strip()}
                   | DEFAULT_BROWSER_ORIGINS)

# A JSON-RPC backtest request is a few hundred bytes. Anything approaching a megabyte is
# either broken or probing, and parsing it first is how you get memory-exhausted.
MAX_BODY_BYTES = 64 * 1024


class BodyTooLarge(Exception):
    """Distinct from a parse failure. json.JSONDecodeError subclasses ValueError, so
    catching ValueError for the size check turned every malformed body into a 413."""

# JSON-RPC reserved codes, plus our own above -32000.
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602
UNAUTHENTICATED, QUOTA_EXCEEDED = -32001, -32002

app = FastAPI(title="Stratify MCP", docs_url=None, redoc_url=None, openapi_url=None)


# Two public hostnames serve this one app: a web host (signup, dashboard, reports) and a
# protocol host (/mcp only). Deriving every URL from the request's own Host means the
# signup page advertises an mcp_endpoint on the WEB host, and a report minted during an
# MCP call gets a link on the PROTOCOL host -- each correct-by-accident and wrong to hand
# to a person. These pin the two URLs that are handed out rather than merely served.
PUBLIC_BASE_URL  = os.getenv("STRATIFY_PUBLIC_BASE_URL", "").rstrip("/")
PUBLIC_MCP_URL   = os.getenv("STRATIFY_PUBLIC_MCP_URL", "").rstrip("/")


def _base_url(request):
    """Where a human-facing link should point: the web host, when one is configured."""
    return PUBLIC_BASE_URL or str(request.base_url).rstrip("/")


def _mcp_url(request):
    """The endpoint an MCP client should be pointed at."""
    return PUBLIC_MCP_URL or f"{str(request.base_url).rstrip('/')}/mcp"


def _client_ip(request):
    """The address the signup throttle counts against.

    THE GATE WAS RIGHT AND THE INDEX WAS WRONG. Trusting X-Forwarded-For only behind a
    declared proxy is correct. Reading `fwd.split(",")[0]` is not: nginx sets
    $proxy_add_x_forwarded_for, which APPENDS the real peer to whatever the client sent, so
    the LEFTMOST element is attacker-supplied and the rightmost is nginx's own observation.
    Sending `X-Forwarded-For: 1.2.3.4` with an incrementing address gave a fresh identity
    per request, so the 3/hour and 10/day account caps never fired -- roughly 600 accounts
    an hour from one address, each with a fresh quota. That is the entire abuse model of a
    self-serve service.

    X-Real-IP is preferred because nginx OVERWRITES it with $remote_addr rather than
    appending, so it cannot be spoofed through our own proxy at all. The rightmost XFF
    element is the fallback with the same property.
    """
    if os.getenv("STRATIFY_BEHIND_PROXY") == "1":
        real = (request.headers.get("x-real-ip") or "").strip()
        if real:
            try:
                ipaddress.ip_address(real)
                return real
            except ValueError:
                pass
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            # RIGHTMOST, not leftmost: the hop our own proxy added.
            candidate = fwd.split(",")[-1].strip()
            try:
                ipaddress.ip_address(candidate)
                return candidate
            except ValueError:
                pass
    return request.client.host if request.client else "unknown"


def _origin_allowed(request):
    origin = request.headers.get("origin")
    if not origin:
        return True                     # non-browser client; no ambient credentials
    if not ALLOWED_ORIGINS:
        return False                    # closed by default, opened by configuration
    host = urlparse(origin).netloc or origin
    return origin in ALLOWED_ORIGINS or host in ALLOWED_ORIGINS


async def _read_json(request):
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise BodyTooLarge(f"request body exceeds {MAX_BODY_BYTES} bytes")
    return json.loads(body)


def _rpc_error(request_id, code, message, data=None):
    err = {"code": code, "message": message}
    if data:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": err}


def _rpc_ok(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _tool_text(payload):
    """MCP tool results are content blocks. The text block is what the MODEL reads;
    structuredContent is the full machine-readable payload.

    These used to be the same 50 KB of JSON, sent twice. The model then had 13,000 tokens
    of equity curve and trade rows in front of it and did the predictable thing: narrated
    the tables. For a backtest the text block is now a short brief -- verdict, the numbers
    that decide it, the evidence, and a pointer at the report -- while structuredContent
    carries everything, unchanged and untrimmed.

    Nothing is withheld by this. A client that reads structuredContent still receives the
    complete payload, storage is untouched, and no release meter moves, because a brief
    contains no prices. What changes is what the model sees first.
    """
    brief = tools.digest(payload) if isinstance(payload, dict) else None
    text = brief if brief else json.dumps(payload, separators=(",", ":"), default=str)
    return {"content": [{"type": "text", "text": text}],
            "structuredContent": payload if isinstance(payload, dict) else {"value": payload},
            "isError": False}


# Ceiling on a logged argument blob. A spec is a few hundred bytes; anything far larger is
# either an unusual client or someone probing, and neither is worth unbounded rows.
MAX_AUDIT_ARGUMENT_BYTES = 4000


def _audit_arguments(arguments):
    """What the model actually asked for. This is the closest thing to a 'prompt' that
    exists on this side of MCP -- the person's own words never reach the server, only the
    call their model constructed from them."""
    if arguments is None:
        return None
    try:
        blob = json.dumps(arguments, default=str, separators=(",", ":"))
    except Exception:                       # noqa: BLE001
        return None
    if len(blob) > MAX_AUDIT_ARGUMENT_BYTES:
        return blob[:MAX_AUDIT_ARGUMENT_BYTES] + '..."TRUNCATED"'
    return blob


def _tool_refusal(message):
    """A refusal is a RESULT with isError, not a protocol error: the model should read it
    and correct the call, not treat the connection as broken."""
    return {"content": [{"type": "text", "text": message}], "isError": True}


# Header names a client may present the API key under.
#
# WHY MORE THAN ONE. claude.ai's custom-connector UI reserves `Authorization` for its own
# OAuth flow and will not let a user set it by hand, so a bearer-only server is
# unreachable from that surface even though it supports header auth. Accepting the key
# under a plain custom header costs nothing and is what makes the browser client usable
# without building OAuth. `X-API-Key` is the ecosystem's common spelling; the rest are
# accepted because clients disagree about capitalisation and punctuation.
API_KEY_HEADERS = ("authorization", "x-api-key", "x-stratify-key", "api-key")


def _key_from_headers(headers):
    """Pull the key out of whichever header the client could actually set.

    Tolerates a missing "Bearer " prefix: several connector UIs provide only a value box
    and users paste the raw key. A key is unambiguous either way -- it carries its own
    sk_live_ prefix -- so refusing it on formatting alone would be pedantry that costs a
    real user their first call.
    """
    for name in API_KEY_HEADERS:
        raw = headers.get(name)
        if not raw:
            continue
        raw = raw.strip()
        if raw.lower().startswith("bearer "):
            raw = raw.split(None, 1)[1].strip()
        if raw:
            return raw
    return None


def _authenticate(headers, url_key=None):
    # A header beats the URL when both are present: it is the better channel, and a stale
    # key baked into a saved connector URL should not override one sent deliberately.
    presented = _key_from_headers(headers) or (url_key or "").strip() or None
    return store.authenticate(presented) if presented else None


CORS_HEADERS = ("authorization, x-api-key, x-stratify-key, api-key, "
                "content-type, mcp-protocol-version, mcp-session-id")


def _cors(response, request):
    """Echo the caller's origin when it is allowed. Never '*': that would let any page
    read the response, and a wildcard cannot be combined with credentials anyway."""
    origin = request.headers.get("origin")
    if origin and _origin_allowed(request):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Headers"] = CORS_HEADERS
        response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        response.headers["Access-Control-Max-Age"] = "600"
    return response


@app.options("/mcp")
async def mcp_preflight(request: Request):
    """A browser will not send Authorization cross-origin until this succeeds. Without it
    the connector fails with an opaque network error rather than an auth error."""
    if not _origin_allowed(request):
        return JSONResponse({"error": "origin not allowed"}, status_code=403)
    return _cors(JSONResponse(None, status_code=204), request)


# OFF BY DEFAULT. Header auth reaches claude.ai (it accepts `api-key`), so this exists
# only for a client that will pass neither a header nor OAuth. Enabling it puts a live
# credential in a URL, which is a place URLs get stored and logged -- do not turn it on
# without a client that actually needs it.
ALLOW_URL_KEY = os.getenv("STRATIFY_ALLOW_URL_KEY") == "1"


@app.post("/mcp/k/{url_key}")
async def mcp_endpoint_keyed(url_key: str, request: Request):
    """The key carried in the PATH, for clients that will not pass a custom header.

    claude.ai's connector reserves Authorization for its own OAuth flow, and when header
    auth is unavailable it falls back to OAuth discovery + Dynamic Client Registration --
    /.well-known/oauth-protected-resource, /.well-known/oauth-authorization-server,
    POST /register -- and reports "couldn't reach" when those 404. Every connector UI
    lets a user set the URL, so this route works everywhere without building OAuth.

    THE TRADE, stated plainly: a key in a URL is a key in a place URLs get stored -- the
    client's config, and any proxy log in between. nginx is configured not to log this
    path (see sites-available/stratify), and the key is revocable from the dashboard, but
    a header remains the better choice wherever a client allows one.
    """
    if not ALLOW_URL_KEY:
        return JSONResponse(_rpc_error(None, INVALID_REQUEST,
            "URL-embedded keys are disabled on this server; send the key as a header "
            "(api-key, X-API-Key or Authorization)"), status_code=404)
    return await _serve_mcp(request, url_key)


@app.post("/mcp")
async def mcp_endpoint(request: Request):
    return await _serve_mcp(request, None)


async def _serve_mcp(request: Request, url_key):
    if not _origin_allowed(request):
        return JSONResponse(
            _rpc_error(None, INVALID_REQUEST, "origin not allowed"), status_code=403)
    try:
        message = await _read_json(request)
    except BodyTooLarge as exc:
        return JSONResponse(_rpc_error(None, PARSE_ERROR, str(exc)), status_code=413)
    except Exception:
        return JSONResponse(_rpc_error(None, PARSE_ERROR, "invalid JSON"), status_code=400)

    if isinstance(message, list):
        return _cors(JSONResponse([_handle(m, request, url_key) for m in message]), request)
    return _cors(JSONResponse(_handle(message, request, url_key)), request)


def _handle(message, request, url_key=None):
    # TEMPORARY DIAGNOSTIC (2026-08-25): claude.ai reports "couldn't reach" while every
    # POST returns 200, so we need to see which method it calls and whether the API key
    # header survives its connector UI. Logs the header NAME only, never the value.
    if os.getenv("STRATIFY_TRACE") == "1":
        seen = [h for h in API_KEY_HEADERS if request.headers.get(h)]
        print(f"[trace] method={(message or {}).get('method')!r} "
              f"key_headers={seen or 'NONE'} ua={request.headers.get('user-agent','?')[:40]}",
              flush=True)

    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _rpc_error(None, INVALID_REQUEST, "expected JSON-RPC 2.0")
    method, request_id = message.get("method"), message.get("id")
    params = message.get("params") or {}
    if not isinstance(params, dict):
        # JSON-RPC allows params to be an array. Every handler here calls params.get(),
        # and a non-empty list is truthy with no .get -- so `"params":[1]` was an
        # unauthenticated 500 on several methods. A malformed request is a 400.
        return _rpc_error(request_id, INVALID_REQUEST,
                          "params must be an object for this server's methods")

    # Notifications carry no id and get no response body.
    if request_id is None and method and method.startswith("notifications/"):
        return {"jsonrpc": "2.0", "id": None, "result": {}}

    if method == "initialize":
        return _rpc_ok(request_id, {
            "protocolVersion": PROTOCOL_VERSION,
            # resources and prompts are served, not just tools: the knowledge base is
            # readable without spending a tool call, and the prompts encode whole
            # workflows so a model does the careful thing by default rather than by
            # inspiration.
            # listChanged TRUE. It was False, which tells a client the tool list is
            # immutable and safe to cache for the life of the connection -- so when
            # build_report was added, every already-connected session kept serving the
            # old list, and the model correctly reported a tool that the response text
            # advertised but tools/list did not contain. We cannot push a notification
            # over stateless HTTP, but declaring the list mutable is the difference
            # between a client that may re-poll and one that never will.
            "capabilities": {"tools": {"listChanged": True},
                             "resources": {"listChanged": False, "subscribe": False},
                             "prompts": {"listChanged": False},
                             # The apps extension. The CLIENT advertises support at
                             # initialize; declaring it here too tells a host that
                             # inspects server capabilities that our tools carry UI
                             # templates, and is ignored by hosts that do not.
                             "extensions": {
                                 "io.modelcontextprotocol/ui": {
                                     "mimeTypes": [appview.MIME]}}},
            "serverInfo": SERVER_INFO,
            "instructions": knowledge.SERVER_INSTRUCTIONS,
        })
    if method == "ping":
        return _rpc_ok(request_id, {})
    if method == "tools/list":
        return _rpc_ok(request_id, {"tools": tools.TOOLS})
    # Knowledge endpoints are UNAUTHENTICATED on purpose: they return no market data and
    # no account state, only how to use the service correctly. Requiring a key to learn
    # the rules would make the first call a model makes the one most likely to be wrong.
    if method == "resources/list":
        # The UI template is listed alongside the knowledge documents. A host that does
        # not implement the apps extension simply sees a resource whose mimeType it does
        # not recognise and ignores it, which is the intended degradation.
        return _rpc_ok(request_id,
                       {"resources": knowledge.RESOURCES + [appview.RESOURCE]})
    if method == "resources/read":
        uri = (params or {}).get("uri", "")
        if uri == appview.URI:
            return _rpc_ok(request_id, {"contents": [appview.contents()]})
        doc = knowledge.read_resource(uri)
        if doc is None:
            return _rpc_error(request_id, INVALID_PARAMS, f"unknown resource {uri!r}")
        return _rpc_ok(request_id, {"contents": [doc]})
    if method == "prompts/list":
        return _rpc_ok(request_id, {"prompts": knowledge.PROMPTS})
    if method == "prompts/get":
        got = knowledge.get_prompt((params or {}).get("name", ""),
                                   (params or {}).get("arguments"))
        if got is None:
            return _rpc_error(request_id, INVALID_PARAMS,
                              f"unknown prompt {(params or {}).get('name')!r}")
        return _rpc_ok(request_id, got)

    if method != "tools/call":
        return _rpc_error(request_id, METHOD_NOT_FOUND, f"unknown method {method!r}")

    name = params.get("name")
    handler = tools.HANDLERS.get(name)
    if handler is None:
        return _rpc_error(request_id, INVALID_PARAMS, f"unknown tool {name!r}")

    key = _authenticate(request.headers, url_key)
    if key is None:
        return _rpc_error(
            request_id, UNAUTHENTICATED,
            "missing or invalid API key",
            {"how_to_fix": f"POST {_base_url(request)}/v1/signup with an email to get one, "
                           f"then send it as 'Authorization: Bearer sk_live_...'",
             "alternative_headers": ("If your client reserves Authorization for OAuth "
                                     "(claude.ai's connector UI does), send the key as "
                                     "'X-API-Key: sk_live_...' instead — same effect.")})

    tier = quota.tier_for(key)
    try:
        quota.check(key["account_id"], tier)
    except quota.QuotaExceeded as exc:
        return _rpc_error(request_id, QUOTA_EXCEEDED, exc.message,
                          {"limit": exc.limit, "retry_after_seconds": exc.retry_after_seconds})

    context = {"key_id": key["key_id"], "account_id": key["account_id"],
               "tier": tier.name, "base_url": _base_url(request)}
    try:
        slot = quota.CONCURRENCY.acquire(key["account_id"], tier.max_concurrent,
                                         tier=tier.name)
    except quota.QuotaExceeded as exc:
        return _rpc_error(request_id, QUOTA_EXCEEDED, exc.message,
                          {"limit": exc.limit, "retry_after_seconds": exc.retry_after_seconds})
    # Usage is metered HERE, for every tool, rather than inside individual handlers.
    # It was previously recorded only by run_backtest, which left describe_coverage and
    # search costing a caller nothing while still hitting ClickHouse. Metering at the
    # dispatch point means a new tool cannot forget to be counted.
    started = time.process_time()
    # Bind the ClickHouse identity for this request. Everything downstream inherits it,
    # and the row policy on that user is what actually stops a free key reading paid data.
    tier_token = engine_db.use_tier(tier.name)
    # The audit row is assembled as the call runs and written in `finally`, so a refusal
    # and an internal error are recorded as faithfully as a success. A log that only holds
    # the calls that worked is the one shape of log that cannot answer "what went wrong".
    audit = {"account_id": key["account_id"], "key_id": key["key_id"], "tier": tier.name,
             "method": method, "tool": params.get("name"),
             "arguments_json": _audit_arguments(params.get("arguments")),
             "outcome": "ok",
             "client": (request.headers.get("user-agent") or "")[:200]}
    payload = None
    try:
        payload = handler(params.get("arguments") or {}, context)
    except tools.ToolError as exc:
        audit["outcome"], audit["refusal"] = "refused", str(exc)[:500]
        return _rpc_ok(request_id, _tool_refusal(str(exc)))
    except engine_db.CapacityExceeded as exc:
        # A shared-capacity limit, not a fault. It reached the blanket handler below and
        # came back as "internal error", which is both unhelpful and untrue.
        audit["outcome"], audit["refusal"] = "capacity", exc.message[:500]
        return _rpc_error(request_id, QUOTA_EXCEEDED, exc.message,
                          {"retry_after_seconds": exc.retry_after_seconds})
    except Exception:
        # Never leak a stack trace or a query to a caller.
        audit["outcome"] = "error"
        traceback.print_exc()
        return _rpc_error(request_id, -32603, "internal error")
    finally:
        engine_db.current_user.reset(tier_token)
        # price_points is written by the handler into the context. Metering it HERE, at
        # the same point as CPU, is what stops a future tool that returns prices from
        # forgetting to be counted.
        cpu = time.process_time() - started
        store.record_usage(key["key_id"], key["account_id"], cpu,
                           price_points=context.get("price_points", 0))
        audit.update(cpu_seconds=cpu, price_points=context.get("price_points", 0),
                     backtest_id=(payload or {}).get("backtest_id")
                     if isinstance(payload, dict) else None,
                     response_bytes=len(json.dumps(payload, default=str))
                     if payload is not None else 0)
        store.record_call(audit)
        quota.CONCURRENCY.release(slot)

    if isinstance(payload, dict):
        # Snapshot AFTER the call, so the caller sees what this request consumed rather
        # than what was left before it ran. A quota readout that lags by one request is
        # how clients end up surprised by a 429.
        payload["quota"] = quota.snapshot(key["account_id"], tier)
    return _rpc_ok(request_id, _tool_text(payload))


# ---------------------------------------------------------------- accounts

@app.post("/v1/signup")
async def signup(request: Request):
    try:
        body = await _read_json(request)
    except BodyTooLarge as exc:
        return JSONResponse({"error": str(exc)}, status_code=413)
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    email = (body or {}).get("email", "").strip()
    if "@" not in email or len(email) > 254:
        return JSONResponse({"error": "a valid email is required"}, status_code=400)
    try:
        ip_hash = quota.check_signup(_client_ip(request))
    except quota.QuotaExceeded as exc:
        return JSONResponse(
            {"error": exc.message, "limit": exc.limit,
             "retry_after_seconds": exc.retry_after_seconds},
            status_code=429, headers={"Retry-After": str(exc.retry_after_seconds)})
    account_id, created = store.create_account(email)
    if not created:
        # SIGNUP IS NOT A LOGIN. Returning a key here handed anyone who knew an existing
        # customer's email address that customer's account -- their keys, their research,
        # their strategy book, and (because tier is read off the account) their paid data.
        # Issuing a second key to an existing account is a job for someone already holding
        # a credential for it; see POST /v1/keys, which now requires one.
        return JSONResponse(
            {"error": "an account already exists for that address",
             "how_to_fix": ("Use the API key you were issued at signup. To mint another, "
                            "call POST /v1/keys with an existing key in the Authorization "
                            "header, or use the dashboard."),
             "note": ("Signing up again does not return an existing account's key. If you "
                      "have lost every key for this account, contact support -- there is "
                      "deliberately no self-serve path that turns an email address alone "
                      "into access.")},
            status_code=409)
    try:
        quota.check_key_issuance(account_id)
    except quota.QuotaExceeded as exc:
        return JSONResponse({"error": exc.message, "limit": exc.limit}, status_code=429)
    key_id, secret = store.issue_key(account_id)
    store.record_signup(ip_hash, account_id)
    return {
        "account_id": account_id, "key_id": key_id, "api_key": secret,
        "tier": "free",
        "quota_note": ("Limits are metered on the ACCOUNT, not on the key. Issuing more "
                       "keys does not increase your allowance."),
        "limits": {"backtests_per_hour": quota.TIERS["free"].requests_per_hour,
                   "cpu_seconds_per_hour": quota.TIERS["free"].cpu_seconds_per_hour,
                   "price_points_per_hour": quota.TIERS["free"].price_points_per_hour,
                   "max_concurrent": quota.TIERS["free"].max_concurrent},
        "mcp_endpoint": _mcp_url(request),
        "warning": ("This key is shown once and is not recoverable. Store it now. "
                    "Send it as 'Authorization: Bearer <key>'."),
    }


@app.post("/v1/keys")
async def new_key(request: Request):
    try:
        body = await _read_json(request)
    except BodyTooLarge as exc:
        return JSONResponse({"error": str(exc)}, status_code=413)
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    # AUTHENTICATE FIRST. This endpoint used to mint a live key for any account_id that
    # existed, with no credential -- and account_id is not a secret: it is printed on the
    # dashboard, returned by signup, and returned by list_strategies. Knowing it was enough
    # to issue a working key against someone else's tier.
    #
    # Proving you already hold a credential for the account is the whole check. A caller
    # with a valid key or an active session is, by definition, already inside.
    presented = _authenticate(request.headers)
    session = _account_from_cookie(request)
    if presented is not None:
        account_id = presented["account_id"]
    elif session is not None:
        account_id = session["account_id"]
    else:
        return JSONResponse(
            {"error": "authentication required",
             "how_to_fix": ("Send an existing API key as 'Authorization: Bearer sk_live_...' "
                            "(or 'X-API-Key'), or call this from a signed-in dashboard "
                            "session. An account_id alone is not a credential.")},
            status_code=401)
    # The account is now taken from the CREDENTIAL, never from the request body, so a
    # caller cannot name someone else's account.
    if (body or {}).get("account_id") not in (None, "", account_id):
        return JSONResponse(
            {"error": "keys are issued for the authenticated account only"},
            status_code=403)
    try:
        quota.check_key_issuance(account_id)
    except quota.QuotaExceeded as exc:
        return JSONResponse({"error": exc.message, "limit": exc.limit}, status_code=429)
    key_id, secret = store.issue_key(account_id)
    return {"key_id": key_id, "api_key": secret,
            "warning": "Shown once. Not recoverable. Keys share the account's quota."}


@app.get("/v1/coverage")
async def coverage(request: Request):
    """Public and unauthenticated on purpose: a client should be able to see what exists
    before deciding whether to sign up. It returns metadata, never data."""
    return tools.describe_coverage({}, {"key_id": None, "base_url": _base_url(request)})


# ---------------------------------------------------------------- dashboard

SESSION_COOKIE = "stratify_session"


def _account_from_cookie(request):
    return store.account_by_session(request.cookies.get(SESSION_COOKIE))


@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    """Shown to EVERYONE, signed in or not.

    It used to redirect a signed-in visitor straight into the dashboard, which meant the
    one page the whole product is judged on was invisible to every existing customer --
    including whoever is working on it.
    """
    account = _account_from_cookie(request)
    return HTMLResponse(render.landing(site.landing_view(
        _base_url(request), signed_in=account is not None,
        google_ready=oauth.enabled(),
        error=request.query_params.get("error"))))


@app.post("/signup", response_class=HTMLResponse)
async def signup_form(request: Request):
    form = await request.form()
    email = (form.get("email") or "").strip()
    if "@" not in email or len(email) > 254:
        return HTMLResponse(dash.landing(_base_url(request), "That is not a valid email."),
                            status_code=400)
    try:
        ip_hash = quota.check_signup(_client_ip(request))
    except quota.QuotaExceeded as exc:
        return HTMLResponse(dash.landing(_base_url(request), exc.message), status_code=429)
    account_id, created = store.create_account(email)
    if not created:
        # Worse than the JSON path: this one also set a 90-day session cookie, so typing a
        # known email into the public form opened that person's dashboard.
        return HTMLResponse(dash.landing(
            _base_url(request),
            "An account already exists for that address. Signing up again will not return "
            "its key — use the key you were issued, or contact support."), status_code=409)
    try:
        quota.check_key_issuance(account_id)
    except quota.QuotaExceeded as exc:
        return HTMLResponse(dash.landing(_base_url(request), exc.message), status_code=429)
    _, secret = store.issue_key(account_id)
    store.record_signup(ip_hash, account_id)
    account = store.account_by_session(store.session_token(account_id))
    response = HTMLResponse(dash.dashboard(account, _base_url(request), mcp_url=_mcp_url(request), new_key=secret))
    # httponly and samesite=strict: the session cookie is never readable by script and
    # never travels on a cross-site request, so a page elsewhere cannot drive this
    # dashboard on the user's behalf.
    # `secure` as well as httponly/samesite: HSTS protects a RETURNING browser, not a
    # first visit, and without this the cookie would go out over a plaintext first request.
    response.set_cookie(SESSION_COOKIE, account["session_token"], httponly=True,
                        secure=True, samesite="strict",
                        max_age=60 * 60 * 24 * 90, path="/")
    return response


# ------------------------------------------------------- sign in with Google
#
# The session cookie is set in exactly two places: here, and the legacy email signup above.
# Everything else only reads it. Keeping the write points down to two is what makes "who
# can be signed in as whom" a question with a short answer.

def _sign_in(account, response):
    """Attach a fresh session cookie for `account` to `response`."""
    response.set_cookie(SESSION_COOKIE, account["session_token"], httponly=True,
                        secure=True, samesite="lax",
                        max_age=60 * 60 * 24 * 90, path="/")
    return response


@app.get("/auth/google")
async def auth_google(request: Request):
    """Start the handshake. `next` lets a deep link survive the round trip to Google."""
    if not oauth.enabled():
        return HTMLResponse(render.message_page(
            "Sign-in is not configured",
            "This server has no Google client credentials, so sign-in is switched off.",
            "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and restart."), status_code=503)
    url, flow = oauth.begin(_base_url(request), request.query_params.get("next") or "/app")
    response = RedirectResponse(url, status_code=302)
    # samesite=lax, NOT strict: Google redirects the browser back to us as a cross-site
    # navigation, and a strict cookie is not sent on one -- the handshake would fail its
    # own CSRF check every single time. Lax is precisely the case this setting exists for.
    response.set_cookie(oauth.FLOW_COOKIE, flow, httponly=True, secure=True,
                        samesite="lax", max_age=oauth.FLOW_TTL, path="/auth")
    return response


@app.get(oauth.CALLBACK_PATH)
async def auth_google_callback(request: Request):
    q = request.query_params
    if q.get("error"):
        return HTMLResponse(render.message_page(
            "Sign-in cancelled", "Google did not complete the sign-in.",
            q.get("error_description") or q.get("error")), status_code=400)
    try:
        who = oauth.finish(_base_url(request), q.get("code"), q.get("state"),
                           request.cookies.get(oauth.FLOW_COOKIE))
    except oauth.OAuthError as exc:
        return HTMLResponse(render.message_page("Could not sign you in", str(exc)),
                            status_code=400)
    account, created = store.account_for_google(
        who["sub"], who["email"], who["name"], who["picture"])
    if created:
        # The signup throttle applies to Google accounts too. It is not a bot check --
        # Google already did that -- it is what stops one person minting accounts to reset
        # a per-account quota.
        try:
            ip_hash = quota.check_signup(_client_ip(request))
            store.record_signup(ip_hash, account["account_id"])
        except quota.QuotaExceeded as exc:
            return HTMLResponse(render.message_page(
                "Too many new accounts", exc.message), status_code=429)
    response = RedirectResponse(who["next"], status_code=302)
    _sign_in(account, response)
    response.delete_cookie(oauth.FLOW_COOKIE, path="/auth")
    return response


@app.post(oauth.ONETAP_PATH)
async def auth_google_onetap(request: Request):
    """Google One Tap posts the credential straight here, as a form.

    This endpoint is PUBLIC and unauthenticated by nature -- it is how you sign in -- so
    everything it is handed is hostile until `verify_id_token` says otherwise. It never
    parses the token itself.
    """
    form = await request.form()
    try:
        who = oauth.verify_id_token(
            form.get("credential"),
            csrf_cookie=request.cookies.get(oauth.CSRF_FIELD),
            csrf_field=form.get(oauth.CSRF_FIELD))
    except oauth.OAuthError as exc:
        return HTMLResponse(render.message_page("Could not sign you in", str(exc)),
                            status_code=400)
    account, created = store.account_for_google(
        who["sub"], who["email"], who["name"], who["picture"])
    if created:
        try:
            ip_hash = quota.check_signup(_client_ip(request))
            store.record_signup(ip_hash, account["account_id"])
        except quota.QuotaExceeded as exc:
            return HTMLResponse(render.message_page(
                "Too many new accounts", exc.message), status_code=429)
    # Where One Tap was shown, not an attacker-supplied target: `_safe_next` keeps this a
    # path on this site whatever the form said.
    nxt = oauth._safe_next(form.get("next") or "/app")
    return _sign_in(account, RedirectResponse(nxt, status_code=303))


@app.get("/logout")
async def logout(request: Request):
    """Rotates the token SERVER side, so a copied cookie dies with the session rather than
    outliving it. Clearing the browser's cookie alone leaves the secret valid."""
    account = _account_from_cookie(request)
    if account is not None:
        store.rotate_session(account["account_id"])
    response = RedirectResponse("/", status_code=302)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


# -------------------------------------------------------------------- the app pages

def _require(request):
    """-> (account, None) or (None, redirect to sign-in). Every /app route starts here."""
    account = _account_from_cookie(request)
    if account is not None:
        return account, None
    nxt = urllib.parse.quote(request.url.path, safe="/")
    return None, RedirectResponse(f"/auth/google?next={nxt}", status_code=302)


@app.get("/app", response_class=HTMLResponse)
async def app_overview(request: Request):
    account, redirect = _require(request)
    if redirect:
        return redirect
    return HTMLResponse(render.overview(site.overview_view(account, _base_url(request))))


@app.get("/app/keys", response_class=HTMLResponse)
async def app_keys(request: Request):
    account, redirect = _require(request)
    if redirect:
        return redirect
    return HTMLResponse(render.keys(site.keys_view(account)))


@app.post("/app/keys/new", response_class=HTMLResponse)
async def app_keys_new(request: Request):
    account, redirect = _require(request)
    if redirect:
        return redirect
    try:
        quota.check_key_issuance(account["account_id"])
    except quota.QuotaExceeded as exc:
        return HTMLResponse(render.keys(site.keys_view(account, message=exc.message)),
                            status_code=429)
    _, secret = store.issue_key(account["account_id"])
    # Rendered here and nowhere else, ever. There is no route that can fetch it again.
    return HTMLResponse(render.keys(site.keys_view(account, new_key=secret)))


@app.post("/app/keys/revoke", response_class=HTMLResponse)
async def app_keys_revoke(request: Request):
    account, redirect = _require(request)
    if redirect:
        return redirect
    form = await request.form()
    key_id = (form.get("key_id") or "").strip()
    # Ownership is checked server side. The form field is caller-controlled, so without
    # this anyone could revoke anyone else's key by posting its id.
    if store.key_owner(key_id) != account["account_id"]:
        return HTMLResponse(render.keys(site.keys_view(
            account, message="That key does not belong to this account.")),
            status_code=403)
    store.revoke_key(key_id)
    return HTMLResponse(render.keys(site.keys_view(
        account, message=f"Revoked {key_id}.")))


@app.get("/app/logs", response_class=HTMLResponse)
async def app_logs(request: Request):
    account, redirect = _require(request)
    if redirect:
        return redirect
    return HTMLResponse(render.logs(site.logs_view(account)))


@app.get("/app/reports", response_class=HTMLResponse)
async def app_reports(request: Request):
    account, redirect = _require(request)
    if redirect:
        return redirect
    return HTMLResponse(render.reports(site.reports_view(account, _base_url(request))))


@app.get("/docs", response_class=HTMLResponse)
async def docs_page(request: Request):
    """Public on purpose. Somebody deciding whether to sign up has to be able to read what
    they would be signing up to."""
    return HTMLResponse(render.docs(site.docs_view(
        _base_url(request), signed_in=_account_from_cookie(request) is not None)))


@app.get("/privacy", response_class=HTMLResponse)
async def privacy_page(request: Request):
    """Public and linkable without a session. Google's consent screen fetches this URL,
    and a policy behind a login is not a policy."""
    return HTMLResponse(render.legal(site.legal_view(
        "privacy", signed_in=_account_from_cookie(request) is not None)))


@app.get("/terms", response_class=HTMLResponse)
async def terms_page(request: Request):
    return HTMLResponse(render.legal(site.legal_view(
        "terms", signed_in=_account_from_cookie(request) is not None)))


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    account = _account_from_cookie(request)
    if account is None:
        return HTMLResponse(dash.landing(_base_url(request), "Please sign in again."),
                            status_code=401)
    return HTMLResponse(dash.dashboard(account, _base_url(request), mcp_url=_mcp_url(request)))


@app.post("/dashboard/keys", response_class=HTMLResponse)
async def dashboard_new_key(request: Request):
    account = _account_from_cookie(request)
    if account is None:
        return HTMLResponse(dash.landing(_base_url(request)), status_code=401)
    try:
        quota.check_key_issuance(account["account_id"])
    except quota.QuotaExceeded as exc:
        return HTMLResponse(dash.dashboard(account, _base_url(request), mcp_url=_mcp_url(request),
                                           message=exc.message), status_code=429)
    _, secret = store.issue_key(account["account_id"])
    return HTMLResponse(dash.dashboard(account, _base_url(request), mcp_url=_mcp_url(request), new_key=secret))


@app.post("/dashboard/revoke", response_class=HTMLResponse)
async def dashboard_revoke(request: Request):
    account = _account_from_cookie(request)
    if account is None:
        return HTMLResponse(dash.landing(_base_url(request)), status_code=401)
    form = await request.form()
    key_id = (form.get("key_id") or "").strip()
    # Ownership is checked server-side: the form field is caller-controlled, so without
    # this anyone could revoke anyone else's key by posting its id.
    if store.key_owner(key_id) != account["account_id"]:
        return HTMLResponse(dash.dashboard(account, _base_url(request), mcp_url=_mcp_url(request),
                                           message="That key does not belong to this "
                                                   "account."), status_code=403)
    store.revoke_key(key_id)
    return HTMLResponse(dash.dashboard(account, _base_url(request), mcp_url=_mcp_url(request),
                                       message=f"Revoked {key_id}."))


# ---------------------------------------------------------------- feedback

@app.post("/v1/feedback")
async def submit_feedback_http(request: Request):
    """The same intake the MCP tool uses, over plain HTTP.

    TWO CREDENTIALS ARE ACCEPTED, an API key or the dashboard session, because the two
    places a person reports something from are their agent and their browser, and making
    the browser path mint a key first would lose most reports. Neither is optional:
    anonymous intake is a spam queue, and a report with no account has no context
    attached, which is the only thing that makes a report worth keeping.
    """
    try:
        body = await _read_json(request)
    except BodyTooLarge as exc:
        return JSONResponse({"error": str(exc)}, status_code=413)
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "expected a JSON object"}, status_code=400)

    source, account_id, tier = "api", None, "free"
    key = _authenticate(request.headers)
    if key is not None:
        account_id, tier = key["account_id"], quota.tier_for(key).name
    else:
        account = _account_from_cookie(request)
        if account is not None:
            source = "dashboard"
            account_id, tier = account["account_id"], account["tier"]
    if account_id is None:
        return JSONResponse(
            {"error": "authentication required",
             "how_to_fix": "Send your API key as 'Authorization: Bearer sk_live_...', or "
                           "file it from the dashboard while signed in."},
            status_code=401)
    try:
        result = feedback_mod.submit(
            account_id=account_id, tier=tier,
            title=body.get("title"), body=body.get("body"),
            category=body.get("category"), severity=body.get("severity"),
            backtest_id=body.get("backtest_id"), source=source)
    except feedback_mod.FeedbackError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(result, status_code=201 if result["is_new"] else 200)


@app.get("/v1/feedback")
async def my_feedback_http(request: Request):
    key = _authenticate(request.headers)
    account_id = key["account_id"] if key is not None else None
    if account_id is None:
        account = _account_from_cookie(request)
        account_id = account["account_id"] if account is not None else None
    if account_id is None:
        return JSONResponse({"error": "authentication required"}, status_code=401)
    return JSONResponse({"reports": store.feedback_for_account(account_id)})


# ---------------------------------------------------------------- admin console

def _admin_account(request):
    """Returns the account only if it is signed in AND flagged admin.

    The flag is re-read from the database on every request rather than trusted from the
    session, so revoking admin takes effect on the next click instead of at the next
    login. A 90-day cookie makes that difference matter.
    """
    account = _account_from_cookie(request)
    if account is None or not store.is_admin(account["account_id"]):
        return None
    return account


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    account = _admin_account(request)
    if account is None:
        # 404, not 403. A 403 confirms the console exists at this path to anyone who
        # guesses it; there is nothing to gain from telling them.
        return HTMLResponse("<h1>Not found</h1>", status_code=404)
    return HTMLResponse(admin_console.console(account))


@app.post("/admin/feedback", response_class=HTMLResponse)
async def admin_triage(request: Request):
    account = _admin_account(request)
    if account is None:
        return HTMLResponse("<h1>Not found</h1>", status_code=404)
    form = await request.form()
    feedback_id = (form.get("feedback_id") or "").strip()
    status = (form.get("status") or "").strip() or None
    severity = (form.get("severity") or "").strip() or None
    note = (form.get("triage_note") or "").strip()[:300] or None
    if status is not None and status not in feedback_mod.STATUSES:
        return HTMLResponse(admin_console.console(account, "Unknown status."),
                            status_code=400)
    if severity is not None and severity not in feedback_mod.SEVERITIES:
        return HTMLResponse(admin_console.console(account, "Unknown severity."),
                            status_code=400)
    if store.get_feedback(feedback_id) is None:
        return HTMLResponse(admin_console.console(account, "No such report."),
                            status_code=404)
    store.update_feedback(feedback_id, status=status, severity=severity, triage_note=note)
    return HTMLResponse(admin_console.console(
        account, f"Updated {feedback_id}."), status_code=200)


@app.get("/report/{token}", response_class=HTMLResponse)
async def full_report(token: str):
    """The full strategy report. Public by signed token, like /r/{token}.

    Served from storage rather than re-rendered: the page embeds a charting library and the
    whole index series, so rebuilding it per view would turn a shared link into a repeated
    seven-year query.
    """
    doc = store.get_full_report(token)
    if doc is None:
        return HTMLResponse("<h1>No such report</h1>", status_code=404)
    return HTMLResponse(doc, headers={"Cache-Control": "private, max-age=3600"})


@app.get("/healthz")
async def healthz():
    return {"ok": True, "ts": time.time()}


@app.get("/r/{token}", response_class=HTMLResponse)
async def report(token: str):
    """The hosted report and the artifact are the SAME document.

    They were two renderers briefly, and two renderers of one backtest is two sets of
    numbers waiting to disagree. `server/artifact.py` is the only one; this route serves
    it over HTTP and `build_report` hands the identical bytes to a model to publish.
    """
    row = store.get_result_by_token(token)
    if row is None:
        return HTMLResponse("<h1>No such report</h1>", status_code=404)
    return HTMLResponse(artifact_report.render(json.loads(row["payload_json"]),
                                               row["backtest_id"]))
