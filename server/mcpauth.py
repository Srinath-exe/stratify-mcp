"""OAuth 2.1 authorization server, so the browser assistants can reach this service.

WHY THIS EXISTS AT ALL, given that v1 deliberately shipped API keys only (decision §0).
The keys work fine everywhere a client can set a header — Claude Code, Claude Desktop,
OpenCode, Gemini CLI, Codex. They do not work in any of the browser products, and each one
refuses for its own reason:

  * ChatGPT       — OAuth 2.1 with Dynamic Client Registration is mandatory and bearer
                    tokens are explicitly not accepted.
  * Gemini web    — "Connected Apps" takes a URL and runs DCR. There is no field for a key.
  * Gemini Business — OAuth 2.0 only, stated outright.
  * Claude's Connectors Directory — "use OAuth 2.0 for authenticated services".

So this is one piece of work that opens four surfaces, and it does not replace API keys:
both credentials reach the same accounts, the same quotas and the same tier.

WHAT MAKES THIS SAFE TO EXPOSE, in order of how much they matter:

  1. PKCE S256 is REQUIRED, not merely supported. Every client here is a public client —
     no secret survives being shipped in a browser or a CLI — so the code verifier is the
     only thing binding a redirect to the client that started it. `plain` is refused.
  2. Redirect URIs are matched EXACTLY against a registered list, with one deliberate
     exception for loopback addresses (RFC 8252 §7.3): native clients bind an ephemeral
     port at runtime, so the port is ignored there and only there.
  3. Codes are single-use, five minutes, and bound to the client, the redirect and the
     verifier. A replayed code revokes the entire token family, per OAuth 2.1 — because a
     second presentation means the first was seen by someone who should not have.
  4. Refresh tokens rotate on every use, and reuse of a rotated one is treated the same
     way. This is the requirement for public clients and it is what makes a stolen refresh
     token self-limiting: the thief and the victim cannot both keep using the chain.
  5. Nothing here mints an account. Authorization requires an existing signed-in session,
     so the identity always comes from Google sign-in — this server issues tokens, it does
     not decide who people are.

WHAT IS DELIBERATELY NOT HERE: a `client_credentials` grant. Every connection must have a
user in the loop, both because Anthropic requires it and because a machine-to-machine
token with no consenting human is exactly the credential that ends up in a public repo.
"""
import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
import urllib.request

from . import store

# One functional scope. Fine-grained scopes on a service whose entire surface is "run
# backtests for this account" would be consent-screen theatre: the user would be asked to
# reason about a distinction that does not exist. `offline_access` is separate because it
# is a real one — it decides whether the client gets to come back without asking again.
SCOPE = "mcp"
OFFLINE = "offline_access"
SCOPES_SUPPORTED = (SCOPE, OFFLINE)

REGISTRATIONS_PER_IP_PER_HOUR = 20

# CIMD (Client ID Metadata Document): the client_id is itself an HTTPS URL that serves the
# client's registration. Claude Code uses this, and it means no /register round trip and no
# row in our database per connection. The document is SELF-ASSERTED, which is why the
# consent screen shows the URL's host rather than the client_name it claims.
CIMD_TIMEOUT = 10
CIMD_MAX_BYTES = 64 * 1024


class OAuthError(Exception):
    """An RFC 6749 error. `code` is the machine-readable one clients branch on — getting
    it right matters more than the description, because `invalid_grant` specifically is
    what tells a client to discard a dead refresh token instead of retrying it forever."""

    def __init__(self, code, description, status=400):
        super().__init__(description)
        self.code = code
        self.description = description
        self.status = status

    def body(self):
        return {"error": self.code, "error_description": self.description}


# ---------------------------------------------------------------- discovery

def protected_resource_metadata(resource_url, issuer):
    """RFC 9728. Served by the MCP host; names the authorization server.

    The `resource` value must equal the MCP URL EXACTLY as a user types it into their
    client, path included — a mismatch here is the most common cause of a connector that
    completes sign-in and then fails every call.
    """
    return {
        "resource": resource_url,
        "authorization_servers": [issuer],
        "scopes_supported": list(SCOPES_SUPPORTED),
        "bearer_methods_supported": ["header"],
        "resource_documentation": issuer.rstrip("/") + "/docs",
    }


def authorization_server_metadata(issuer):
    """RFC 8414. Both registration paths are advertised on purpose.

    ChatGPT mandates Dynamic Client Registration; Claude prefers CIMD and only selects it
    when `client_id_metadata_document_supported` is true AND "none" appears in
    `token_endpoint_auth_methods_supported` — the second because its CIMD client
    authenticates as a public client. Advertising both means every client finds a path it
    can take, and DCR remains the fallback for anything that does not know CIMD.
    """
    base = issuer.rstrip("/")
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "revocation_endpoint": f"{base}/oauth/revoke",
        "scopes_supported": list(SCOPES_SUPPORTED),
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
        "code_challenge_methods_supported": ["S256"],
        "client_id_metadata_document_supported": True,
        "service_documentation": f"{base}/docs",
    }


# ---------------------------------------------------------------- redirect URIs

def _is_loopback(parsed):
    return (parsed.scheme == "http"
            and (parsed.hostname in ("127.0.0.1", "::1", "localhost")))


def redirect_uri_allowed(registered, requested):
    """Exact match, except that loopback ignores the port.

    RFC 8252 §7.3 requires the port-agnostic comparison for native clients, which bind an
    ephemeral port at runtime — Claude Code picks a new one every session, so an exact
    match would reject it every time. The exception is scoped to loopback and nothing else:
    ignoring the port on a public host would let a redirect land on a different service.
    """
    if requested in registered:
        return True
    want = urllib.parse.urlparse(requested)
    if not _is_loopback(want):
        return False
    for candidate in registered:
        got = urllib.parse.urlparse(candidate)
        if (_is_loopback(got) and got.hostname == want.hostname
                and got.path == want.path):
            return True
    return False


def _valid_redirect(uri):
    """HTTPS everywhere, with loopback HTTP allowed for native clients.

    A redirect to plain http on a public host would put the authorization code on the wire
    in clear text, which is the whole attack the code exchange exists to prevent.
    """
    if not isinstance(uri, str) or len(uri) > 2000:
        return False
    p = urllib.parse.urlparse(uri)
    if p.fragment:
        return False                       # RFC 6749 §3.1.2 forbids a fragment
    if p.scheme == "https" and p.hostname:
        return True
    if _is_loopback(p):
        return True
    # A private-use scheme (com.example.app:/cb) is how a mobile app receives a redirect.
    return bool(p.scheme and "." in p.scheme and not p.scheme.startswith("http"))


# ---------------------------------------------------------------- clients

def register(body, ip_hash=None, now=None):
    """RFC 7591 Dynamic Client Registration. Returns the response document.

    Open by necessity — the RFC defines no authenticated mode and ChatGPT requires it. It
    is safe because a client id on its own grants nothing: it cannot read anything until a
    real user completes a consent screen for it. What it does cost is a database row, so
    registration is rate-limited per source address.
    """
    now = time.time() if now is not None else time.time()
    if not isinstance(body, dict):
        raise OAuthError("invalid_client_metadata", "the body must be a JSON object")
    uris = body.get("redirect_uris")
    if not isinstance(uris, list) or not uris:
        raise OAuthError("invalid_redirect_uri",
                         "redirect_uris is required and must be a non-empty array")
    if len(uris) > 10:
        raise OAuthError("invalid_redirect_uri", "at most 10 redirect_uris")
    for uri in uris:
        if not _valid_redirect(uri):
            raise OAuthError(
                "invalid_redirect_uri",
                f"{uri!r} is not acceptable: use https, a loopback http address, or a "
                f"private-use scheme, and no fragment")
    if ip_hash is not None:
        seen = store.oauth_registrations_since(ip_hash, time.time() - 3600)
        if seen >= REGISTRATIONS_PER_IP_PER_HOUR:
            raise OAuthError("invalid_request",
                             f"{seen} clients registered from this address in the last "
                             f"hour; the limit is {REGISTRATIONS_PER_IP_PER_HOUR}",
                             status=429)

    # PUBLIC CLIENT BY DEFAULT. A browser assistant or a CLI cannot keep a secret, and
    # issuing one anyway invites a client to rely on it as though it were confidential.
    # PKCE is what secures these, and it is mandatory below.
    method = body.get("token_endpoint_auth_method", "none")
    if method not in ("none", "client_secret_post", "client_secret_basic"):
        raise OAuthError("invalid_client_metadata",
                         f"unsupported token_endpoint_auth_method {method!r}")
    secret = None if method == "none" else "cs_" + secrets.token_urlsafe(32)

    client_id = store.register_oauth_client(
        uris, client_name=(body.get("client_name") or "")[:200] or None,
        client_uri=(body.get("client_uri") or "")[:500] or None,
        secret=secret, ip_hash=ip_hash)
    out = {
        "client_id": client_id,
        "client_id_issued_at": int(time.time()),
        "redirect_uris": uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": method,
        "scope": " ".join(SCOPES_SUPPORTED),
    }
    if body.get("client_name"):
        out["client_name"] = body["client_name"]
    if secret:
        # No expiry: rotating a secret we cannot notify anyone about would just break
        # working clients silently.
        out["client_secret"] = secret
        out["client_secret_expires_at"] = 0
    return out


def _fetch_cimd(client_id):
    """Resolve a Client ID Metadata Document.

    THE DOCUMENT IS SELF-ASSERTED — anyone can host one — so it is checked for the two
    properties that make it meaningful rather than trusted wholesale: it must be
    self-referential (its own client_id equals the URL it was served from), and every
    redirect_uri must be same-origin with it. Without the second check, any site could
    publish a document redirecting to somebody else's callback.
    """
    parsed = urllib.parse.urlparse(client_id)
    if parsed.scheme != "https" or not parsed.hostname:
        raise OAuthError("invalid_client", "a CIMD client_id must be an https URL")
    req = urllib.request.Request(client_id, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=CIMD_TIMEOUT) as r:
            raw = r.read(CIMD_MAX_BYTES + 1)
    except Exception as exc:                                       # noqa: BLE001
        raise OAuthError("invalid_client",
                         f"could not fetch the client_id document: {exc}") from exc
    if len(raw) > CIMD_MAX_BYTES:
        raise OAuthError("invalid_client", "the client_id document is too large")
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        raise OAuthError("invalid_client",
                         "the client_id document is not JSON") from exc
    if not isinstance(doc, dict) or doc.get("client_id") != client_id:
        raise OAuthError("invalid_client",
                         "the client_id document is not self-referential")
    uris = doc.get("redirect_uris")
    if not isinstance(uris, list) or not uris:
        raise OAuthError("invalid_client",
                         "the client_id document declares no redirect_uris")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    for uri in uris:
        p = urllib.parse.urlparse(uri)
        if _is_loopback(p):
            continue                       # native clients; port-agnostic match applies
        if f"{p.scheme}://{p.netloc}" != origin:
            raise OAuthError(
                "invalid_client",
                f"redirect_uri {uri!r} is not same-origin with the client_id document")
    return {"client_id": client_id, "redirect_uris": uris,
            "client_name": doc.get("client_name"),
            "client_uri": doc.get("client_uri") or origin,
            "client_secret_hash": None,
            # The display name a consent screen may show. For CIMD this is the HOST, not
            # the document's client_name, because the document is self-asserted and a
            # familiar-looking name is exactly what a phishing client would put there.
            "display": parsed.hostname, "is_cimd": True}


def resolve_client(client_id):
    """A registered client, or a CIMD one fetched on the spot."""
    if not client_id:
        raise OAuthError("invalid_client", "client_id is required")
    if client_id.startswith("https://"):
        return _fetch_cimd(client_id)
    row = store.oauth_client(client_id)
    if row is None:
        raise OAuthError("invalid_client", f"unknown client_id {client_id!r}")
    row = dict(row)
    row["redirect_uris"] = json.loads(row["redirect_uris"])
    row["is_cimd"] = False
    row["display"] = row.get("client_name") or client_id
    return row


# ---------------------------------------------------------------- authorize

def _check_pkce(challenge, method):
    """S256 only.

    OAuth 2.1 removes `plain`, and it should: `plain` puts the verifier in the same
    redirect as the code, so an attacker who can read one can read the other and the whole
    mechanism buys nothing.
    """
    if not challenge:
        raise OAuthError("invalid_request",
                         "code_challenge is required — this server requires PKCE")
    if method not in (None, "", "S256"):
        raise OAuthError("invalid_request",
                         f"code_challenge_method {method!r} is not supported; use S256")
    if not (43 <= len(challenge) <= 128):
        raise OAuthError("invalid_request", "code_challenge is not a valid S256 challenge")


def begin(params):
    """Validate an /authorize request. -> a context for the consent screen.

    Raises OAuthError for anything the client got wrong. The CALLER decides how to report
    it, and the distinction matters: an error about the redirect_uri or the client_id must
    be shown on our own page, never bounced to the redirect, because a bad redirect_uri is
    precisely the case where we must not send anything to that address.
    """
    client = resolve_client(params.get("client_id"))
    redirect_uri = params.get("redirect_uri")
    if not redirect_uri:
        raise OAuthError("invalid_request", "redirect_uri is required")
    if not redirect_uri_allowed(client["redirect_uris"], redirect_uri):
        raise OAuthError(
            "invalid_request",
            f"redirect_uri {redirect_uri!r} is not registered for this client")
    if params.get("response_type") != "code":
        raise OAuthError("unsupported_response_type",
                         "only response_type=code is supported")
    _check_pkce(params.get("code_challenge"), params.get("code_challenge_method"))

    requested = (params.get("scope") or SCOPE).split()
    unknown = [s for s in requested if s not in SCOPES_SUPPORTED]
    if unknown:
        raise OAuthError("invalid_scope", f"unknown scope(s): {' '.join(unknown)}")
    granted = " ".join(s for s in SCOPES_SUPPORTED if s in requested) or SCOPE
    return {"client": client, "redirect_uri": redirect_uri, "scope": granted,
            "state": params.get("state"), "code_challenge": params["code_challenge"]}


def issue_code(ctx, account_id):
    code = "mcp_ac_" + secrets.token_urlsafe(32)
    store.save_oauth_code(code, ctx["client"]["client_id"], account_id,
                          ctx["redirect_uri"], ctx["code_challenge"], ctx["scope"])
    return code


def redirect_with(redirect_uri, **params):
    """Append parameters to a redirect, preserving any the client already put there."""
    parts = list(urllib.parse.urlparse(redirect_uri))
    query = urllib.parse.parse_qsl(parts[4], keep_blank_values=True)
    query += [(k, v) for k, v in params.items() if v is not None]
    parts[4] = urllib.parse.urlencode(query)
    return urllib.parse.urlunparse(parts)


# ---------------------------------------------------------------- token

def _verify_pkce(verifier, challenge):
    if not verifier:
        raise OAuthError("invalid_grant", "code_verifier is required")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    expect = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    if not secrets.compare_digest(expect, challenge):
        raise OAuthError("invalid_grant", "code_verifier does not match code_challenge")


def _authenticate_client(client, presented_secret):
    stored = client.get("client_secret_hash")
    if stored is None:
        return                              # public client: PKCE is the proof
    if not presented_secret or store._hash(presented_secret) != stored:
        raise OAuthError("invalid_client", "client authentication failed", status=401)


def exchange_code(form):
    """authorization_code -> tokens."""
    code = form.get("code")
    if not code:
        raise OAuthError("invalid_request", "code is required")
    row, reason = store.consume_oauth_code(code)
    if reason == "replayed":
        # OAuth 2.1: a code presented twice must be treated as compromise, and everything
        # the first presentation produced has to die with it. Refusing only the second
        # attempt would leave whoever stole the code holding live tokens.
        store.revoke_oauth_family(row["family_id"])
        raise OAuthError("invalid_grant",
                         "this authorization code has already been used; every token "
                         "issued from it has been revoked")
    if reason is not None:
        raise OAuthError("invalid_grant", f"the authorization code is {reason}")

    client = resolve_client(form.get("client_id") or row["client_id"])
    if client["client_id"] != row["client_id"]:
        raise OAuthError("invalid_grant", "this code was issued to a different client")
    _authenticate_client(client, form.get("client_secret"))
    if form.get("redirect_uri") and form["redirect_uri"] != row["redirect_uri"]:
        raise OAuthError("invalid_grant",
                         "redirect_uri does not match the one used to get this code")
    _verify_pkce(form.get("code_verifier"), row["code_challenge"])
    return _mint(row["client_id"], row["account_id"], row["scope"], row["family_id"])


def refresh(form):
    """refresh_token -> tokens, with rotation and reuse detection."""
    token = form.get("refresh_token")
    if not token:
        raise OAuthError("invalid_request", "refresh_token is required")
    row, reason = store.oauth_token(token, "refresh")
    if row is not None and row.get("rotated_at"):
        # A rotated token used again means a copy is loose. Kill the family: the thief and
        # the legitimate client are indistinguishable from here, and letting both continue
        # is the one outcome that is certainly wrong.
        store.revoke_oauth_family(row["family_id"])
        raise OAuthError("invalid_grant",
                         "this refresh token has already been rotated; the grant has "
                         "been revoked. Ask the user to reconnect.")
    if reason is not None:
        raise OAuthError("invalid_grant", f"the refresh token is {reason}")
    client = resolve_client(form.get("client_id") or row["client_id"])
    if client["client_id"] != row["client_id"]:
        raise OAuthError("invalid_grant", "this token was issued to a different client")
    _authenticate_client(client, form.get("client_secret"))
    store.rotate_oauth_refresh(token)
    return _mint(row["client_id"], row["account_id"], row["scope"], row["family_id"])


def _mint(client_id, account_id, scope, family_id):
    access = store.OAUTH_ACCESS_PREFIX + secrets.token_urlsafe(32)
    store.save_oauth_token(access, "access", client_id, account_id, scope, family_id)
    out = {"access_token": access, "token_type": "Bearer",
           "expires_in": int(store.ACCESS_TTL), "scope": scope}
    # A refresh token only when the user actually consented to offline access. Handing one
    # out regardless would make the scope a decoration.
    if OFFLINE in scope.split():
        rt = store.OAUTH_REFRESH_PREFIX + secrets.token_urlsafe(32)
        store.save_oauth_token(rt, "refresh", client_id, account_id, scope, family_id)
        out["refresh_token"] = rt
    return out


def revoke(form):
    """RFC 7009. Always reports success — telling a caller that a token they presented is
    unknown to us would turn this into an oracle for guessing valid tokens."""
    token = (form.get("token") or "").strip()
    for kind in ("access", "refresh"):
        row, _ = store.oauth_token(token, kind)
        if row is not None:
            store.revoke_oauth_family(row["family_id"])
            break
    return {}


# ---------------------------------------------------------------- the 401 challenge

def challenge_header(resource_metadata_url, scope=SCOPE,
                     description="Authentication required"):
    """The header that turns a refusal into a sign-in prompt.

    A 200 carrying a tool error is what this replaces, and the difference is total: the
    model reads a tool error as a result and moves on, so the user sees "please sign in"
    as text and has no way to. Only a transport-level 401 makes a client pause, run the
    OAuth flow and retry the same call.
    """
    return (f'Bearer error="invalid_token", error_description="{description}", '
            f'resource_metadata="{resource_metadata_url}", scope="{scope}"')
