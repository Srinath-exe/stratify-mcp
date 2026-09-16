"""OAuth 2.1 authorization server.

This is the piece that lets ChatGPT, the Gemini web app and Anthropic's Connectors
Directory reach the service at all — every one of them refuses a bearer API key. It is
also the largest new attack surface the service has, so these tests are mostly about the
ways it could be abused rather than the happy path.

Each test names the attack it prevents. A test that only asserts the flow works would pass
just as happily against an implementation with no PKCE, no rotation and a redirect_uri
check that accepts anything.
"""
import base64
import hashlib
import json
import secrets
import sys
import tempfile
import urllib.parse
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server import app as app_mod, mcpauth, quota, store  # noqa: E402
from engine import honesty  # noqa: E402

REDIRECT = "https://claude.ai/api/mcp/auth_callback"


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(store, "DB_PATH", Path(d) / "service.sqlite")
        monkeypatch.setattr(honesty, "VARIANT_LOG", Path(d) / "variants.sqlite")
        monkeypatch.setattr(quota, "CONCURRENCY", quota.Concurrency())
        yield


@pytest.fixture
def client():
    return TestClient(app_mod.app, base_url="https://testserver")


@pytest.fixture
def account():
    account_id, _ = store.create_account("oauth@example.com")
    return account_id


@pytest.fixture
def signed_in(client, account):
    client.cookies.set("stratify_session", store.session_token(account))
    return account


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def register(client, **over):
    body = {"redirect_uris": [REDIRECT], "client_name": "Test Client",
            "token_endpoint_auth_method": "none"}
    body.update(over)
    return client.post("/oauth/register", json=body)


def authorize_params(client_id, challenge, **over):
    p = {"response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT,
         "code_challenge": challenge, "code_challenge_method": "S256",
         "scope": "mcp offline_access", "state": "st_" + secrets.token_hex(4)}
    p.update(over)
    return p


def full_grant(client, signed_in, scope="mcp offline_access"):
    verifier, challenge = pkce()
    client_id = register(client).json()["client_id"]
    params = authorize_params(client_id, challenge, scope=scope)
    r = client.post("/oauth/authorize", data={**params, "decision": "allow"},
                    follow_redirects=False)
    code = urllib.parse.parse_qs(
        urllib.parse.urlparse(r.headers["location"]).query)["code"][0]
    tok = client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": client_id,
        "redirect_uri": REDIRECT, "code_verifier": verifier}).json()
    return client_id, verifier, tok


# ---------------------------------------------------------------- discovery

def test_the_discovery_chain_resolves_end_to_end(client):
    """A client is handed nothing but a URL. Everything else it learns from this chain,
    which is why the same server works in Claude, ChatGPT and Gemini without three
    integrations — and why one broken link reads as "couldn't reach the MCP server"."""
    prm = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert prm["resource"].endswith("/mcp")
    issuer = prm["authorization_servers"][0]
    asm = client.get("/.well-known/oauth-authorization-server").json()
    assert asm["issuer"] == issuer
    for endpoint in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
        assert asm[endpoint].startswith(issuer), endpoint


def test_both_protected_resource_metadata_spellings_are_served(client):
    """RFC 9728 §3.1: a client whose resource URL has a path tries the suffixed form
    first. Serving only one is a discovery failure that looks like an outage."""
    a = client.get("/.well-known/oauth-protected-resource").json()
    b = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert a == b


def test_metadata_advertises_what_each_client_needs_to_select_a_path(client):
    """Claude picks CIMD only when BOTH the flag and "none" auth are advertised; ChatGPT
    needs a registration_endpoint. Dropping either strands one of them."""
    asm = client.get("/.well-known/oauth-authorization-server").json()
    assert asm["client_id_metadata_document_supported"] is True
    assert "none" in asm["token_endpoint_auth_methods_supported"]
    assert asm["registration_endpoint"]
    assert asm["code_challenge_methods_supported"] == ["S256"]


# ---------------------------------------------------------------- PKCE

def test_authorization_without_pkce_is_refused(client, signed_in):
    """Every client here is public — no secret survives shipping in a browser or a CLI —
    so the verifier is the only thing binding a redirect to whoever started it."""
    client_id = register(client).json()["client_id"]
    params = authorize_params(client_id, "x")
    del params["code_challenge"]
    r = client.get("/oauth/authorize", params=params)
    assert r.status_code == 400
    assert "PKCE" in r.text or "code_challenge" in r.text


def test_plain_pkce_is_refused(client, signed_in):
    """OAuth 2.1 removes `plain`, and it should: it puts the verifier in the same redirect
    as the code, so anyone who can read one can read the other."""
    _, challenge = pkce()
    client_id = register(client).json()["client_id"]
    r = client.get("/oauth/authorize", params=authorize_params(
        client_id, challenge, code_challenge_method="plain"))
    assert r.status_code == 400


def test_a_wrong_verifier_cannot_redeem_a_code(client, signed_in):
    """The attack: an authorization code stolen from a redirect. Without the verifier —
    which never left the legitimate client — it is worthless."""
    verifier, challenge = pkce()
    client_id = register(client).json()["client_id"]
    params = authorize_params(client_id, challenge)
    r = client.post("/oauth/authorize", data={**params, "decision": "allow"},
                    follow_redirects=False)
    code = urllib.parse.parse_qs(
        urllib.parse.urlparse(r.headers["location"]).query)["code"][0]
    bad = client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": client_id,
        "redirect_uri": REDIRECT, "code_verifier": secrets.token_urlsafe(48)})
    assert bad.status_code == 400
    assert bad.json()["error"] == "invalid_grant"


# ---------------------------------------------------------------- redirect URIs

def test_an_unregistered_redirect_uri_is_refused_on_our_own_page(client, signed_in):
    """And critically NOT bounced to that address: an unregistered redirect_uri is exactly
    the case where sending anything there would be the vulnerability."""
    _, challenge = pkce()
    client_id = register(client).json()["client_id"]
    r = client.get("/oauth/authorize", params=authorize_params(
        client_id, challenge, redirect_uri="https://evil.example.com/steal"),
        follow_redirects=False)
    assert r.status_code == 400
    assert "evil.example.com" not in r.headers.get("location", "")


def test_registration_refuses_a_plaintext_http_redirect(client):
    """A code delivered over plain http on a public host is a code on the wire in clear
    text, which is the whole attack the exchange exists to prevent."""
    r = register(client, redirect_uris=["http://evil.example.com/cb"])
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_redirect_uri"


def test_loopback_ignores_the_port_but_only_for_loopback():
    """RFC 8252 §7.3. Claude Code binds a fresh ephemeral port every session, so an exact
    match would reject it every time — but ignoring the port on a public host would let a
    redirect land on a different service entirely."""
    assert mcpauth.redirect_uri_allowed(
        ["http://localhost/callback"], "http://localhost:51793/callback")
    assert mcpauth.redirect_uri_allowed(
        ["http://127.0.0.1/callback"], "http://127.0.0.1:8123/callback")
    assert not mcpauth.redirect_uri_allowed(
        ["https://app.example.com/cb"], "https://app.example.com:8443/cb")
    assert not mcpauth.redirect_uri_allowed(
        ["http://localhost/callback"], "http://localhost:51793/other")


# ---------------------------------------------------------------- codes & tokens

def test_a_replayed_code_revokes_everything_it_produced(client, signed_in):
    """OAuth 2.1 requires this. A code presented twice means the first presentation was
    seen by someone who should not have seen it; refusing only the second attempt leaves
    the thief holding live tokens."""
    client_id, verifier, tok = full_grant(client, signed_in)
    assert tok["access_token"]

    # Replay the same code. The grant dies with it.
    verifier2, challenge2 = pkce()
    params = authorize_params(client_id, challenge2)
    r = client.post("/oauth/authorize", data={**params, "decision": "allow"},
                    follow_redirects=False)
    code = urllib.parse.parse_qs(
        urllib.parse.urlparse(r.headers["location"]).query)["code"][0]
    first = client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": client_id,
        "redirect_uri": REDIRECT, "code_verifier": verifier2}).json()
    again = client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": client_id,
        "redirect_uri": REDIRECT, "code_verifier": verifier2})
    assert again.json()["error"] == "invalid_grant"

    dead = client.post("/mcp", headers={"Authorization": "Bearer " + first["access_token"]},
                       json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                             "params": {"name": "describe_coverage", "arguments": {}}})
    assert dead.status_code == 401, "the replayed code's tokens must be revoked too"


def test_a_reused_refresh_token_revokes_the_family(client, signed_in):
    """Rotation is only useful with reuse detection. Once a rotated token reappears, the
    thief and the legitimate client are indistinguishable — letting both continue is the
    one outcome that is certainly wrong."""
    client_id, _, tok = full_grant(client, signed_in)
    rotated = client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
        "client_id": client_id}).json()
    assert rotated["refresh_token"] != tok["refresh_token"]

    reuse = client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
        "client_id": client_id})
    assert reuse.json()["error"] == "invalid_grant"

    r = client.post("/mcp", headers={"Authorization": "Bearer " + rotated["access_token"]},
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": "describe_coverage", "arguments": {}}})
    assert r.status_code == 401, "the whole family must die, not just the replayed token"


def test_a_refresh_token_is_only_issued_when_offline_access_was_granted(client, signed_in):
    """Otherwise the scope is a decoration: the user would be shown a choice that has no
    effect on what the client actually gets."""
    _, _, tok = full_grant(client, signed_in, scope="mcp")
    assert "refresh_token" not in tok
    assert tok["access_token"]


def test_client_credentials_grant_is_refused(client):
    """A machine-to-machine token with no consenting user is the credential that ends up
    in a public repository. Anthropic refuses it too."""
    r = client.post("/oauth/token", data={"grant_type": "client_credentials",
                                          "client_id": "x", "client_secret": "y"})
    assert r.status_code == 400
    assert r.json()["error"] == "unsupported_grant_type"


def test_a_code_issued_to_one_client_cannot_be_redeemed_by_another(client, signed_in):
    verifier, challenge = pkce()
    a = register(client).json()["client_id"]
    b = register(client).json()["client_id"]
    params = authorize_params(a, challenge)
    r = client.post("/oauth/authorize", data={**params, "decision": "allow"},
                    follow_redirects=False)
    code = urllib.parse.parse_qs(
        urllib.parse.urlparse(r.headers["location"]).query)["code"][0]
    bad = client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": b,
        "redirect_uri": REDIRECT, "code_verifier": verifier})
    assert bad.json()["error"] == "invalid_grant"


# ---------------------------------------------------------------- consent

def test_authorization_requires_a_signed_in_human(client):
    """This server issues tokens; it does not decide who people are. Identity comes from
    Google sign-in or an admin-issued password, so a signed-out visitor is sent to the
    sign-in chooser with the whole authorization request carried in `next`."""
    _, challenge = pkce()
    client_id = register(client).json()["client_id"]
    r = client.get("/oauth/authorize", params=authorize_params(client_id, challenge),
                   follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("/login?next=/oauth/authorize%3F")
    assert "code_challenge" in urllib.parse.unquote(r.headers["location"])


def test_declining_tells_the_client_instead_of_leaving_a_dead_tab(client, signed_in):
    _, challenge = pkce()
    client_id = register(client).json()["client_id"]
    params = authorize_params(client_id, challenge)
    r = client.post("/oauth/authorize", data={**params, "decision": "deny"},
                    follow_redirects=False)
    assert r.status_code == 302
    q = urllib.parse.parse_qs(urllib.parse.urlparse(r.headers["location"]).query)
    assert q["error"] == ["access_denied"]
    assert q["state"] == [params["state"]]


def test_state_is_returned_unchanged(client, signed_in):
    """The client's CSRF defence. Dropping it silently breaks a check the client is
    relying on, and it has no way to tell."""
    _, challenge = pkce()
    client_id = register(client).json()["client_id"]
    params = authorize_params(client_id, challenge, state="opaque-value-123")
    r = client.post("/oauth/authorize", data={**params, "decision": "allow"},
                    follow_redirects=False)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(r.headers["location"]).query)
    assert q["state"] == ["opaque-value-123"]


# ---------------------------------------------------------------- the account link

def test_an_oauth_token_and_an_api_key_reach_the_same_account(client, signed_in):
    """Two credentials, one shape. Everything downstream — quotas, tier binding, the audit
    log — reads the same fields, so neither path needs its own copy of that logic."""
    _, _, tok = full_grant(client, signed_in)
    key_id, api_key = store.issue_key(signed_in)

    def whoami(credential):
        r = client.post("/mcp", headers={"Authorization": f"Bearer {credential}"},
                        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "list_strategies", "arguments": {}}})
        return r.json()["result"]["structuredContent"]["account_id"]

    assert whoami(tok["access_token"]) == whoami(api_key) == signed_in


def test_oauth_calls_spend_the_same_quota_as_a_key(client, signed_in):
    """Otherwise OAuth is a way around the rate limit, which would make the limit
    decorative for exactly the clients most likely to loop."""
    _, _, tok = full_grant(client, signed_in)
    client.post("/mcp", headers={"Authorization": "Bearer " + tok["access_token"]},
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": "describe_coverage", "arguments": {}}})
    _billable, metadata, cpu, _pts = store.usage_since(signed_in, 0)
    assert metadata >= 1 and cpu > 0


def test_disconnecting_from_the_dashboard_ends_the_grant(client, signed_in):
    """A grant a user cannot withdraw is not consent. Revoking the family, not one token,
    is what actually ends it — a client holding a refresh token would otherwise mint a new
    access token seconds later."""
    client_id, _, tok = full_grant(client, signed_in)
    assert store.oauth_connections(signed_in)

    client.post("/app/connections/revoke", data={"client_id": client_id})
    assert store.oauth_connections(signed_in) == []

    r = client.post("/mcp", headers={"Authorization": "Bearer " + tok["access_token"]},
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": "describe_coverage", "arguments": {}}})
    assert r.status_code == 401
    refreshed = client.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
        "client_id": client_id})
    assert refreshed.json()["error"] == "invalid_grant", "refresh must die with the grant"


# ---------------------------------------------------------------- registration abuse

def test_registration_is_rate_limited_per_address(client, monkeypatch):
    """Open registration is required by the spec and by ChatGPT, and is safe because a
    client_id grants nothing before a human consents — but it is a free database write."""
    monkeypatch.setattr(mcpauth, "REGISTRATIONS_PER_IP_PER_HOUR", 3)
    codes = [register(client).status_code for _ in range(5)]
    assert codes[:3] == [201, 201, 201]
    assert codes[-1] == 429


def test_tokens_are_never_stored_in_a_readable_form(client, signed_in):
    """A database that leaks must not hand over live credentials. There is no reason to
    keep the plaintext: these are only ever compared against something a caller presents."""
    _, _, tok = full_grant(client, signed_in)
    raw = Path(store.DB_PATH).read_bytes()
    for secret in (tok["access_token"], tok["refresh_token"]):
        assert secret.encode() not in raw, "a live token is sitting in the database"
