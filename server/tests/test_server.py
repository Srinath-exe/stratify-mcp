"""Server tests: protocol, auth, quotas, refusals, and the two things that must never
happen — market data leaving, and one key seeing another key's results."""
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

import pytest

import conftest as _conftest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server import app as app_mod, knowledge, quota, store, tools  # noqa: E402
from engine import honesty  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(store, "DB_PATH", Path(d) / "service.sqlite")
        monkeypatch.setattr(honesty, "VARIANT_LOG", Path(d) / "variants.sqlite")
        monkeypatch.setattr(quota, "CONCURRENCY", quota.Concurrency())
        yield


@pytest.fixture
def client():
    # base_url is https so the SESSION COOKIE, which is now marked Secure, is
    # actually returned by the client. Over plain http the browser -- and this
    # client -- correctly drops it, which is the flag doing its job.
    return TestClient(app_mod.app, base_url="https://testserver")


@pytest.fixture
def signup(client):
    return client.post("/v1/signup", json={"email": "t@example.com"}).json()


@pytest.fixture
def key(signup):
    return signup["api_key"]


@pytest.fixture
def account(signup):
    return signup["account_id"]


def rpc(client, method, params=None, key=None, request_id=1):
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    body = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers=headers).json()


def call(client, name, arguments, key):
    return _conftest.skip_if_the_database_is_why_this_failed(
        rpc(client, "tools/call", {"name": name, "arguments": arguments}, key=key))


# ---------------------------------------------------------------- protocol

def test_initialize_needs_no_key(client):
    r = rpc(client, "initialize", {})
    assert r["result"]["protocolVersion"] == app_mod.PROTOCOL_VERSION
    assert r["result"]["serverInfo"]["name"] == "stratify"
    assert "instructions" in r["result"]


def test_tools_list_matches_the_registry(client, key):
    names = [t["name"] for t in rpc(client, "tools/list", key=key)["result"]["tools"]]
    assert names == [t["name"] for t in tools.TOOLS]


def test_every_tool_has_a_description_and_schema(client, key):
    for t in rpc(client, "tools/list", key=key)["result"]["tools"]:
        assert len(t["description"]) > 40
        assert t["inputSchema"]["type"] == "object"


def test_notifications_get_no_error(client):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert "error" not in r.json()


def test_unknown_method_is_a_protocol_error(client, key):
    assert rpc(client, "tools/nope", key=key)["error"]["code"] == app_mod.METHOD_NOT_FOUND


def test_bad_json_is_rejected_cleanly(client):
    r = client.post("/mcp", content=b"{not json",
                    headers={"content-type": "application/json"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == app_mod.PARSE_ERROR


def test_oversized_body_is_refused_before_parsing(client):
    r = client.post("/mcp", content=b'{"a":"' + b"x" * (app_mod.MAX_BODY_BYTES + 1) + b'"}',
                    headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_browser_origin_is_refused_by_default(client):
    """A local MCP server is reachable from any page the user visits unless it checks
    Origin. The spec requires this; closed by default, opened by configuration."""
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                    headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_configured_origin_is_allowed(client, monkeypatch):
    monkeypatch.setattr(app_mod, "ALLOWED_ORIGINS", {"https://app.stratify.io"})
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                    headers={"Origin": "https://app.stratify.io"})
    assert r.status_code == 200


def test_non_browser_client_without_origin_is_fine(client):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 200


def test_batch_requests_work(client, key):
    r = client.post("/mcp", json=[
        {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ], headers={"Authorization": f"Bearer {key}"})
    assert [m["id"] for m in r.json()] == [1, 2]


# ---------------------------------------------------------------- auth

def test_tool_call_without_a_key_is_refused_with_instructions(client):
    err = call(client, "describe_coverage", {}, key=None)["error"]
    assert err["code"] == app_mod.UNAUTHENTICATED
    assert "signup" in err["data"]["how_to_fix"]


def test_a_wrong_key_is_refused(client):
    assert call(client, "describe_coverage", {}, key="sk_live_nope")["error"]["code"] \
        == app_mod.UNAUTHENTICATED


def test_revoked_key_stops_working(client, key):
    row = store.authenticate(key)
    store.revoke_key(row["key_id"])
    assert call(client, "describe_coverage", {}, key=key)["error"]["code"] \
        == app_mod.UNAUTHENTICATED


def test_plaintext_key_is_never_stored(client, key):
    con = store.connect()
    blob = json.dumps([dict(r) for r in con.execute("SELECT * FROM api_keys")], default=str)
    con.close()
    assert key not in blob
    assert key[8:] not in blob


def test_signup_rejects_a_non_email(client):
    assert client.post("/v1/signup", json={"email": "nope"}).status_code == 400


# ---------------------------------------------------------------- isolation

def test_one_key_cannot_read_another_keys_result(client):
    k1 = client.post("/v1/signup", json={"email": "a@x.com"}).json()["api_key"]
    k2 = client.post("/v1/signup", json={"email": "b@x.com"}).json()["api_key"]
    made = call(client, "run_backtest", {"spec": {
        "structure": "iron_fly", "entry_time": "09:30",
        "params": {"pct_width": 1.5, "entry_dte": 4}}}, key=k1)
    bid = made["result"]["structuredContent"]["backtest_id"]
    other = call(client, "get_backtest", {"backtest_id": bid}, key=k2)
    assert other["result"]["isError"] is True


# ---------------------------------------------------------------- quotas

def test_request_quota_returns_a_retry_time(client, key, account):
    key_id = store.authenticate(key)["key_id"]
    now = time.time()
    for _ in range(quota.TIERS["free"].requests_per_hour):
        store.record_usage(key_id, account, 0.01, now - 60)
    err = call(client, "describe_coverage", {}, key=key)["error"]
    assert err["code"] == app_mod.QUOTA_EXCEEDED
    assert err["data"]["limit"] == "requests_per_hour"
    assert err["data"]["retry_after_seconds"] > 0


def test_cpu_quota_is_independent_of_request_count(client, key, account):
    key_id = store.authenticate(key)["key_id"]
    store.record_usage(key_id, account, quota.TIERS["free"].cpu_seconds_per_hour + 1,
                       time.time() - 60)
    err = call(client, "describe_coverage", {}, key=key)["error"]
    assert err["data"]["limit"] == "cpu_seconds_per_hour"


def test_concurrency_limit_is_enforced(client, key, account):
    for _ in range(quota.TIERS["free"].max_concurrent):
        quota.CONCURRENCY.acquire(account, quota.TIERS["free"].max_concurrent)
    err = call(client, "describe_coverage", {}, key=key)["error"]
    assert err["data"]["limit"] == "concurrency"


def test_concurrency_is_released_even_when_a_tool_refuses(client, key, account):
    call(client, "run_backtest", {"spec": {"structure": "debit_spread", "params": {}}}, key=key)
    assert quota.CONCURRENCY.active(account) == 0


# ---------------------------------------------------------------- abuse: free keys

def test_signup_is_throttled_per_source_address(client):
    """Without this the per-account quota is decorative: a new account is a new
    allowance, so anyone could mint keys in a loop and never hit a limit."""
    codes = [client.post("/v1/signup", json={"email": f"a{i}@x.com"}).status_code
             for i in range(quota.SIGNUPS_PER_IP_PER_HOUR + 2)]
    assert codes[:quota.SIGNUPS_PER_IP_PER_HOUR] == [200] * quota.SIGNUPS_PER_IP_PER_HOUR
    assert codes[quota.SIGNUPS_PER_IP_PER_HOUR:] == [429, 429]


def test_throttled_signup_says_when_and_what_to_do_instead(client):
    for i in range(quota.SIGNUPS_PER_IP_PER_HOUR):
        client.post("/v1/signup", json={"email": f"b{i}@x.com"})
    r = client.post("/v1/signup", json={"email": "late@x.com"})
    assert r.status_code == 429
    assert r.headers["Retry-After"] == str(r.json()["retry_after_seconds"])
    assert "/v1/keys" in r.json()["error"]


def test_keys_per_account_is_capped(client, key, account):
    """Now authenticated: the account comes from the CREDENTIAL, not the request body."""
    hdr = {"Authorization": f"Bearer {key}"}
    codes = [client.post("/v1/keys", json={}, headers=hdr).status_code
             for _ in range(quota.MAX_ACTIVE_KEYS_PER_ACCOUNT + 1)]
    assert codes.count(200) == quota.MAX_ACTIVE_KEYS_PER_ACCOUNT - 1   # signup issued one
    assert codes[-1] == 429


def test_extra_keys_share_one_quota(client, key, account):
    """Issuing another key must not hand out another allowance."""
    _, second = store.issue_key(account)
    for _ in range(3):
        call(client, "describe_coverage", {}, key=key)
    q = call(client, "describe_coverage", {}, key=second)["result"]["structuredContent"]["quota"]
    assert q["requests_used"] == 4
    assert q["metered_on"] == "account"


def test_forwarded_ip_is_ignored_unless_a_proxy_is_declared(client, monkeypatch):
    """X-Forwarded-For is caller-controlled. Trusting it unconditionally would give every
    abuser a fresh identity per request."""
    monkeypatch.delenv("STRATIFY_BEHIND_PROXY", raising=False)
    codes = [client.post("/v1/signup", json={"email": f"c{i}@x.com"},
                         headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code
             for i in range(quota.SIGNUPS_PER_IP_PER_HOUR + 1)]
    assert codes[-1] == 429


def test_every_tool_costs_quota_not_only_run_backtest(client, key, account):
    """describe_coverage still queries ClickHouse; metering only run_backtest left it free."""
    before = store.usage_since(account, 0)[0]
    call(client, "describe_coverage", {}, key=key)
    call(client, "search", {"query": "costs"}, key=key)
    assert store.usage_since(account, 0)[0] == before + 2


# ---------------------------------------------------------------- retention

def test_purge_blanks_old_bodies_but_keeps_the_id_resolvable(client, key):
    made = call(client, "run_backtest", {"spec": {
        "structure": "iron_fly", "entry_time": "09:30",
        "params": {"pct_width": 1.5, "entry_dte": 4}}}, key=key)
    bid = made["result"]["structuredContent"]["backtest_id"]
    stats = store.purge(now=time.time() + 40 * 86400)
    assert stats["result_bodies_purged"] == 1
    row = store.get_result(bid)
    assert row is not None
    assert json.loads(row["spec_json"])["purged"] is True
    assert row["spec_hash"]              # the hash survives, for metering and dedup


def test_purge_is_idempotent(client, key):
    call(client, "run_backtest", {"spec": {
        "structure": "iron_fly", "entry_time": "09:30",
        "params": {"pct_width": 1.5, "entry_dte": 4}}}, key=key)
    future = time.time() + 40 * 86400
    assert store.purge(now=future)["result_bodies_purged"] == 1
    assert store.purge(now=future)["result_bodies_purged"] == 0


def test_purge_ages_out_usage_and_signup_rows(client, key, account):
    store.record_usage("k", account, 1.0, time.time() - 40 * 86400)
    stats = store.purge()
    assert stats["usage_rows_deleted"] >= 1
    assert stats["signup_rows_deleted"] >= 0


def test_usage_is_reported_after_the_call_not_before(client, key):
    r = call(client, "run_backtest", {"spec": {
        "structure": "iron_fly", "entry_time": "09:30",
        "params": {"pct_width": 1.5, "entry_dte": 4}}}, key=key)
    q = r["result"]["structuredContent"]["quota"]
    assert q["requests_used"] == 1 and q["cpu_seconds_used"] > 0


# ---------------------------------------------------------------- refusals and data

def test_a_bad_spec_is_a_tool_result_not_a_protocol_error(client, key):
    """The model should read the reason and fix the call, not see a broken connection."""
    r = call(client, "run_backtest", {"spec": {"structure": "debit_spread", "params": {}}}, key=key)
    assert "error" not in r
    assert r["result"]["isError"] is True
    assert "margin-sizing bug" in r["result"]["content"][0]["text"]


def test_no_route_returns_market_data(client, key):
    paths = {r.path for r in app_mod.app.routes if hasattr(r, "path")}
    assert not any(p.startswith("/v1/export") or "bars" in p or "chain" in p for p in paths)


def test_result_releases_traded_prices_and_counts_them(client, key):
    """SUPERSEDES test_result_carries_no_per_contract_pnl.

    That test asserted no leg and no price ever left the service. The policy changed on
    purpose: a caller who cannot see an entry price, an exit price and a timestamp cannot
    check the arithmetic, so the honesty panel had to be taken on trust. What is released
    is the contracts the strategy TRADED, at the minutes it traded them -- never the chain.
    This pins the new boundary and the meter that measures it.
    """
    r = call(client, "run_backtest", {"spec": {
        "structure": "iron_condor", "entry_time": "09:30",
        "params": {"pct_offset": 1.5, "pct_width": 1.0, "entry_dte": 4}}}, key=key)
    payload = r["result"]["structuredContent"]
    assert payload["trade_detail"]["prices_included"] is True
    assert payload["trades"][0]["legs"][0]["entry_price"] > 0
    # Released, counted, and disclosed in the same response.
    n = payload["data_release"]["price_points_released"]
    assert n > 0
    assert payload["quota"]["price_points_used"] >= n
    assert payload["quota"]["price_points_limit"] == quota.TIERS["free"].price_points_per_hour


def test_summary_detail_releases_no_prices_at_all(client, key):
    r = call(client, "run_backtest", {"spec": {
        "structure": "iron_condor", "entry_time": "09:30",
        "params": {"pct_offset": 1.5, "pct_width": 1.0, "entry_dte": 4}},
        "detail": "summary"}, key=key)
    payload = r["result"]["structuredContent"]
    assert "trades" not in payload and "equity_curve" not in payload
    blob = json.dumps(payload)
    assert "entry_price" not in blob


def test_narrow_run_is_refused_prices(client, key, monkeypatch):
    """A run below the trade floor gets aggregates but no per-leg prices. Without this the
    rich payload would be a price lookup: pick one strike, one narrow window, read the
    quote back out. It is the same floor that withholds Sharpe, for the same reason."""
    from engine import detail as detail_mod
    monkeypatch.setattr(detail_mod, "PRICE_DETAIL_MIN_TRADES", 10_000)
    r = call(client, "run_backtest", {"spec": {
        "structure": "iron_condor", "entry_time": "09:30",
        "params": {"pct_offset": 1.5, "pct_width": 1.0, "entry_dte": 4}}}, key=key)
    payload = r["result"]["structuredContent"]
    assert payload["trade_detail"]["prices_included"] is False
    assert "entry_price" not in json.dumps(payload)
    assert payload["data_release"]["price_points_released"] == 0


def test_no_endpoint_exposes_an_untraded_strike(client, key):
    """The release is bounded by what the STRATEGY touched. Every strike that appears in a
    result has to be one of that trade's own legs -- if any other strike could appear, the
    result would be a window onto the chain rather than a record of a backtest."""
    r = call(client, "run_backtest", {"spec": {
        "structure": "iron_condor", "entry_time": "09:30",
        "params": {"pct_offset": 1.5, "pct_width": 1.0, "entry_dte": 4}}}, key=key)
    payload = r["result"]["structuredContent"]
    for t in payload["trades"]:
        assert 1 <= len(t["legs"]) <= 4
        assert len({(l["type"], l["strike"]) for l in t["legs"]}) == len(t["legs"])


def test_coverage_is_public_and_returns_only_metadata(client):
    body = client.get("/v1/coverage").json()
    assert body["trading_days"] == 246
    assert "withheld" in body and "debit_spread" in body["withheld"]
    assert "close" not in json.dumps(body)


def test_methodology_is_reachable_and_names_its_own_gaps(client, key):
    r = call(client, "explain_methodology", {"topic": "slippage"}, key=key)
    text = r["result"]["structuredContent"]["explanation"]
    assert "Modelled, not measured" in text


# ---------------------------------------------------------------- report

def test_report_renders_and_leads_with_the_honesty_panel(client, key):
    r = call(client, "run_backtest", {"spec": {
        "structure": "iron_condor", "entry_time": "09:30",
        "params": {"pct_offset": 1.5, "pct_width": 1.0, "entry_dte": 4}}}, key=key)
    url = r["result"]["structuredContent"]["report_url"].replace("http://testserver", "")
    page = client.get(url)
    assert page.status_code == 200
    body = page.text
    # The ordering IS the product: honesty panel before the P&L, because a report
    # that leads with a number invites the reader to stop at the number.
    assert body.index("What the evidence supports") < body.index("The numbers")
    assert "Not investment advice" in body
    # Fully self-contained: nothing here may cause the browser to fetch anything. The
    # SVG namespace is excluded because it is an identifier, never a request -- it now
    # appears as a JS constant as well as an xmlns attribute, since the charts build
    # their elements with createElementNS.
    assert "http://" not in body.replace("http://www.w3.org/2000/svg", "")
    for fetching in ("<link", "<script src", "<img", "<iframe", "@import", "url("):
        assert fetching not in body, f"report would fetch something: {fetching}"


def test_unknown_report_token_is_a_404(client):
    assert client.get("/r/nope").status_code == 404


# --------------------------------------------------------------- schema drift

def test_usage_metering_survives_the_migration_column_order(monkeypatch, tmp_path):
    """A database migrated by ALTER TABLE has account_id LAST, not second.

    SCHEMA declares usage(key_id, account_id, ts, cpu_seconds), but MIGRATIONS adds
    account_id to databases created before it existed -- and ALTER TABLE appends. A
    positional `INSERT INTO usage VALUES (?,?,?,?)` therefore wrote cpu_seconds into
    account_id on every migrated database, so usage_since() matched nothing and the
    per-account request and CPU quotas silently never fired. Every test passed because
    tests build a fresh database, where the positional order happens to line up.

    This builds the table in its pre-migration shape, which is the shape production
    actually had, and asserts the round-trip.
    """
    db = tmp_path / "migrated.sqlite"
    con = sqlite3.connect(db)
    with con:
        con.execute("CREATE TABLE usage (key_id TEXT NOT NULL, ts REAL NOT NULL, "
                    "cpu_seconds REAL NOT NULL DEFAULT 0)")
    con.close()
    monkeypatch.setattr(store, "DB_PATH", db)

    store.record_usage("key_x", "acc_x", 1.5, now=1000.0)
    store.record_usage("key_x", "acc_x", 2.5, now=1001.0)
    n, cpu, pts = store.usage_since("acc_x", 0)
    assert n == 2, "requests were not attributed to the account"
    assert cpu == pytest.approx(4.0), "CPU seconds were not attributed to the account"
    assert pts == 0, "price points defaulted wrong on a pre-migration database"


def test_every_insert_names_its_columns():
    """Positional INSERT plus ALTER TABLE migration is the bug above. Naming columns
    makes physical order irrelevant, so require it rather than re-testing each table."""
    src = Path(store.__file__).read_text()
    bare = re.findall(r"INSERT INTO (\w+) VALUES", src)
    assert not bare, f"positional INSERT into {bare}; name the columns"


# --------------------------------------------------------------- knowledge surfaces

def test_resources_list_and_read_every_topic(client):
    """Every advertised resource must actually resolve. A knowledge base that lists a
    document it cannot serve is worse than one that lists nothing."""
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "resources/list"})
    resources = r.json()["result"]["resources"]
    assert resources, "no resources advertised"
    for res in resources:
        got = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "resources/read",
                                        "params": {"uri": res["uri"]}})
        body = got.json()["result"]["contents"][0]
        assert body["text"].strip(), f"{res['uri']} resolved to empty text"


def test_unknown_resource_is_a_protocol_error_not_a_crash(client):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "resources/read",
                                  "params": {"uri": "stratify://knowledge/nope"}})
    assert r.json()["error"]["code"] == app_mod.INVALID_PARAMS


def test_prompts_render_with_their_arguments(client):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "prompts/list"})
    prompts = r.json()["result"]["prompts"]
    assert prompts
    for spec in prompts:
        args = {a["name"]: "XYZ123" for a in spec["arguments"]}
        got = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "prompts/get",
                                        "params": {"name": spec["name"], "arguments": args}})
        text = got.json()["result"]["messages"][0]["content"]["text"]
        assert "XYZ123" in text, f"{spec['name']} did not substitute its argument"
        assert "{" not in text, f"{spec['name']} left an unsubstituted placeholder"


def test_capabilities_advertise_what_is_actually_served(client):
    """A client that sees no `resources` capability will never call resources/list, so
    advertising and serving must not drift apart."""
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    caps = r.json()["result"]["capabilities"]
    for name, method in (("resources", "resources/list"), ("prompts", "prompts/list")):
        assert name in caps, f"{name} served but not advertised"
        listed = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": method})
        assert "result" in listed.json(), f"{method} advertised but not served"


def test_instructions_carry_the_facts_models_get_wrong():
    """These four are the documented, repeated failure modes. If the ambient text stops
    naming them, a weak model stops being protected from them."""
    text = knowledge.SERVER_INSTRUCTIONS.lower()
    for fact in ("tuesday", "lot size", "30 minutes", "50 trades"):
        assert fact in text, f"instructions no longer mention {fact!r}"


def test_interpretation_refuses_to_overclaim_on_a_small_sample():
    out = knowledge.interpretation({"n_trades": 12}, {})
    assert any("30-trade floor" in r for r in out["reading"])
    assert out["do_not_conclude"], "a 12-trade result must carry an explicit limit"


def test_interpretation_always_states_it_is_not_advice():
    for n in (12, 45, 900):
        out = knowledge.interpretation({"n_trades": n}, {})
        # The entry reads "That this IS advice" -- it sits under do_not_conclude,
        # so the negation is the heading, not the sentence.
        assert any("advice" in d.lower() for d in out["do_not_conclude"])


def test_interpretation_reads_oos_count_from_the_right_nesting_level():
    """panel["out_of_sample"] is the whole split BLOCK; the trade count is one level
    further in. Reading it off the outer dict yields 0 silently, which produced
    "the held-out final 30 % (0 trades) stayed profitable" on a 16-trade slice --
    a sentence that is both wrong and exactly the defect the honesty panel exists to
    prevent. Assert against the real shape."""
    panel = {"out_of_sample": {
        "method": "chronological 70/30",
        "in_sample": {"n_trades": 37},
        "out_of_sample": {"n_trades": 16},
        "held_up": True,
    }}
    out = knowledge.interpretation({"n_trades": 53}, panel)
    joined = " ".join(out["reading"])
    assert "16 trades" in joined, joined
    assert "(0 trades)" not in joined, "read the count off the wrong level again"


def test_interpretation_flags_disagreeing_walk_forward_folds():
    """A positive headline across folds that disagree is an average, not an edge."""
    panel = {"walk_forward": [
        {"fold": 1, "from": "2025-07-01", "to": "2025-10-16", "profitable": True},
        {"fold": 2, "from": "2025-10-24", "to": "2026-02-13", "profitable": True},
        {"fold": 3, "from": "2026-02-20", "to": "2026-06-25", "profitable": False},
    ]}
    out = knowledge.interpretation({"n_trades": 53}, panel)
    assert any("LOST money" in r for r in out["reading"])
    assert any("consistent" in d for d in out["do_not_conclude"])


# ------------------------------------------------------------------ browser clients

def test_preflight_succeeds_for_an_allowed_browser_origin(client):
    """A browser will not send Authorization cross-origin until OPTIONS succeeds. Without
    this the connector fails with an opaque network error, not an auth error."""
    r = client.options("/mcp", headers={
        "Origin": "https://claude.ai",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,authorization"})
    assert r.status_code == 204
    assert r.headers["access-control-allow-origin"] == "https://claude.ai"
    assert "authorization" in r.headers["access-control-allow-headers"].lower()


def test_preflight_refuses_an_unknown_origin(client):
    r = client.options("/mcp", headers={"Origin": "https://evil.example",
                                        "Access-Control-Request-Method": "POST"})
    assert r.status_code == 403


def test_cors_never_uses_a_wildcard_origin(client):
    """'*' would let any page read the response, and cannot be combined with credentials."""
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                    headers={"Origin": "https://claude.ai"})
    assert r.headers.get("access-control-allow-origin") == "https://claude.ai"


def test_serverside_client_without_origin_still_works(client):
    """Anthropic's connector fetches server-side and sends no Origin at all."""
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert r.json()["result"]["protocolVersion"]


def test_api_key_is_accepted_under_every_supported_header(client):
    """claude.ai's connector UI reserves Authorization for OAuth and will not let a user
    set it, so a bearer-only server is unreachable from that surface. Each accepted
    spelling must authenticate identically."""
    _, secret = store.issue_key(store.create_account("hdr@example.com")[0])
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "explain_methodology", "arguments": {"topic": "costs"}}}
    for header, value in (
        ("Authorization", f"Bearer {secret}"),
        ("Authorization", secret),          # no prefix: some UIs offer only a value box
        ("X-API-Key", secret),
        ("X-Stratify-Key", secret),
        ("Api-Key", f"Bearer {secret}"),
    ):
        r = client.post("/mcp", json=call, headers={header: value})
        assert "result" in r.json(), f"{header}={value[:12]}... was rejected"


def test_a_bad_key_is_still_refused_under_every_header(client):
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "explain_methodology", "arguments": {}}}
    for header in ("Authorization", "X-API-Key", "X-Stratify-Key", "Api-Key"):
        r = client.post("/mcp", json=call, headers={header: "sk_live_not_a_real_key"})
        assert r.json()["error"]["code"] == app_mod.UNAUTHENTICATED, header


def test_url_embedded_key_route_is_disabled_by_default(client):
    """A credential in a URL is a credential in the client's config and every proxy log
    between here and there. Header auth reaches claude.ai, so this stays off unless a
    deployment explicitly turns it on."""
    r = client.post("/mcp/k/sk_live_whatever",
                    json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert r.status_code == 404


# --------------------------------------------------------------- intraday surface
#
# A capability the model cannot SEE is a capability that does not exist. The engine gained
# a daily cadence and a clock exit; these assert that the tool description, the coverage
# document and the ambient instructions all say so — because the failure this fixes was an
# assistant telling a user that "buy at 11, sell at 2, every day" could not be expressed.

def test_the_schema_advertises_cadence_and_exit_time():
    spec = [t for t in tools.TOOLS if t["name"] == "run_backtest"][0]
    # `spec` is a oneOf of two forms now: the preset shape and the open leg-list shape.
    # This test is about the preset one.
    preset = spec["inputSchema"]["properties"]["spec"]["oneOf"][0]
    props = preset["properties"]
    assert set(props["cadence"]["enum"]) == {"weekly", "daily"}
    assert "14:00" in props["exit_time"]["enum"]
    assert "max_dte" in props
    assert "detail" in spec["inputSchema"]["properties"]


def test_ambient_instructions_say_intraday_is_expressible():
    text = knowledge.SERVER_INSTRUCTIONS
    assert "cadence 'daily'" in text and "exit_time" in text
    assert "should not tell a user it is not" in text


def test_coverage_advertises_both_cadences_and_the_release_policy(client):
    body = client.get("/v1/coverage").json()
    assert set(body["cadences"]) == {"weekly", "daily"}
    assert "14:00" in body["exit_times"]
    assert "never" in body["returns"]
    assert "close" not in json.dumps(body)


def test_an_intraday_run_returns_a_curve_and_same_session_exits(client, key):
    r = call(client, "run_backtest", {"spec": {
        "structure": "long_option", "cadence": "daily",
        "entry_time": "11:00", "exit_time": "14:00",
        "params": {"pct_offset": 0.5, "direction": "CE"}}}, key=key)
    payload = r["result"]["structuredContent"]
    assert payload["summary"]["n_trades"] > 200, "daily cadence stayed weekly"
    assert set(payload["summary"]["exit_reasons"]) <= {"TIME", "SL", "TP"}
    curve = payload["equity_curve"]
    assert curve["columns"][0] == "date"
    assert len(curve["rows"]) == payload["summary"]["n_trades"]
    for t in payload["trades"]:
        assert t["entry"][:10] == t["exit"][:10]


def test_intraday_interpretation_warns_about_the_clock_and_the_overlap(client, key):
    r = call(client, "run_backtest", {"spec": {
        "structure": "long_option", "cadence": "daily",
        "entry_time": "11:00", "exit_time": "14:00",
        "params": {"pct_offset": 0.5, "direction": "CE"}}}, key=key)
    interp = json.dumps(r["result"]["structuredContent"]["interpretation"])
    assert "NOT" in interp and "independent observations" in interp
    assert "fixed clock time is a signal" in interp


def test_a_positive_ratio_on_a_losing_book_is_called_out():
    """A long option's margin is the premium paid, so a 400 % winner contributes +4.0 to
    the mean return-on-margin while a total loss is capped at -1.0. The mean goes positive
    on a book that lost money, and so does the Sharpe built on it. Observed live: Sharpe
    +0.691 on a run that lost Rs 12,130."""
    out = knowledge.interpretation(
        {"n_trades": 53, "total_pnl_rupees": -12130.0, "mean_return_on_margin": 0.55}, {})
    blob = json.dumps(out)
    assert "LOST" in blob
    assert "capped at -100" in blob
    assert any("did not" in d for d in out["do_not_conclude"])


def test_what_is_persisted_is_what_was_released(client, key):
    """SUPERSEDES test_detail_levels_change_the_response_not_the_record.

    That test asserted the record always kept everything, which is what made the price
    meter decorative: detail='summary' was metered at ZERO and stored every per-leg price
    anyway, so the cheapest and most private-looking call was the cheapest way to harvest —
    read it straight back out at detail='full', or out of the unauthenticated report URL.

    The rule now is that the stored record cannot exceed what the caller asked for.
    """
    r = call(client, "run_backtest", {"spec": {
        "structure": "iron_fly", "entry_time": "09:30",
        "params": {"pct_width": 1.5, "entry_dte": 4}},
        "detail": "summary"}, key=key)
    payload = r["result"]["structuredContent"]
    bid = payload["backtest_id"]
    assert "trades" not in payload
    assert payload["data_release"]["price_points_released"] == 0

    # Asking for it back at full width cannot conjure prices that were never stored.
    back = call(client, "get_backtest", {"backtest_id": bid, "detail": "full"},
                key=key)["result"]["structuredContent"]
    assert not back.get("trades")
    assert "entry_price" not in json.dumps(back)

    # And the shareable report renders the stored record, so it cannot leak them either.
    row = store.get_result(bid)
    assert "entry_price" not in row["payload_json"]


def test_re_reading_a_result_is_metered_again(client, key):
    """A stored result can be fetched repeatedly. Each fetch crosses the boundary again, so
    an unmetered second route would defeat the meter on the first."""
    made = call(client, "run_backtest", {"spec": {
        "structure": "iron_condor", "entry_time": "09:30",
        "params": {"pct_offset": 1.5, "pct_width": 1.0, "entry_dte": 4}}}, key=key)
    p = made["result"]["structuredContent"]
    bid, first = p["backtest_id"], p["quota"]["price_points_used"]
    assert p["data_release"]["price_points_released"] > 0
    again = call(client, "get_backtest", {"backtest_id": bid}, key=key)
    assert again["result"]["structuredContent"]["quota"]["price_points_used"] > first


def test_costs_exceeding_the_edge_is_not_reported_as_a_percentage():
    """A negative share_of_edge_surviving means costs ate the whole gross edge and more.
    Phrasing that as 1 - share produced 'Costs consume 447 % of the gross edge' on a real
    intraday result: derivable, and meaningless. The useful reading is that the rule was
    gross-profitable and net-losing, which points at frequency rather than at the rule."""
    out = knowledge.interpretation(
        {"n_trades": 245, "total_pnl_rupees": -14381.0},
        {"cost_drag": {"gross_points_before_costs": 51.65, "slippage_points": 24.7,
                       "charges_points": 205.96, "net_points_after_costs": -179.01,
                       "share_of_edge_surviving": -3.4658}})
    blob = json.dumps(out)
    assert "447" not in blob and "%" not in "".join(out["reading"][-1:])
    assert "EXCEEDED" in blob
    assert any("gross edge" in d for d in out["do_not_conclude"])


def test_the_release_meter_counts_what_left_not_what_was_computed(client, key):
    """The record is stored full width whatever `detail` asked for, so the count on the
    RESPONSE has to be recomputed from the response. Billing a summary for prices it did
    not return would teach callers to avoid the cheapest and least exposing option."""
    args = {"spec": {"structure": "long_option", "cadence": "daily",
                     "entry_time": "11:00", "exit_time": "14:00",
                     "params": {"pct_offset": 0.5, "direction": "CE"}}}
    summ = call(client, "run_backtest", dict(args, detail="summary"), key=key)
    p = summ["result"]["structuredContent"]
    assert p["data_release"]["price_points_released"] == 0
    assert "entry_price" not in json.dumps(p)
    before = p["quota"]["price_points_used"]

    std = call(client, "run_backtest", dict(args, detail="standard"), key=key)
    q = std["result"]["structuredContent"]
    released = q["data_release"]["price_points_released"]
    # Trimmed to STANDARD_TRADES, so the count must be well under the full run's.
    assert 0 < released <= tools.STANDARD_TRADES * 4
    assert q["quota"]["price_points_used"] == before + released


# --------------------------------------------------------------- call log
#
# Two things this can never become: a leak of anything the caller did not send us, and a
# reason for a request to fail. Both are asserted rather than intended.

def test_every_call_is_logged_with_what_was_asked(client, key, account):
    call(client, "run_backtest", {"spec": {
        "structure": "iron_fly", "entry_time": "09:30",
        "params": {"pct_width": 1.5, "entry_dte": 4}}, "detail": "summary"}, key=key)
    call(client, "describe_coverage", {}, key=key)
    rows = store.recent_calls(account)
    assert {r["tool"] for r in rows} == {"run_backtest", "describe_coverage"}
    bt = [r for r in rows if r["tool"] == "run_backtest"][0]
    assert bt["outcome"] == "ok"
    assert "iron_fly" in bt["arguments_json"]
    assert bt["cpu_seconds"] > 0 and bt["response_bytes"] > 0
    assert bt["backtest_id"], "a logged backtest must resolve to its stored result"


def test_a_refusal_is_logged_with_its_reason(client, key, account):
    """A log that holds only the calls that worked cannot answer 'what went wrong'."""
    call(client, "run_backtest", {"spec": {"structure": "debit_spread", "params": {}}}, key=key)
    row = store.recent_calls(account)[0]
    assert row["outcome"] == "refused"
    assert "debit_spread" in row["refusal"]


def test_logging_failure_never_breaks_the_request(client, key, monkeypatch):
    """An audit log that can take the service down is worse than no audit log."""
    def boom(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(store, "connect", boom)
    store.record_call({"account_id": "acc", "method": "tools/call"})   # must not raise


def test_the_log_holds_no_response_body(client, key, account):
    """A full backtest payload is ~300 KB and is already stored once in `results`. Keeping
    a second copy per call would grow this database by gigabytes of duplicates."""
    r = call(client, "run_backtest", {"spec": {
        "structure": "iron_fly", "entry_time": "09:30",
        "params": {"pct_width": 1.5, "entry_dte": 4}}}, key=key)
    payload = r["result"]["structuredContent"]
    row = store.recent_calls(account)[0]
    blob = json.dumps(dict(row))
    assert str(payload["summary"]["total_pnl_rupees"]) not in blob
    assert row["response_bytes"] > 1000


def test_call_log_ages_out_with_the_spec_bodies(client, key, account):
    store.record_call({"account_id": account, "method": "tools/call", "tool": "x"},
                      now=time.time() - 40 * 86400)
    assert store.purge()["call_rows_deleted"] >= 1


# --------------------------------------------------------------- strategy book

QUALIFIER = {"structure": "iron_condor", "entry_time": "09:30",
             "params": {"pct_offset": 2.5, "pct_width": 1.0, "entry_dte": 2}}


def test_the_bar_is_not_made_money(client, key):
    """A losing result is obviously excluded, but so is a PROFITABLE one whose held-out
    period failed. P&L is what a parameter sweep maximises by construction, so on its own
    it is the weakest evidence this service produces."""
    r = call(client, "run_backtest", {"spec": {
        "structure": "iron_condor", "entry_time": "09:30",
        "params": {"pct_offset": 1.5, "pct_width": 1.0, "entry_dte": 4}},
        "detail": "summary"}, key=key)
    sb = r["result"]["structuredContent"]["strategy_book"]
    assert sb["qualified"] is False
    assert sb["checks"]["out_of_sample_held_up"]["pass"] is False
    # The near miss names what was missing rather than only reporting failure.
    assert "out_of_sample_held_up" in sb["note"]


def test_a_qualifying_result_is_kept_and_listed(client, key):
    r = call(client, "run_backtest", {"spec": QUALIFIER, "detail": "summary"}, key=key)
    sb = r["result"]["structuredContent"]["strategy_book"]
    assert sb["qualified"] is True and sb["entry_id"]
    listed = call(client, "list_strategies", {}, key=key)["result"]["structuredContent"]
    assert listed["n_entries"] == 1
    e = listed["strategies"][0]
    assert e["entry_id"] == sb["entry_id"]
    assert e["folds_profitable"] == e["folds_total"]
    assert e["spec"]["structure"] == "iron_condor"


def test_rerunning_a_spec_updates_the_entry_instead_of_duplicating(client, key):
    """A parameter sweep is dozens of near-identical specs. Without dedup the book fills
    with one idea at forty offsets and stops being a shortlist."""
    first = call(client, "run_backtest", {"spec": QUALIFIER, "detail": "summary"},
                 key=key)["result"]["structuredContent"]["strategy_book"]
    again = call(client, "run_backtest", {"spec": QUALIFIER, "detail": "summary"},
                 key=key)["result"]["structuredContent"]["strategy_book"]
    assert again["entry_id"] == first["entry_id"]
    assert again["times_seen"] == first["times_seen"] + 1
    assert call(client, "list_strategies", {}, key=key)[
        "result"]["structuredContent"]["n_entries"] == 1


def test_the_book_is_ranked_by_consistency_not_pnl(client, key):
    """Memory of a decision made elsewhere in this project: rank by consistency, not raw
    return. Sorting by P&L puts a strategy that won big in one fold and lost in two above
    one that made money in all three, every time."""
    listed = call(client, "list_strategies", {}, key=key)["result"]["structuredContent"]
    assert listed["order"] == "consistency"
    assert "worst walk-forward fold" in listed["bar"]["default_ranking"]
    # P&L ordering is available, and is labelled as the risky one.
    by_pnl = call(client, "list_strategies", {"order": "pnl"}, key=key)
    assert by_pnl["result"]["structuredContent"]["order"] == "pnl"


def test_the_book_is_scoped_to_the_account(client, key):
    """Another account's shortlist is the closest thing this service has to private IP."""
    call(client, "run_backtest", {"spec": QUALIFIER, "detail": "summary"}, key=key)
    other = client.post("/v1/signup", json={"email": "stranger@x.com"},
                        headers={"X-Forwarded-For": "10.9.9.9"}).json()["api_key"]
    listed = call(client, "list_strategies", {}, key=other)
    assert listed["result"]["structuredContent"]["n_entries"] == 0


def test_the_book_stores_no_prices(client, key):
    """Summary statistics only. The full result is already in `results` under its own
    retention rules, and a second copy here would outlive them."""
    call(client, "run_backtest", {"spec": QUALIFIER}, key=key)
    listed = call(client, "list_strategies", {}, key=key)["result"]["structuredContent"]
    blob = json.dumps(listed)
    assert "entry_price" not in blob and "legs" not in blob


def test_the_book_and_its_bar_are_documented_where_a_model_will_look():
    assert "strategy_book" in knowledge.TOPICS
    text = knowledge.TOPICS["strategy_book"]
    assert "is NOT logged" in text          # the prompt-logging limit, stated plainly
    assert "not 'made money'" in text.lower().replace("’", "'")
    assert "list_strategies" in knowledge.SERVER_INSTRUCTIONS
    # Every topic is also served as a resource; a new one must not break that.
    assert any(r["uri"].endswith("strategy_book") for r in knowledge.RESOURCES)


# --------------------------------------------------------------- account takeover
#
# Two paths, one weakness: identity was an unverified email address or a guessable id.
# Both handed an attacker somebody else's account, and — because tier is read off the
# ACCOUNT — somebody else's paid data.

def test_signup_is_not_a_login(client):
    """create_account returned the EXISTING account_id for a known email, and both signup
    handlers then issued a key against it. Typing a customer's address into the public form
    returned their key; the HTML path also set their 90-day session cookie."""
    first = client.post("/v1/signup", json={"email": "victim@corp.com"},
                        headers={"X-Real-IP": "10.1.1.1"})
    assert first.status_code == 200
    victim_account = first.json()["account_id"]

    again = client.post("/v1/signup", json={"email": "victim@corp.com"},
                        headers={"X-Real-IP": "10.1.1.2"})
    assert again.status_code == 409
    body = again.json()
    assert "api_key" not in body and "account_id" not in body
    assert victim_account not in json.dumps(body)


def test_signup_form_does_not_hand_over_a_session(client):
    client.post("/signup", data={"email": "web@corp.com"}, headers={"X-Real-IP": "10.2.2.1"})
    stolen = client.post("/signup", data={"email": "web@corp.com"},
                         headers={"X-Real-IP": "10.2.2.2"})
    assert stolen.status_code == 409
    assert "stratify_session" not in stolen.headers.get("set-cookie", "")


def test_issuing_a_key_requires_a_credential_not_an_account_id(client, account):
    """account_id is not a secret — it is on the dashboard, in the signup response, and in
    list_strategies. This endpoint used to mint a live key for any id that existed."""
    r = client.post("/v1/keys", json={"account_id": account})
    assert r.status_code == 401
    assert "api_key" not in r.json()


def test_a_key_cannot_be_minted_for_someone_elses_account(client, key, account):
    other = client.post("/v1/signup", json={"email": "other@corp.com"},
                        headers={"X-Real-IP": "10.3.3.1"}).json()["account_id"]
    r = client.post("/v1/keys", json={"account_id": other},
                    headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 403
    assert "api_key" not in r.json()


def test_unknown_account_is_not_an_existence_oracle(client):
    """The old 404-vs-200 split told an attacker which ids were real."""
    real = client.post("/v1/keys", json={"account_id": "acc_0000000000000000"}).status_code
    assert real == 401, "must not distinguish a real account from a fake one before auth"


def test_forwarded_for_cannot_forge_a_fresh_signup_identity(client):
    """nginx APPENDS the real peer to X-Forwarded-For, so the leftmost element is
    attacker-supplied. Reading it gave a fresh identity per request and the 3/hour account
    cap never fired — roughly 600 accounts an hour from one address."""
    import server.app as app_mod

    class _Req:
        def __init__(self, headers):
            self.headers = headers
            self.client = type("C", (), {"host": "203.0.113.9"})()

    monkey = os.environ.get("STRATIFY_BEHIND_PROXY")
    os.environ["STRATIFY_BEHIND_PROXY"] = "1"
    try:
        # Spoofed leftmost, real peer appended by the proxy on the right.
        ip = app_mod._client_ip(_Req({"x-forwarded-for": "1.2.3.4, 203.0.113.9"}))
        assert ip == "203.0.113.9", f"trusted the spoofed hop: {ip}"
        # X-Real-IP is overwritten by nginx, so it wins outright.
        ip = app_mod._client_ip(_Req({"x-real-ip": "203.0.113.9",
                                      "x-forwarded-for": "1.2.3.4, 203.0.113.9"}))
        assert ip == "203.0.113.9"
    finally:
        if monkey is None:
            os.environ.pop("STRATIFY_BEHIND_PROXY", None)
        else:
            os.environ["STRATIFY_BEHIND_PROXY"] = monkey


def test_the_schema_advertises_the_open_protocol():
    """The presets are a convenience; the open form is the product. If it is not in the
    schema no model will ever reach for it, and the whole capability is invisible."""
    spec = [t for t in tools.TOOLS if t["name"] == "run_backtest"][0]
    forms = spec["inputSchema"]["properties"]["spec"]["oneOf"]
    assert len(forms) == 2
    openform = [f for f in forms if "legs" in f.get("properties", {})][0]
    blob = json.dumps(openform)
    for capability in ("delta_near", "premium_near", "roll", "close_legs", "from_leg",
                       "portfolio", "max_adjustments", "next"):
        assert capability in blob, capability
    # And the description has to say the tier does not gate any of it.
    assert "tier" in spec["description"] and "window" in spec["description"]
