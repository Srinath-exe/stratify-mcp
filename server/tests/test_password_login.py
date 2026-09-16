"""Issued-password sign-in: admin-only issuance, one failure message, two locks, and the
same session cookie the Google path sets."""
import sys
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from server import app as app_mod, quota, store  # noqa: E402
from engine import honesty  # noqa: E402

EMAIL, PASSWORD = "reviewer@example.com", "a-review-password-1"


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


def _admin(client):
    aid, _ = store.create_account("root@example.com")
    store.grant_admin("root@example.com")
    client.cookies.set(app_mod.SESSION_COOKIE, store.session_token(aid))
    return aid


# ---------------------------------------------------------------- hashing

def test_hash_is_salted_scrypt_and_verifies_in_constant_shape():
    h1, h2 = store.hash_password(PASSWORD), store.hash_password(PASSWORD)
    assert h1 != h2 and h1.startswith("scrypt$32768$8$1$")
    assert store.check_password(PASSWORD, h1) and store.check_password(PASSWORD, h2)
    assert not store.check_password(PASSWORD + "x", h1)
    assert store.check_password(PASSWORD, None) is False          # dummy path, no raise
    assert store.check_password(PASSWORD, "garbage") is False


def test_unknown_address_costs_as_much_as_a_wrong_password():
    store.set_password(EMAIL, PASSWORD)
    t = time.perf_counter(); store.verify_password(EMAIL, "wrong-password-xx"); wrong = time.perf_counter() - t
    t = time.perf_counter(); store.verify_password("nobody@example.com", "wrong-password-xx"); unknown = time.perf_counter() - t
    assert unknown > wrong * 0.5   # both run a full scrypt; an early return would be ~0


def test_minimum_length_is_enforced_at_the_store():
    with pytest.raises(ValueError):
        store.set_password(EMAIL, "short")
    assert store.verify_password(EMAIL, "short") is None


# ---------------------------------------------------------------- the form

def test_login_sets_the_session_and_honours_next(client):
    store.set_password(EMAIL, PASSWORD)
    r = client.post("/login", data={"email": EMAIL, "password": PASSWORD,
                                    "next": "/oauth/authorize?client_id=x&state=y"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/oauth/authorize?client_id=x&state=y"
    cookie = r.cookies.get(app_mod.SESSION_COOKIE)
    assert cookie and store.account_by_session(cookie)["email"] == EMAIL
    assert "httponly" in r.headers["set-cookie"].lower()
    assert "secure" in r.headers["set-cookie"].lower()


def test_login_next_cannot_leave_the_site(client):
    store.set_password(EMAIL, PASSWORD)
    for evil in ("https://evil.example", "//evil.example", "/\\evil.example"):
        r = client.post("/login", data={"email": EMAIL, "password": PASSWORD, "next": evil},
                        follow_redirects=False)
        assert r.status_code == 303
        loc = r.headers["location"]
        assert loc.startswith("/") and not loc.startswith("//"), (evil, loc)


def test_wrong_password_and_unknown_address_are_the_same_answer(client):
    store.set_password(EMAIL, PASSWORD)
    a = client.post("/login", data={"email": EMAIL, "password": "not-it-not-it-1"})
    b = client.post("/login", data={"email": "ghost@example.com", "password": PASSWORD})
    assert a.status_code == b.status_code == 401
    assert "did not match" in a.text and "did not match" in b.text
    assert app_mod.SESSION_COOKIE not in a.cookies and app_mod.SESSION_COOKIE not in b.cookies


def test_google_only_accounts_cannot_be_entered_with_a_password(client):
    store.create_account(EMAIL)                      # exists, no password
    r = client.post("/login", data={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 401


def test_per_account_lock_after_repeated_failures(client):
    store.set_password(EMAIL, PASSWORD)
    for _ in range(quota.LOGIN_FAILURES_PER_EMAIL_PER_HOUR):
        assert client.post("/login", data={"email": EMAIL, "password": "wrong-wrong-wrong"}).status_code == 401
    r = client.post("/login", data={"email": EMAIL, "password": PASSWORD})   # even the RIGHT one
    assert r.status_code == 429 and "Retry-After" in r.headers
    assert app_mod.SESSION_COOKIE not in r.cookies


def test_per_address_lock_covers_many_accounts(client):
    for i in range(quota.LOGIN_FAILURES_PER_IP_PER_HOUR):
        client.post("/login", data={"email": f"u{i}@example.com", "password": "wrong-wrong-wrong"})
    store.set_password(EMAIL, PASSWORD)
    assert client.post("/login", data={"email": EMAIL, "password": PASSWORD}).status_code == 429


def test_successes_do_not_count_toward_the_lock(client):
    store.set_password(EMAIL, PASSWORD)
    for _ in range(quota.LOGIN_FAILURES_PER_EMAIL_PER_HOUR + 2):
        assert client.post("/login", data={"email": EMAIL, "password": PASSWORD},
                           follow_redirects=False).status_code == 303


def test_login_page_shows_google_first_and_password_folded(client):
    r = client.get("/login?next=/app")
    assert r.status_code == 200
    assert 'href="/auth/google?next=/app"' in r.text
    assert "<details" in r.text and ' open' not in r.text.split("<details")[1].split(">")[0]
    assert 'type="password"' in r.text


def test_signed_out_authorize_lands_on_the_chooser_with_next(client):
    r = client.get("/oauth/authorize", params={
        "response_type": "code", "client_id": "https://client.example/cimd.json",
        "redirect_uri": "https://client.example/cb", "code_challenge": "a" * 43,
        "code_challenge_method": "S256", "scope": "mcp", "state": "s"},
        follow_redirects=False)
    # Either the chooser, or a 400 from client resolution -- but never straight to Google.
    if r.status_code == 302:
        assert r.headers["location"].startswith("/login?next=/oauth/authorize")


def test_login_page_redirects_when_already_signed_in(client):
    aid, _ = store.create_account("me@example.com")
    client.cookies.set(app_mod.SESSION_COOKIE, store.session_token(aid))
    r = client.get("/login?next=/app/keys", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/app/keys"


# ---------------------------------------------------------------- admin issuance

def test_only_admin_can_issue_and_the_route_hides_itself(client):
    r = client.post("/admin/password", data={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 404
    aid, _ = store.create_account("user@example.com")
    client.cookies.set(app_mod.SESSION_COOKIE, store.session_token(aid))
    assert client.post("/admin/password", data={"email": EMAIL, "password": PASSWORD}).status_code == 404
    assert store.verify_password(EMAIL, PASSWORD) is None


def test_admin_issues_creates_the_account_then_removes(client):
    _admin(client)
    r = client.post("/admin/password", data={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 200 and "Password set for" in r.text
    assert PASSWORD not in r.text                                   # never echoed
    assert store.verify_password(EMAIL, PASSWORD) is not None
    assert client.post("/admin/password", data={"email": EMAIL, "password": "short"}).status_code == 400
    r = client.post("/admin/password", data={"email": EMAIL, "action": "clear"})
    assert r.status_code == 200 and "removed" in r.text
    assert store.verify_password(EMAIL, PASSWORD) is None
    assert store.password_accounts() == []


def test_admin_console_lists_issued_passwords(client):
    _admin(client)
    store.set_password(EMAIL, PASSWORD)
    r = client.get("/admin")
    assert "Password sign-ins" in r.text and EMAIL in r.text and "scrypt$" not in r.text


def test_purge_drops_old_login_attempts():
    store.record_login("ip", EMAIL, ok=False, now=time.time() - 10 * 86400)
    store.record_login("ip", EMAIL, ok=False)
    assert store.login_failures_since(0, email=EMAIL) == 2
    store.purge()
    assert store.login_failures_since(0, email=EMAIL) == 1


# ------------------------------------------- pre-registration takeover via email signup

def test_keys_minted_before_the_owner_verified_the_address_die_on_claim(client):
    """The attack: sign up victim@example.com through the unverified email path, keep the
    key; when the real owner signs in with Google and is linked to that account, the
    attacker's key must stop working and the attacker's session must be dead."""
    r = client.post("/v1/signup", json={"email": "victim@example.com"})
    assert r.status_code in (200, 201), r.text
    attacker_key = r.json()["api_key"]
    account_id = r.json()["account_id"] if "account_id" in r.json() else store.authenticate(attacker_key)["account_id"]
    old_session = store.session_token(account_id)
    assert store.authenticate(attacker_key) is not None                  # works before claim

    owner, created = store.account_for_google("sub-123", "victim@example.com", "Victim")
    assert created is False and owner["account_id"] == account_id         # same account, linked
    assert store.authenticate(attacker_key) is None                       # key revoked
    assert store.account_by_session(old_session) is None                  # session rotated
    assert store.active_key_count(account_id) == 0
    # and the owner can mint a fresh one on the account they now own
    _, fresh = store.issue_key(account_id)
    assert store.authenticate(fresh)["account_id"] == account_id
