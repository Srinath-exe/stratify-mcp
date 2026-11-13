"""Regression tests for the 13 Nov 2026 security review.

Each test pins one finding so it cannot quietly come back. None needs ClickHouse.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from server import app as app_mod, mcpauth, quota, site, store  # noqa: E402
from server.scriptjson import script_json  # noqa: E402
from engine import db as engine_db, honesty, strategy  # noqa: E402

WEB = "https://testserver"


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(store, "DB_PATH", Path(d) / "service.sqlite")
        monkeypatch.setattr(honesty, "VARIANT_LOG", Path(d) / "variants.sqlite")
        monkeypatch.setattr(quota, "CONCURRENCY", quota.Concurrency())
        yield


@pytest.fixture
def client():
    return TestClient(app_mod.app, base_url=WEB)


def _oauth_token(account_id, client_id="https://chatgpt.example/oauth/client.json"):
    tok = store.OAUTH_ACCESS_PREFIX + "t" + str(time.time_ns())
    store.save_oauth_token(tok, "access", client_id, account_id, "mcp", "fam_" + account_id)
    return tok


# ---- C1: one OAuth client is many accounts --------------------------------------------

def test_a_shared_oauth_client_never_shares_backtests():
    a, _ = store.create_account("a@example.com")
    b, _ = store.create_account("b@example.com")
    shared = "oauth:https://chatgpt.example/oauth/client.json"
    bt = store.save_result(shared, '{"structure":"iron_condor"}', "h", "{}", account_id=a)[0]
    # B holds a token for the SAME client, so its key_id is identical to A's.
    assert store.result_for_account(bt, b, shared) is None
    assert [r["backtest_id"] for r in store.recent_results_for_owner(b, shared)] == []
    assert store.recent_results(shared) == []
    # A still sees its own.
    assert store.result_for_account(bt, a, shared)["backtest_id"] == bt
    assert [r["backtest_id"] for r in store.recent_results_for_owner(a, shared)] == [bt]


def test_an_api_key_still_finds_its_legacy_ownerless_rows():
    a, _ = store.create_account("a@example.com")
    key_id, _ = store.issue_key(a)
    bt = store.save_result(key_id, "{}", "h", "{}", account_id="")[0]
    assert store.result_for_account(bt, a, key_id)["backtest_id"] == bt


def test_an_empty_account_never_matches_the_ownerless_rows():
    store.save_result("key_x", "{}", "h", "{}", account_id="")
    assert store.recent_results_for_owner("", "oauth:https://c.example/x.json") == []


# ---- H1: stored XSS through a rule label or a name -------------------------------------

def test_script_json_cannot_close_the_script_element():
    out = script_json({"why": "</script><script>alert(1)</script>", "s": "a&b "})
    assert "</script" not in out.lower() and "<" not in out and "&" not in out
    assert json.loads(out) == {"why": "</script><script>alert(1)</script>", "s": "a&b "}


@pytest.mark.parametrize("bad", ["</script><script>alert(1)</script>", 'a"b', "x`y",
                                 "a\\b", "line\nbreak", {"x": 1}, "a" * 61])
def test_free_text_labels_refuse_markup_and_junk(bad):
    with pytest.raises(strategy.StrategyError):
        strategy._text(bad, "rules[0].label", 60, "rule 0")


@pytest.mark.parametrize("ok", ["Iron fly — wide", "Srinath's strangle", "₹5k stop (CE) 1.5%"])
def test_ordinary_labels_still_pass(ok):
    assert strategy._text(ok, "rules[0].label", 60, "rule 0") == ok


@pytest.mark.parametrize("q", [-5, 2.5, "abc", 10 ** 15, True])
def test_roll_qty_is_bounded(q):
    with pytest.raises(strategy.StrategyError):
        strategy._roll_qty(q, "rules[0].then.roll.qty")


# ---- M1: cross-origin writes from sibling subdomains ----------------------------------

@pytest.mark.parametrize("path", ["/admin/password", "/oauth/authorize", "/dashboard/keys",
                                  "/app/keys/new", "/v1/feedback", "/login"])
def test_sibling_origin_writes_are_refused(client, path):
    r = client.post(path, headers={"Origin": "https://ops.aeon-labs.site"}, data={"x": "1"})
    assert r.status_code == 403


def test_same_origin_writes_pass_the_origin_check(client):
    r = client.post("/login", headers={"Origin": WEB}, data={"email": "x@example.com",
                                                             "password": "nope"})
    assert r.status_code != 403


def test_the_mcp_transport_keeps_its_own_origin_rules(client):
    r = client.post("/mcp", headers={"Origin": "https://claude.ai"},
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 200


# ---- M2: an OAuth grant cannot mint a permanent key -----------------------------------

def test_an_oauth_token_cannot_create_api_keys(client):
    a, _ = store.create_account("a@example.com")
    r = client.post("/v1/keys", json={}, headers={"Authorization": f"Bearer {_oauth_token(a)}"})
    assert r.status_code == 403
    assert all(k["revoked_at"] is not None for k in store.list_keys(a)) or not store.list_keys(a)


def test_an_api_key_can_still_create_api_keys(client):
    a, _ = store.create_account("a@example.com")
    _, secret = store.issue_key(a)
    r = client.post("/v1/keys", json={}, headers={"Authorization": f"Bearer {secret}"})
    assert r.status_code in (200, 201)


# ---- M3: consent never shows a self-registered name as verified ------------------------

def test_userinfo_redirects_are_refused():
    assert not mcpauth._valid_redirect("https://chatgpt.com@evil.example/cb")
    assert mcpauth._valid_redirect("https://chatgpt.com/cb")


def test_the_consent_identity_is_a_host_not_a_claimed_name():
    assert site._host_of("https://chatgpt.com@evil.example/cb") == "evil.example"


# ---- H3: batches ------------------------------------------------------------------------

def test_large_batches_are_refused(client):
    batch = [{"jsonrpc": "2.0", "id": i, "method": "tools/list"} for i in range(50)]
    r = client.post("/mcp", json=batch)
    assert r.status_code == 400


def test_a_small_batch_is_still_answered(client):
    batch = [{"jsonrpc": "2.0", "id": i, "method": "tools/list"} for i in range(2)]
    r = client.post("/mcp", json=batch)
    assert r.status_code == 200 and len(r.json()) == 2


# ---- H3 (data): per-request query budget ----------------------------------------------

def test_the_query_budget_stops_a_runaway_request(monkeypatch):
    monkeypatch.setitem(engine_db.BUDGETS, "free", (3, 60.0))
    tok = engine_db.begin_budget("free")
    try:
        for _ in range(3):
            engine_db._spend()
        with pytest.raises(engine_db.TooMuchWork):
            engine_db._spend()
    finally:
        engine_db.end_budget(tok)
    engine_db._spend()          # outside a request: no budget, no error


# ---- returning Google users keep their keys and connections ---------------------------

def test_a_returning_google_sign_in_revokes_nothing():
    acct, created = store.account_for_google("sub-1", "a@example.com")
    assert created
    key_id, _ = store.issue_key(acct["account_id"])
    tok = _oauth_token(acct["account_id"])
    store.account_for_google("sub-1", "a@example.com")          # signs in again
    assert all(k["revoked_at"] is None for k in store.list_keys(acct["account_id"]))
    assert store.authenticate_oauth(tok) is not None


def test_claiming_an_email_signup_still_revokes_what_was_minted_before():
    a, _ = store.create_account("a@example.com")
    store.issue_key(a)
    store.account_for_google("sub-2", "a@example.com")          # the verified owner claims it
    assert all(k["revoked_at"] is not None for k in store.list_keys(a))


def test_a_password_cannot_be_set_on_a_google_account():
    store.account_for_google("sub-3", "g@example.com")
    with pytest.raises(ValueError):
        store.set_password("g@example.com", "a-long-enough-password-1")


# ---- L1: IPv6 throttles count the /64 ---------------------------------------------------

def test_ipv6_addresses_in_one_64_share_a_throttle_identity():
    assert store.hash_ip("2001:db8:1:2::1") == store.hash_ip("2001:db8:1:2:ffff::9")
    assert store.hash_ip("2001:db8:1:2::1") != store.hash_ip("2001:db8:1:3::1")
    assert store.hash_ip("203.0.113.5") != store.hash_ip("203.0.113.6")
