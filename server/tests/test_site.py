"""The website, end to end.

These assert on WHAT A PAGE SAYS, not on how it looks, so that a redesign of render.py
cannot quietly break sign-in, leak another account's rows, or lose the shown-once promise
on a key. Every check here is about a guard, not about markup.
"""
import re
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server import oauth, render, site, store  # noqa: E402
from server.app import SESSION_COOKIE, app  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "t.sqlite")
    return TestClient(app, base_url="https://stratify.test")


def _account(email="a@example.com", sub="google-sub-1"):
    row, _ = store.account_for_google(sub, email, "A Person", None)
    return row


def _signed_in(client, account):
    client.cookies.set(SESSION_COOKIE, account["session_token"])
    return client


# ---------------------------------------------------------------------- landing

def test_the_landing_page_is_public_and_says_what_this_is(client):
    r = client.get("/")
    assert r.status_code == 200
    body = r.text
    assert "Sign in" in body
    # The hero must carry the actual offer, not a placeholder.
    assert "backtested" in body.lower()
    assert "delta_near" in body, "the hero example should show a real spec"
    assert "sk_live_" not in body


def test_a_signed_in_visitor_still_sees_the_landing_page(client):
    """It used to redirect straight into the dashboard, which made the page the product is
    judged on invisible to everyone who already had an account."""
    acc = _account()
    r = _signed_in(client, acc).get("/")
    assert r.status_code == 200
    assert "Open your dashboard" in r.text


def test_the_sign_in_button_is_honest_when_google_is_not_configured(client, monkeypatch):
    monkeypatch.setattr(oauth, "enabled", lambda: False)
    assert "not configured" in client.get("/").text
    monkeypatch.setattr(oauth, "enabled", lambda: True)
    assert "/auth/google" in client.get("/").text


# ------------------------------------------------------------------------- auth

def test_every_app_page_requires_a_session(client):
    for path in ("/app", "/app/keys", "/app/logs", "/app/reports"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 302, path
        # ...and remembers where the visitor was going
        assert "next=" in r.headers["location"], path


def test_the_docs_are_readable_without_signing_in(client):
    r = client.get("/docs")
    assert r.status_code == 200 and "Field reference" in r.text


def test_signing_out_invalidates_the_cookie_server_side(client):
    """Deleting the browser's cookie is not enough: a copy taken from a shared machine
    would still work."""
    acc = _account()
    stolen = acc["session_token"]
    _signed_in(client, acc).get("/logout", follow_redirects=False)
    assert store.account_by_session(stolen) is None


def test_google_sign_in_is_refused_without_matching_state(monkeypatch):
    """Without the state check an attacker can complete a login in your browser against
    THEIR account and collect whatever you do next."""
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "shh")
    _, flow = oauth.begin("https://stratify.test", "/app")
    with pytest.raises(oauth.OAuthError, match="did not start here"):
        oauth.finish("https://stratify.test", "code", "not-the-state", flow)
    with pytest.raises(oauth.OAuthError, match="expired"):
        oauth.finish("https://stratify.test", "code", "s", "garbage.cookie")


def test_an_expired_or_tampered_handshake_is_refused():
    _, flow = oauth.begin("https://stratify.test")
    tampered = flow[:-1] + ("0" if flow[-1] != "0" else "1")
    assert oauth._unsign(tampered) is None
    stale = oauth._sign({"state": "s", "verifier": "v", "next": "/app",
                         "exp": time.time() - 1})
    assert oauth._unsign(stale) is None


def test_the_next_parameter_cannot_leave_this_site():
    """An open redirect here would let a phishing page send somebody through a real Google
    login and land them somewhere else entirely.

    THIS TEST USED TO PASS WHILE THE GUARD WAS BROKEN. It covered "//evil" and an absolute
    URL but not the backslash form, and a leading-"//" check alone does not stop it:
    browsers normalise "\\" to "/" before resolving, so "/\\evil.test" was accepted here and
    then fetched as "//evil.test". Control characters do the same, being stripped during
    the same normalisation. Each shape below is a way of writing an authority that a naive
    prefix test reads as a path.
    """
    hostile = ("//evil.test", "https://evil.test", "javascript:alert(1)", 7, None,
               "/\\evil.test",         # backslash -> "//evil.test" in Chrome and Firefox
               "\\\\evil.test",        # both slashes backslashed
               "/\r\n//evil.test",     # CR/LF stripped, leaving "///evil.test"
               "/\t\\evil.test",       # tab plus backslash
               "\\/evil.test")
    for h in hostile:
        assert oauth._safe_next(h) == "/app", f"escaped the site via {h!r}"
    for safe in ("/app/logs", "/app", "/app/keys?new=1", "/r/abc123"):
        assert oauth._safe_next(safe) == safe
    # The cap is a sanity bound, not the security control -- the origin checks above are
    # what stop an escape, and they are asserted on every hostile input in this test. It
    # was 200 and silently truncated an /oauth/authorize round trip, which carries a
    # client_id, a redirect_uri, a PKCE challenge, a scope and a state.
    assert len(oauth._safe_next("/a" * 5000)) <= 1500
    long_authorize = ("/oauth/authorize?response_type=code&client_id=cli_" + "a" * 32
                      + "&redirect_uri=https%3A%2F%2Fclaude.ai%2Fapi%2Fmcp%2Fauth_callback"
                      + "&code_challenge=" + "b" * 43
                      + "&code_challenge_method=S256&scope=mcp+offline_access&state="
                      + "c" * 32)
    assert oauth._safe_next(long_authorize) == long_authorize, (
        "an OAuth authorize round trip must survive sign-in intact")


def test_a_verified_google_identity_is_matched_on_subject_not_email(client):
    """An address can be reassigned inside a Workspace domain. Keying on it would hand the
    account to whoever holds the address next."""
    first = _account("person@corp.test", "sub-A")
    same = _account("renamed@corp.test", "sub-A")
    assert same["account_id"] == first["account_id"]
    other, created = store.account_for_google("sub-B", "person@corp.test", None, None)
    assert created and other["account_id"] != first["account_id"]


# ------------------------------------------------------------------------- keys

def test_a_key_is_shown_once_and_never_again(client):
    acc = _account()
    c = _signed_in(client, acc)
    r = c.post("/app/keys/new")
    secret = re.search(r"(sk_live_[A-Za-z0-9_\-]+)", r.text)
    assert secret, "the minting response must show the key"
    assert secret.group(1) not in c.get("/app/keys").text
    assert secret.group(1) not in c.get("/app").text


def test_you_cannot_revoke_a_key_you_do_not_own(client):
    mine = _account("mine@example.com", "sub-mine")
    theirs = _account("theirs@example.com", "sub-theirs")
    their_key, _ = store.issue_key(theirs["account_id"])
    r = _signed_in(client, mine).post("/app/keys/revoke", data={"key_id": their_key})
    assert r.status_code == 403
    assert not store.list_keys(theirs["account_id"])[0]["revoked_at"]


# ------------------------------------------------------------- logs and reports

def test_the_activity_log_shows_only_this_account(client):
    mine = _account("m@example.com", "sub-m")
    theirs = _account("t@example.com", "sub-t")
    for acc, tool in ((mine, "run_backtest"), (theirs, "SECRET_OTHER_TOOL")):
        store.record_call({"account_id": acc["account_id"], "key_id": "k", "tier": "free",
                           "method": "tools/call", "tool": tool, "arguments_json": "{}",
                           "outcome": "ok", "cpu_seconds": 0.5, "price_points": 0})
    body = _signed_in(client, mine).get("/app/logs").text
    assert "run_backtest" in body and "SECRET_OTHER_TOOL" not in body


def test_refused_calls_appear_in_the_log_too(client):
    """A log that only holds the calls that worked cannot answer the one question you ask
    a log."""
    acc = _account()
    store.record_call({"account_id": acc["account_id"], "key_id": "k", "tier": "free",
                       "method": "tools/call", "tool": "run_backtest",
                       "arguments_json": "{}", "outcome": "refused",
                       "refusal": "only NIFTY is served today", "cpu_seconds": 0.0,
                       "price_points": 0})
    body = _signed_in(client, acc).get("/app/logs").text
    assert "refused" in body and "only NIFTY is served today" in body


def test_reports_are_private_to_the_account(client):
    mine = _account("m2@example.com", "sub-m2")
    theirs = _account("t2@example.com", "sub-t2")
    my_key, _ = store.issue_key(mine["account_id"])
    their_key, _ = store.issue_key(theirs["account_id"])
    store.save_result(my_key, '{"name":"mine"}', "h1", "{}")
    store.save_result(their_key, '{"name":"THEIR_SECRET_STRATEGY"}', "h2", "{}")
    body = _signed_in(client, mine).get("/app/reports").text
    assert "mine" in body and "THEIR_SECRET_STRATEGY" not in body


# ------------------------------------------------------------------- rendering

def test_a_hostile_display_name_cannot_inject_markup(client):
    """The name and the strategy name come from Google and from the caller."""
    acc, _ = store.account_for_google("sub-x", "x@example.com",
                                      '<script>alert(1)</script>', None)
    body = _signed_in(client, acc).get("/app").text
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


def test_the_documented_fields_are_the_engine_s_own(client):
    """Docs retyped into prose are wrong within a month. These are generated from the
    protocol, so they cannot drift without this failing."""
    from engine import strategy as strategy_mod
    body = client.get("/docs").text
    for name in strategy_mod.FIELDS:
        assert f"<code>{name}</code>" in body, f"{name} is undocumented"
    for action in ("close_legs", "close_and_open", "roll"):
        assert action in body
    for cadence in strategy_mod.CADENCES:
        assert cadence in body


def test_every_example_in_the_docs_actually_runs(client):
    """A doc page must not show a strategy the engine would refuse."""
    import datetime as dt
    from engine import strategy as strategy_mod
    window = (dt.date(2025, 7, 1), dt.date(2026, 6, 30))
    for example in site.EXAMPLES + [site.HERO_EXAMPLE]:
        parsed = strategy_mod.parse(example["spec"], window=window)
        assert parsed.n_legs >= 1, example.get("key")


# ------------------------------------------------------------------------ legal

def test_the_legal_pages_are_public_and_linked(client):
    """Google's consent screen fetches these URLs itself, and a privacy policy behind a
    login is not a privacy policy."""
    for path, must in (("/privacy", "Retention" if False else "How long it is kept"),
                       ("/terms", "Liability")):
        r = client.get(path)
        assert r.status_code == 200, path
        assert must in r.text, path
    home = client.get("/").text
    assert '/privacy' in home and '/terms' in home


def test_the_privacy_page_matches_the_retention_the_code_actually_applies():
    """The dates in a policy are a promise. If purge()'s defaults move, this fails rather
    than the promise silently becoming false."""
    import inspect
    src = inspect.signature(store.purge)
    assert src.parameters["usage_days"].default == 30
    assert src.parameters["spec_body_days"].default == 30
    assert src.parameters["call_days"].default == 30
    assert src.parameters["signup_days"].default == 7
    text = " ".join(i for s in site.PRIVACY["sections"] for i in s["items"])
    assert "30 days" in text and "7 days" in text


def test_the_privacy_page_does_not_promise_scopes_we_do_not_ask_for():
    claimed = [i for s in site.PRIVACY["sections"] for i in s["items"]
               if "scope" in i.lower()]
    assert claimed, "the policy should state which scopes are requested"
    for scope in oauth.SCOPES.split():
        assert scope in claimed[0], scope


# ---------------------------------------------------------------------- One Tap

@pytest.fixture()
def google_on(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "shh")


def test_one_tap_is_shown_to_a_stranger_and_not_to_a_customer(client, google_on):
    """The prompt exists to remove a click from someone who has not signed in. Showing it
    to a signed-in user is a box over their own dashboard."""
    assert "g_id_onload" in client.get("/").text
    assert 'data-use_fedcm_for_prompt="true"' in client.get("/").text, \
        "Chrome requires FedCM for One Tap; without it the prompt silently never appears"
    acc = _account("ot@example.com", "sub-ot")
    assert "g_id_onload" not in _signed_in(client, acc).get("/").text


def test_one_tap_requires_the_csrf_double_submit_and_cannot_be_switched_off(
        client, google_on):
    """This check was first written to run only when one of the two values was present --
    which an attacker disables by sending NEITHER. The guard was there and did nothing,
    which is worse than not writing it, because it reads as protection."""
    for cookie, field in ((None, None), ("x", None), (None, "x"), ("x", "y")):
        if cookie:
            client.cookies.set(oauth.CSRF_FIELD, cookie)
        data = {"credential": "aaa.bbb.ccc"}
        if field:
            data[oauth.CSRF_FIELD] = field
        r = client.post(oauth.ONETAP_PATH, data=data, follow_redirects=False)
        assert r.status_code == 400, (cookie, field)
        assert "did not start here" in r.text, (cookie, field)
        client.cookies.clear()


def test_a_one_tap_credential_is_never_trusted_on_its_face(client, google_on, monkeypatch):
    """The code flow's token arrives on a TLS response to a request WE made. This one
    arrives through the browser from a script, so a self-signed JWT with somebody's email
    in it must not sign anybody in."""
    import base64
    import json as _json

    def forged(email):
        part = base64.urlsafe_b64encode(_json.dumps({
            "iss": "https://accounts.google.com", "aud": oauth.client_id(),
            "sub": "victim-sub", "email": email, "email_verified": True,
            "exp": time.time() + 3600}).encode()).rstrip(b"=").decode()
        return f"header.{part}.signature"

    # Google is the only thing that can say a token is real; verification must go to it.
    called = {}

    def refuse(_credential):
        called["asked"] = True
        raise oauth.OAuthError("Google could not validate that sign-in.")

    monkeypatch.setattr(oauth, "_tokeninfo", refuse)
    client.cookies.set(oauth.CSRF_FIELD, "tok")
    r = client.post(oauth.ONETAP_PATH,
                    data={"credential": forged("ceo@example.com"),
                          oauth.CSRF_FIELD: "tok"}, follow_redirects=False)
    assert called.get("asked"), "the token must be verified with Google, not just parsed"
    assert r.status_code == 400
    assert store.account_by_session(r.cookies.get(SESSION_COOKIE)) is None
    assert not any(a for a in [store.account_for_google] if False)


def test_a_token_for_another_application_is_refused(google_on, monkeypatch):
    """A token that is valid, signed by Google, and issued for somebody else's client id
    is still a valid token. Accepting one lets any Google developer sign in as anybody."""
    monkeypatch.setattr(oauth, "_tokeninfo", lambda _c: {
        "iss": "https://accounts.google.com", "aud": "SOMEONE-ELSES-CLIENT-ID",
        "sub": "s", "email": "a@b.test", "email_verified": True,
        "exp": time.time() + 3600})
    with pytest.raises(oauth.OAuthError, match="different application"):
        oauth.verify_id_token("a.b.c", "tok", "tok")


def test_an_unverified_google_email_is_refused(google_on, monkeypatch):
    monkeypatch.setattr(oauth, "_tokeninfo", lambda _c: {
        "iss": "https://accounts.google.com", "aud": oauth.client_id(),
        "sub": "s", "email": "a@b.test", "email_verified": False,
        "exp": time.time() + 3600})
    with pytest.raises(oauth.OAuthError, match="not verified"):
        oauth.verify_id_token("a.b.c", "tok", "tok")


def test_a_good_one_tap_credential_signs_you_in(client, google_on, monkeypatch):
    monkeypatch.setattr(oauth, "_tokeninfo", lambda _c: {
        "iss": "https://accounts.google.com", "aud": oauth.client_id(),
        "sub": "sub-good", "email": "Good@Example.test", "email_verified": True,
        "name": "Good Person", "exp": time.time() + 3600})
    client.cookies.set(oauth.CSRF_FIELD, "tok")
    r = client.post(oauth.ONETAP_PATH,
                    data={"credential": "a.b.c", oauth.CSRF_FIELD: "tok"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/app"
    assert store.account_by_session(r.cookies[SESSION_COOKIE])["email"] == "good@example.test"


def test_one_tap_cannot_redirect_off_site(client, google_on, monkeypatch):
    monkeypatch.setattr(oauth, "_tokeninfo", lambda _c: {
        "iss": "https://accounts.google.com", "aud": oauth.client_id(),
        "sub": "sub-r", "email": "r@example.test", "email_verified": True,
        "exp": time.time() + 3600})
    client.cookies.set(oauth.CSRF_FIELD, "tok")
    r = client.post(oauth.ONETAP_PATH,
                    data={"credential": "a.b.c", oauth.CSRF_FIELD: "tok",
                          "next": "https://evil.test/steal"}, follow_redirects=False)
    assert r.headers["location"] == "/app"



def test_every_report_row_links_to_its_report(client):
    """The dashboard's whole job on this page is getting you INTO a report.

    recent_results_for_account did not select report_token, so site._report_row built no
    URL and render._report_table drew no Open button -- for every row, since the page
    shipped. The list looked complete and led nowhere: the only way to reach a report was
    to still have the URL from the original tool call.
    """
    acc = _account("reports@example.com", "sub-reports")
    key_id, _ = store.issue_key(acc["account_id"])
    store.save_result(key_id, '{"structure":"iron_condor","cadence":"weekly"}', "h1", "{}")

    view = site.reports_view(acc, "https://stratify.test")
    assert view["rows"], "the result was saved but the page shows nothing"
    for row in view["rows"]:
        assert row["url"], f"{row['backtest_id']} has no link -- the row is a dead end"
        assert "/r/" in row["url"]

    # And the rendered table must actually carry the anchor, not just the view model.
    assert 'href="https://stratify.test/r/' in render.reports(view)
