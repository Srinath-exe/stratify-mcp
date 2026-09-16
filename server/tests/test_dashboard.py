"""Dashboard and account-lifecycle tests.

The dashboard is where a human creates the credential an agent will then use, so the
things worth testing are the boundaries: a key is shown once, a session is not a key, and
one account cannot touch another's keys.
"""
import re
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server import app as app_mod, quota, store  # noqa: E402
from engine import honesty  # noqa: E402

KEY_RE = re.compile(r"sk_live_[A-Za-z0-9_-]+")


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(store, "DB_PATH", Path(d) / "service.sqlite")
        monkeypatch.setattr(honesty, "VARIANT_LOG", Path(d) / "variants.sqlite")
        yield


@pytest.fixture
def client():
    # base_url is https so the SESSION COOKIE, which is now marked Secure, is
    # actually returned by the client. Over plain http the browser -- and this
    # client -- correctly drops it, which is the flag doing its job.
    return TestClient(app_mod.app, base_url="https://testserver")


def signup(client, email="a@example.com"):
    r = client.post("/signup", data={"email": email}, follow_redirects=False)
    return r, KEY_RE.search(r.text).group(0) if KEY_RE.search(r.text) else None


def test_landing_page_renders_for_a_stranger(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Stratify" in r.text
    assert "Not investment advice" in r.text


def test_signup_shows_the_key_once_and_sets_a_session(client):
    r, key = signup(client)
    assert r.status_code == 200 and key
    assert app_mod.SESSION_COOKIE in r.cookies
    # revisiting the dashboard must NOT show the key again
    again = client.get("/dashboard")
    assert key not in again.text


def test_session_cookie_is_not_the_api_key(client):
    r, key = signup(client)
    assert r.cookies[app_mod.SESSION_COOKIE] != key
    assert not r.cookies[app_mod.SESSION_COOKIE].startswith("sk_live_")


def test_session_cookie_is_httponly_and_samesite_strict(client):
    r, _ = signup(client)
    raw = r.headers["set-cookie"].lower()
    assert "httponly" in raw and "samesite=strict" in raw


def test_dashboard_without_a_session_is_refused(client):
    assert client.get("/dashboard").status_code == 401


def test_dashboard_shows_usage_after_a_backtest(client):
    """The meter labelled "Backtests" must count backtests. It used to count every tool
    call, so reading the coverage moved the number that tells a user how many runs they
    have left."""
    _, key = signup(client)
    client.post("/mcp", headers={"Authorization": f"Bearer {key}"},
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": "run_backtest", "arguments": {"spec": {
                          "structure": "iron_fly", "entry_time": "09:30",
                          "params": {"pct_width": 1.5, "entry_dte": 4}}}}})
    page = client.get("/dashboard").text
    assert "Backtests" in page and "CPU seconds" in page
    assert re.search(r'Backtests</div>\s*<div class="v">1<', page)


def test_dashboard_does_not_count_a_metadata_call_as_a_backtest(client):
    _, key = signup(client)
    client.post("/mcp", headers={"Authorization": f"Bearer {key}"},
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": "describe_coverage", "arguments": {}}})
    page = client.get("/dashboard").text
    assert re.search(r'Backtests</div>\s*<div class="v">0<', page)
    assert re.search(r'Other calls</div>\s*<div class="v">1<', page)


def test_a_second_key_shares_one_account_quota(client):
    _, first = signup(client)
    second = KEY_RE.search(client.post("/dashboard/keys").text).group(0)
    assert second != first
    client.post("/mcp", headers={"Authorization": f"Bearer {first}"},
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": "describe_coverage", "arguments": {}}})
    r = client.post("/mcp", headers={"Authorization": f"Bearer {second}"},
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": "describe_coverage", "arguments": {}}})
    q = r.json()["result"]["structuredContent"]["quota"]
    # describe_coverage is metadata, so the SHARING is asserted on the counter it spends.
    # The property under test is that two keys draw on one account, not which meter moves.
    assert q["metadata_requests_used"] == 2 and q["metered_on"] == "account"


def test_key_cap_is_enforced_from_the_dashboard(client):
    signup(client)
    codes = [client.post("/dashboard/keys").status_code
             for _ in range(quota.MAX_ACTIVE_KEYS_PER_ACCOUNT + 1)]
    assert codes[-1] == 429


def test_revoking_from_the_dashboard_kills_the_key(client):
    _, key = signup(client)
    key_id = store.authenticate(key)["key_id"]
    assert client.post("/dashboard/revoke", data={"key_id": key_id}).status_code == 200
    r = client.post("/mcp", headers={"Authorization": f"Bearer {key}"},
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": "describe_coverage", "arguments": {}}})
    # A revoked key is now refused at the transport layer with a 401 challenge rather
    # than a JSON-RPC error, so a client can offer to reconnect instead of printing the
    # refusal into the conversation as a tool result.
    assert r.status_code == 401


def test_one_account_cannot_revoke_anothers_key(client):
    _, victim_key = signup(client, "victim@example.com")
    victim_key_id = store.authenticate(victim_key)["key_id"]
    # a second browser session, and the attacker knows the key id
    attacker = TestClient(app_mod.app, base_url="https://testserver")
    attacker.post("/signup", data={"email": "attacker@example.com"})
    r = attacker.post("/dashboard/revoke", data={"key_id": victim_key_id})
    assert r.status_code == 403
    assert store.authenticate(victim_key) is not None      # still works


def test_browser_signup_obeys_the_same_throttle_as_the_api(client):
    codes = [client.post("/signup", data={"email": f"x{i}@example.com"}).status_code
             for i in range(quota.SIGNUPS_PER_IP_PER_HOUR + 1)]
    assert codes[-1] == 429


def test_bad_email_is_refused_without_creating_an_account(client):
    r = client.post("/signup", data={"email": "nope"})
    assert r.status_code == 400
    con = store.connect()
    assert con.execute("SELECT count(*) FROM accounts").fetchone()[0] == 0
    con.close()
