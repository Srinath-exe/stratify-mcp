"""Feedback intake, triage, and the admin console.

The things worth testing here are the boundaries, not the happy path: intake refuses
anonymous callers, one account's report cannot pull in another account's backtest, the
console is invisible without the admin flag, and re-filing a closed item reopens it rather
than being swallowed.
"""
import json
import re
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engine import honesty                                          # noqa: E402
from server import app as app_mod, feedback as fb, store            # noqa: E402

KEY_RE = re.compile(r"sk_live_[A-Za-z0-9_-]+")


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setattr(store, "DB_PATH", Path(d) / "service.sqlite")
        monkeypatch.setattr(honesty, "VARIANT_LOG", Path(d) / "variants.sqlite")
        yield


@pytest.fixture
def client():
    return TestClient(app_mod.app, base_url="https://testserver")


def signup(client, email="a@example.com"):
    r = client.post("/signup", data={"email": email})
    match = KEY_RE.search(r.text)
    return match.group(0) if match else None


def auth(key):
    return {"Authorization": f"Bearer {key}"}


# ---------------------------------------------------------------- classification

def test_category_is_inferred_when_not_given():
    category, tags = fb.classify("run_backtest returned a traceback",
                                 "every call errors out")
    assert category == "bug"
    assert "category:inferred" in tags


def test_an_explicit_category_always_beats_the_guess():
    # The reporter knows their own intent. The guess is kept as a tag so a systematic
    # disagreement between the two is visible rather than silently resolved.
    category, tags = fb.classify("please add banknifty",
                                 "it is broken without banknifty", "feature_request")
    assert category == "feature_request"
    assert "looks-like:bug" in tags


def test_dedup_ignores_dates_and_ids():
    a = fb.dedup_key("no data for 2019-03-14", "data_gap")
    b = fb.dedup_key("No data for 2021-07-02", "data_gap")
    assert a == b
    assert a != fb.dedup_key("no margin for iron condors", "data_gap")


def test_severity_defaults_differ_by_category():
    assert fb.infer_severity("x", "y", "bug") == "major"
    assert fb.infer_severity("x", "y", "feature_request") == "idea"
    assert fb.infer_severity("cannot use the service", "", "bug") == "blocker"


# ---------------------------------------------------------------- intake

def test_anonymous_intake_is_refused(client):
    r = client.post("/v1/feedback", json={"title": "something", "body": "broken"})
    assert r.status_code == 401


def test_a_key_can_file(client):
    key = signup(client)
    r = client.post("/v1/feedback", headers=auth(key),
                    json={"title": "margin looks too high on condors",
                          "body": "expected span-ish, got much more"})
    assert r.status_code == 201
    assert r.json()["feedback_id"].startswith("fb_")
    assert r.json()["category"] in fb.CATEGORIES


def test_a_body_is_required(client):
    key = signup(client)
    r = client.post("/v1/feedback", headers=auth(key),
                    json={"title": "it is broken", "body": ""})
    assert r.status_code == 400
    assert "body" in r.json()["error"].lower()


def test_refiling_updates_rather_than_duplicating(client):
    key = signup(client)
    payload = {"title": "iron condor margin too high", "body": "first report"}
    first = client.post("/v1/feedback", headers=auth(key), json=payload)
    second = client.post("/v1/feedback", headers=auth(key),
                         json={**payload, "body": "again, still wrong"})
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["feedback_id"] == second.json()["feedback_id"]
    row = store.get_feedback(first.json()["feedback_id"])
    assert row["times_seen"] == 2
    assert row["body"] == "again, still wrong"


def test_reporting_a_closed_item_reopens_it(client):
    key = signup(client)
    payload = {"title": "settlement price looks wrong", "body": "off by a lot"}
    fid = client.post("/v1/feedback", headers=auth(key), json=payload).json()["feedback_id"]
    store.update_feedback(fid, status="fixed")
    client.post("/v1/feedback", headers=auth(key), json={**payload, "body": "still wrong"})
    assert store.get_feedback(fid)["status"] == "reopened"


def test_context_never_crosses_an_account(client):
    key_a = signup(client, "a@example.com")
    signup(client, "b@example.com")
    account_b = store.connect().execute(
        "SELECT account_id FROM accounts WHERE email='b@example.com'").fetchone()[0]
    # A guessed backtest_id belonging to someone else must not be resolved into the row.
    store.save_result("some_other_key", "{}", "h", '{"secret": 1}')
    row = store.connect().execute("SELECT backtest_id FROM results").fetchone()[0]
    r = client.post("/v1/feedback", headers=auth(key_a),
                    json={"title": "a result of mine looks wrong",
                          "body": "detail here", "backtest_id": row})
    fid = r.json()["feedback_id"]
    context = json.loads(store.get_feedback(fid)["context_json"])
    assert "spec" not in context
    assert context["spec_note"].endswith("on this account")
    assert account_b not in json.dumps(context)


def test_my_reports_are_scoped_to_the_account(client):
    key_a = signup(client, "a@example.com")
    key_b = signup(client, "b@example.com")
    client.post("/v1/feedback", headers=auth(key_a),
                json={"title": "report from a", "body": "x"})
    assert client.get("/v1/feedback", headers=auth(key_b)).json()["reports"] == []
    assert len(client.get("/v1/feedback", headers=auth(key_a)).json()["reports"]) == 1


# ---------------------------------------------------------------- the MCP tool

def rpc(client, key, name, arguments):
    return client.post("/mcp", headers=auth(key), json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments}}).json()


def test_the_tool_is_listed_and_files(client):
    key = signup(client)
    listed = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1,
                                       "method": "tools/list"}).json()
    assert "submit_feedback" in [t["name"] for t in listed["result"]["tools"]]
    out = rpc(client, key, "submit_feedback",
              {"title": "would like banknifty", "body": "only nifty today"})
    text = json.dumps(out)
    assert "fb_" in text
    assert store.list_feedback()[0]["category"] == "feature_request"


def test_the_tool_refuses_a_report_it_cannot_act_on(client):
    key = signup(client)
    out = rpc(client, key, "submit_feedback", {"title": "bad", "body": "x"})
    # A ToolError comes back as a readable refusal, not a JSON-RPC error.
    assert "error" not in out
    assert "4 characters" in json.dumps(out)


# ---------------------------------------------------------------- the console

def test_the_console_is_invisible_without_the_flag(client):
    signup(client)
    assert client.get("/admin").status_code == 404


def test_the_console_is_invisible_to_a_stranger(client):
    assert client.get("/admin").status_code == 404
    assert client.post("/admin/feedback", data={"feedback_id": "x"}).status_code == 404


def test_an_admin_sees_the_queue_and_can_triage(client):
    key = signup(client, "boss@example.com")
    client.post("/v1/feedback", headers=auth(key),
                json={"title": "condor margin looks too high", "body": "detail"})
    store.grant_admin("boss@example.com")
    page = client.get("/admin")
    assert page.status_code == 200
    assert "condor margin looks too high" in page.text
    fid = store.list_feedback()[0]["feedback_id"]
    r = client.post("/admin/feedback", data={"feedback_id": fid, "status": "in_progress",
                                             "severity": "blocker",
                                             "triage_note": "reproduced"})
    assert r.status_code == 200
    row = store.get_feedback(fid)
    assert (row["status"], row["severity"], row["triage_note"]) == (
        "in_progress", "blocker", "reproduced")


def test_revoking_admin_takes_effect_immediately(client):
    signup(client, "boss@example.com")
    store.grant_admin("boss@example.com")
    assert client.get("/admin").status_code == 200
    store.grant_admin("boss@example.com", on=False)
    # Same 90-day session cookie, no re-login: the flag is re-read per request.
    assert client.get("/admin").status_code == 404


def test_triage_rejects_an_unknown_status(client):
    key = signup(client, "boss@example.com")
    client.post("/v1/feedback", headers=auth(key), json={"title": "a title", "body": "b"})
    store.grant_admin("boss@example.com")
    fid = store.list_feedback()[0]["feedback_id"]
    r = client.post("/admin/feedback", data={"feedback_id": fid, "status": "yolo"})
    assert r.status_code == 400
    assert store.get_feedback(fid)["status"] == "new"


def test_themes_count_distinct_accounts(client):
    for i in range(3):
        key = signup(client, f"u{i}@example.com")
        client.post("/v1/feedback", headers=auth(key),
                    json={"title": "please add banknifty options",
                          "body": "need banknifty", "category": "feature_request"})
    themes = store.feedback_themes()
    assert themes[0]["accounts"] == 3
    assert themes[0]["reports"] == 3


# ---------------------------------------------------------------- build_report

def _backtest(client, key):
    out = rpc(client, key, "run_backtest", {
        "spec": {"structure": "iron_condor", "entry_time": "09:30",
                 "params": {"pct_offset": 1.5, "pct_width": 1.0, "entry_dte": 4}},
        "lots": 1, "detail": "standard"})
    return out["result"]["structuredContent"]["backtest_id"]


def test_report_is_a_publishable_document(client):
    key = signup(client)
    out = rpc(client, key, "build_report", {"backtest_id": _backtest(client, key)})
    doc = out["result"]["structuredContent"]["document"]
    assert doc.startswith("<!doctype html>") and doc.rstrip().endswith("</html>")
    assert out["result"]["structuredContent"]["mime_type"] == "text/html"
    # An artifact sandbox blocks every one of these. If any appears, the published page
    # renders unstyled or half-drawn and the user sees a broken report.
    for fetching in ("<link", "<script src", "<img", "<iframe", "@import", "url("):
        assert fetching not in doc
    assert "http://" not in doc.replace("http://www.w3.org/2000/svg", "")
    # Theme-aware in all three states: explicit light, explicit dark, and the unstamped
    # default where only prefers-color-scheme separates them.
    assert "prefers-color-scheme:dark" in doc and "[data-theme=dark]" in doc


def test_report_leads_with_the_evidence_not_the_pnl(client):
    key = signup(client)
    out = rpc(client, key, "build_report", {"backtest_id": _backtest(client, key)})
    doc = out["result"]["structuredContent"]["document"]
    assert doc.index("What the evidence supports") < doc.index("The numbers")
    assert "Not investment advice" in doc


def test_report_cannot_be_built_for_someone_elses_backtest(client):
    key_a = signup(client, "a@example.com")
    key_b = signup(client, "b@example.com")
    bt = _backtest(client, key_a)
    out = rpc(client, key_b, "build_report", {"backtest_id": bt})
    text = json.dumps(out)
    assert "No backtest" in text
    assert "document" not in out.get("result", {}).get("structuredContent", {})


def test_an_unknown_id_reads_the_same_as_someone_elses(client):
    # Identical wording on both paths, so the refusal cannot be used to test whether a
    # guessed backtest_id exists.
    key = signup(client)
    a = rpc(client, key, "build_report", {"backtest_id": "bt_does_not_exist"})
    key_b = signup(client, "b@example.com")
    b = rpc(client, key_b, "build_report", {"backtest_id": _backtest(client, key)})
    strip = lambda o: json.dumps(o).split("No backtest")[1][:20]          # noqa: E731
    assert strip(a).split("'")[1] != strip(b).split("'")[1]   # different ids quoted
    assert json.dumps(a).count("No backtest") == json.dumps(b).count("No backtest")


def test_link_format_skips_the_document(client):
    key = signup(client)
    out = rpc(client, key, "build_report",
              {"backtest_id": _backtest(client, key), "format": "link"})
    sc = out["result"]["structuredContent"]
    assert "/r/" in sc["report_url"]
    assert "document" not in sc


def test_the_hosted_report_and_the_artifact_are_the_same_document(client):
    """Two renderers of one backtest is two sets of numbers waiting to disagree.

    They differ in exactly one place, deliberately: the artifact carries a
    'Source of record' line pointing at the hosted copy, and the hosted copy does not
    point at itself. Everything a reader could act on must be byte-identical.
    """
    key = signup(client)
    out = rpc(client, key, "build_report", {"backtest_id": _backtest(client, key)})
    sc = out["result"]["structuredContent"]
    hosted = client.get(sc["report_url"].replace("https://testserver", "")).text
    # [^<]* not \S+ : the URL butts straight up against </p>, and \S+ eats the tag too.
    provenance = re.compile(r" Source of record: [^<]*")
    assert provenance.sub("", sc["document"]) == provenance.sub("", hosted)
    assert "Source of record" in sc["document"]
    assert "Source of record" not in hosted


# ---------------------------------------------------------------- the response digest

def test_the_text_block_is_a_brief_not_the_whole_payload(client):
    """A model handed 13,000 tokens of tables narrates tables. The text block is what it
    reads first, so it has to be short; structuredContent still carries everything."""
    key = signup(client)
    out = client.post("/mcp", headers=auth(key), json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "run_backtest", "arguments": {
            "spec": {"structure": "iron_condor", "entry_time": "09:30",
                     "params": {"pct_offset": 1.5, "pct_width": 1.0, "entry_dte": 4}},
            "lots": 1, "detail": "standard"}}}).json()["result"]
    text = out["content"][0]["text"]
    full = json.dumps(out["structuredContent"])
    assert len(text) < 4000, "the brief has grown back into a data dump"
    assert len(full) > len(text) * 3, "structuredContent should still carry everything"
    # Nothing is withheld -- the full payload is intact alongside it.
    for section in ("summary", "honesty", "equity_curve", "breakdown"):
        assert section in out["structuredContent"]


def test_the_brief_tells_the_model_not_to_transcribe(client):
    key = signup(client)
    out = client.post("/mcp", headers=auth(key), json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "run_backtest", "arguments": {
            "spec": {"structure": "iron_condor", "entry_time": "09:30",
                     "params": {"pct_offset": 1.5, "pct_width": 1.0, "entry_dte": 4}}}}}
        ).json()["result"]
    text = out["content"][0]["text"]
    assert "Do not reproduce the tables" in text
    assert "build_report" in text
    assert "VERDICT" in text


def test_the_brief_quotes_the_honesty_panel_rather_than_paraphrasing_it(client):
    """Paraphrasing turned 'survives having been searched for across 2 variants' into
    '100% probability the edge is real'. The panel's wording carries a scope clause that
    is the entire content of the claim."""
    key = signup(client)
    out = client.post("/mcp", headers=auth(key), json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "run_backtest", "arguments": {
            "spec": {"structure": "iron_condor", "entry_time": "09:30",
                     "params": {"pct_offset": 1.5, "pct_width": 1.0, "entry_dte": 4}}}}}
        ).json()["result"]
    text = out["content"][0]["text"]
    reading = out["structuredContent"]["honesty"]["multiple_comparisons"]["reading"]
    assert reading in text
    assert "probability the edge is real" not in text


def test_a_refusal_is_still_plain_text(client):
    # The digest must never swallow a refusal -- those have no summary and must read as
    # themselves so the model can correct the call.
    key = signup(client)
    out = client.post("/mcp", headers=auth(key), json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "run_backtest",
                   "arguments": {"spec": {"structure": "nonsense", "params": {}}}}}).json()
    assert out["result"]["isError"] is True
    assert "unknown structure" in out["result"]["content"][0]["text"]


def test_the_report_carries_the_stratify_mark(client):
    key = signup(client)
    out = rpc(client, key, "build_report", {"backtest_id": _backtest(client, key)})
    doc = out["result"]["structuredContent"]["document"]
    assert "Made with" in doc and "Stratify MCP" in doc
    # Drawn, not fetched: an <img> would be blocked by the artifact sandbox.
    assert "<img" not in doc


def test_every_surface_shares_one_design_system(client):
    """The report and the internal catalogue must not be two stylesheets that agree
    today and drift tomorrow."""
    from server import design
    key = signup(client)
    out = rpc(client, key, "build_report", {"backtest_id": _backtest(client, key)})
    doc = out["result"]["structuredContent"]["document"]
    assert design.TOKENS.split("\n")[0] in doc
    for primitive in (".ln{stroke:var(--accent)", ".bb{fill:var(--crit)", ".tk{fill:var(--faint)"):
        assert primitive in doc


# ---------------------------------------------------------------- reported by a user

def _run(client, key, detail="standard"):
    return client.post("/mcp", headers=auth(key), json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "run_backtest", "arguments": {
            "spec": {"structure": "iron_condor", "entry_time": "09:30",
                     "params": {"pct_offset": 1.5, "pct_width": 1.0, "entry_dte": 4}},
            "lots": 1, "detail": detail}}}).json()["result"]


def test_the_brief_describes_what_is_actually_attached(client):
    """It used to promise 'the trades with their leg prices' on every response, including
    summary ones that carried none. A caller went looking, found nothing, and filed a bug."""
    key = signup(client)
    summary = _run(client, key, "summary")["content"][0]["text"]
    standard = _run(client, key, "standard")["content"][0]["text"]
    assert "NO per-trade rows" in summary
    assert "trades" in standard and "NO per-trade rows" not in standard


def test_full_on_a_record_saved_at_standard_says_why(client):
    """A record is trimmed BEFORE it is stored, so detail='full' afterwards cannot return
    trades that were never persisted. Returning fewer in silence reads as data loss."""
    key = signup(client)
    bt = _run(client, key, "standard")["structuredContent"]
    if (bt.get("trade_detail") or {}).get("trades_total", 0) <= len(bt.get("trades") or []):
        pytest.skip("this window produced no truncation to test")
    out = client.post("/mcp", headers=auth(key), json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "get_backtest", "arguments": {
            "backtest_id": bt["backtest_id"], "detail": "full"}}}).json()["result"]
    note = out["structuredContent"].get("detail_note", "")
    assert "could not be honoured" in note
    assert "re-run" in note.lower()
    assert "not data loss" in note


def test_the_tool_list_is_declared_mutable(client):
    """It was declared immutable, so clients cached it forever and a newly added tool was
    invisible to every already-connected session while the response text advertised it."""
    caps = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1,
                                     "method": "initialize", "params": {}}
                       ).json()["result"]["capabilities"]
    assert caps["tools"]["listChanged"] is True


def test_the_brief_does_not_promise_a_tool_unconditionally(client):
    """A client that connected before build_report existed cannot see it. Advertising it
    as if it were certainly there is what produced the bug report."""
    key = signup(client)
    text = _run(client, key)["content"][0]["text"]
    assert "If your client lists a tool called build_report" in text
    # The URL works with no tool call at all, so it is stated unconditionally.
    assert "REPORT:" in text and "/r/" in text


# ---------------------------------------------------------------- inline chat view

def test_the_ui_template_is_listed_as_a_resource(client):
    out = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1,
                                    "method": "resources/list"}).json()["result"]
    ui = [r for r in out["resources"] if r["uri"].startswith("ui://")]
    assert len(ui) == 1
    # The mimeType is what a host matches on to decide it can render this.
    assert ui[0]["mimeType"] == "text/html;profile=mcp-app"


def test_the_template_is_readable_and_self_contained(client):
    from server import appview
    out = client.post("/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "resources/read",
        "params": {"uri": appview.URI}}).json()["result"]
    doc = out["contents"][0]["text"]
    assert out["contents"][0]["mimeType"] == "text/html;profile=mcp-app"
    assert doc.startswith("<!doctype html>")
    # The iframe CSP defaults to deny-everything. Anything fetched here simply fails.
    for fetching in ("<link", "<script src", "<img", "@import", "url("):
        assert fetching not in doc
    assert "http://" not in doc.replace("http://www.w3.org/2000/svg", "")


def test_result_tools_carry_the_ui_template(client):
    """This is what makes the charts automatic. The host renders the template because the
    TOOL points at it -- the model is not consulted and cannot skip it."""
    tools_out = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1,
                                          "method": "tools/list"}).json()["result"]["tools"]
    by_name = {t["name"]: t for t in tools_out}
    for name in ("run_backtest", "get_backtest"):
        assert by_name[name]["_meta"]["ui"]["resourceUri"] == "ui://stratify/backtest"
    # Tools with nothing to draw must not claim a view.
    assert "_meta" not in by_name["describe_coverage"]


def test_the_apps_extension_is_declared(client):
    caps = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1,
                                     "method": "initialize", "params": {}}
                       ).json()["result"]["capabilities"]
    ext = caps["extensions"]["io.modelcontextprotocol/ui"]
    assert "text/html;profile=mcp-app" in ext["mimeTypes"]


def test_the_view_speaks_the_spec_methods(client):
    """Guards against the template drifting away from the 2026-01-26 contract."""
    from server import appview
    doc = appview.TEMPLATE
    for method in ("ui/initialize", "ui/notifications/tool-result",
                   "ui/notifications/size-changed", "ui/notifications/host-context-changed",
                   "ui/request-display-mode", "ui/open-link", "ui/message",
                   "ui/resource-teardown"):
        assert method in doc, f"template no longer handles {method}"
    assert "'2026-01-26'" in doc


def test_the_view_never_stays_silent_about_its_height(client):
    """In flexible mode the host sizes the frame from size-changed. An environment with no
    layout measures zero, and reporting nothing then leaves the frame collapsed."""
    from server import appview
    assert "ctx.displayMode==='fullscreen'?900:560" in appview.TEMPLATE


def test_the_view_and_the_report_share_one_design_system(client):
    from server import appview, design
    assert design.TOKENS.split("\n")[0] in appview.TEMPLATE
    assert ".ln{stroke:var(--accent)" in appview.TEMPLATE


# ---------------------------------------------------------------- full strategy report

def test_the_full_report_is_a_link_not_a_document(client):
    """It embeds a charting library and years of index candles -- roughly 75,000 tokens if
    it crossed the conversation."""
    key = signup(client)
    bt = _backtest(client, key)
    out = rpc(client, key, "build_report", {"backtest_id": bt, "format": "full"})
    sc = out["result"]["structuredContent"]
    assert "/report/" in sc["full_report_url"]
    assert "document" not in sc
    assert sc["bytes"] > 200_000
    page = client.get(sc["full_report_url"].replace("https://testserver", ""))
    assert page.status_code == 200
    assert "Not investment advice" in page.text


def test_the_full_report_covers_every_trade_not_the_released_subset(client):
    """The stored payload is trimmed to what was released. Sizing a capital curve from the
    first 300 of 380 trades ends the strategy eighteen months early and reports it as a
    result."""
    from engine import backtest, detail, spec as spec_mod
    from engine import db as engine_db
    sp = spec_mod.parse({"structure": "iron_condor", "entry_time": "09:30",
                         "params": {"pct_offset": 1.5, "pct_width": 1.0, "entry_dte": 4}},
                        tier="free")
    token = engine_db.use_tier("free")
    try:
        result = backtest.run(sp, lots=1)
    finally:
        engine_db.current_user.reset(token)
    everything = detail.rows_for_chart(result)
    capped = detail.build(result)["trades"]
    assert len(everything) >= len(capped)
    # And the uncapped path must release nothing.
    assert not any("entry_price" in json.dumps(r) for r in everything)


def test_the_capital_model_skips_trades_it_cannot_afford(client):
    from server import sizing
    trades = [{"pnl_rupees": 1000, "margin_points": 250, "lot_size": 50}] * 5
    tiny = sizing.apply(trades, capital=10_000, deploy=0.10)
    assert tiny["trades_taken"] == 0
    assert tiny["trades_skipped_insufficient_capital"] == 5
    # Taking it anyway at one lot is how a backtest reports returns on capital the account
    # did not have.
    assert tiny["ending_capital"] == 10_000


def test_position_size_changes_the_answer_completely(client):
    """The sensitivity table is the point of the report, so this guards the finding."""
    from server import sizing
    from engine import backtest, detail, spec as spec_mod
    from engine import db as engine_db
    sp = spec_mod.parse({"structure": "iron_condor", "entry_time": "09:30",
                         "params": {"pct_offset": 1.5, "pct_width": 1.0, "entry_dte": 4}},
                        tier="free")
    token = engine_db.use_tier("free")
    try:
        rows = detail.rows_for_chart(backtest.run(sp, lots=1))
    finally:
        engine_db.current_user.reset(token)
    if len(rows) < 20:
        pytest.skip("too few trades in the free window to show the spread")
    table = sizing.sensitivity(rows)
    assert len(table) == len(sizing.SENSITIVITY)
    # Drawdown must worsen monotonically with leverage -- if it does not, the model is wrong.
    dds = [r["max_drawdown_pct"] for r in table]
    assert dds == sorted(dds, reverse=True)


def test_full_reports_are_rate_limited(client):
    key = signup(client)
    bt = _backtest(client, key)
    from server import tools as tools_mod
    original = tools_mod.FULL_REPORTS_PER_HOUR
    tools_mod.FULL_REPORTS_PER_HOUR = 1
    try:
        first = rpc(client, key, "build_report", {"backtest_id": bt, "format": "full"})
        assert "full_report_url" in first["result"]["structuredContent"]
        second = rpc(client, key, "build_report", {"backtest_id": bt, "format": "full"})
        assert "limit reached" in json.dumps(second).lower()
    finally:
        tools_mod.FULL_REPORTS_PER_HOUR = original


def test_the_full_report_keeps_the_tradingview_attribution(client):
    """Apache-2.0 requires crediting TradingView on any user-facing page."""
    from server import fullreport
    assert "attributionLogo:true" in fullreport.JS
    assert "Licensed under Apache License 2.0" in fullreport.LWC[:400]
