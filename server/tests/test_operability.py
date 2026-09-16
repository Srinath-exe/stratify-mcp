"""Can this service be run, watched, and recovered?

These are the launch-readiness properties, and none of them are exercised by the
functional tests: a backtest that returns the right numbers is worth nothing if the box
dies and nobody notices, or if the database is lost and no backup restores.

Each test here corresponds to a way the service could have failed in production without
any existing test going red.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server import app as app_mod, quota, store  # noqa: E402
from engine import honesty  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


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


# ---------------------------------------------------------------- health

def test_liveness_does_not_touch_clickhouse(client, monkeypatch):
    """/healthz must stay dependency-free.

    Docker restarts the container when it fails. If it consulted ClickHouse, a database
    blip would restart a healthy app and add a restart storm to an existing outage. This
    asserts the separation by breaking the database and requiring liveness to shrug.
    """
    import engine.db as engine_db
    monkeypatch.setattr(engine_db, "rows",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("CH down")))
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_readiness_reports_each_dependency_separately(client):
    r = client.get("/readyz")
    body = r.json()
    assert set(body["checks"]) == {"clickhouse", "service_db"}
    # Whatever the verdict, the shape has to let an operator see WHICH one is broken --
    # a single boolean is what made the old health check useless at 3 a.m.
    for name, check in body["checks"].items():
        assert "ok" in check, name
        assert check["ok"] or "error" in check, name


def test_readiness_is_503_when_clickhouse_is_unreachable(client, monkeypatch):
    import server.app as app_module
    monkeypatch.setattr(app_module.engine_db, "rows",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("connection refused")))
    r = client.get("/readyz")
    assert r.status_code == 503
    assert r.json()["checks"]["clickhouse"]["ok"] is False
    assert "connection refused" in r.json()["checks"]["clickhouse"]["error"]


def test_readiness_fails_on_a_short_window_not_just_an_unreachable_one(client, monkeypatch):
    """A rebuilt-but-unfilled table is the dangerous case: it answers instantly, so every
    other probe calls it healthy, and every backtest silently returns zero trades."""
    import server.app as app_module
    monkeypatch.setattr(app_module.engine_db, "rows",
                        lambda *a, **k: [{"lo": "2025-07-01", "hi": "2025-07-10", "days": 8}])
    r = client.get("/readyz")
    assert r.status_code == 503
    assert r.json()["checks"]["clickhouse"]["trading_days"] == 8


def test_service_db_probe_proves_writability_and_leaves_nothing_behind():
    assert store.health_probe() is True
    con = sqlite3.connect(store.DB_PATH)
    left = con.execute(
        "SELECT count(*) FROM sqlite_master WHERE name='_health'").fetchone()[0]
    con.close()
    assert left == 0, "the health probe must roll its own write away"


def test_service_db_probe_fails_when_the_database_cannot_be_written(monkeypatch):
    """A read-only mount or a full disk leaves every SELECT working while signup, key
    issuance and metering all fail — so the probe has to write, and has to raise."""
    def refuse():
        raise sqlite3.OperationalError("attempt to write a readonly database")
    monkeypatch.setattr(store, "connect", refuse)
    with pytest.raises(sqlite3.OperationalError):
        store.health_probe()


# ---------------------------------------------------------------- retention

def test_purge_deletes_built_reports_and_reclaims_the_space():
    """Built reports were the one table that grew without limit -- 300-500 KB each, on the
    same disk as ClickHouse, outliving the results they were rendered from."""
    store.create_account("a@example.com")
    account_id, _ = store.create_account("b@example.com")
    store.save_full_report("bt_x", account_id, "<html>" + "x" * 400_000 + "</html>")
    assert store.full_report_quota(account_id, 100)[1] == 1

    out = store.purge(now=time.time() + 40 * 86400)
    assert out["full_reports_deleted"] == 1
    assert store.full_report_quota(account_id, 100)[1] == 0


def test_purge_keeps_result_rows_so_a_backtest_id_still_resolves():
    """Retention blanks bodies; it must not delete rows, or an id a user wrote down
    starts 404-ing instead of explaining itself."""
    account_id, _ = store.create_account("c@example.com")
    key_id, _ = store.issue_key(account_id)
    bt, _token = store.save_result(key_id, json.dumps({"structure": "short_strangle"}),
                                   "hash0", json.dumps({"summary": {"n_trades": 40}}))
    store.purge(now=time.time() + 40 * 86400)
    row = store.get_result(bt)
    assert row is not None
    assert json.loads(row["payload_json"])["purged"] is True


# ---------------------------------------------------------------- shared links

def test_an_expired_report_link_says_so_instead_of_rendering_an_empty_report(client):
    """The failure this replaces: a purged payload rendered a complete, well-formed page
    reading 'no verdict', 'None/100' and 'Nothing to chart'. Report links get shared, so
    a month later every shared link quietly claimed the strategy measured to nothing."""
    account_id, _ = store.create_account("d@example.com")
    key_id, _ = store.issue_key(account_id)
    _bt, token = store.save_result(key_id, json.dumps({"structure": "short_strangle"}),
                                   "hash0", json.dumps({"summary": {"n_trades": 40}}))
    store.purge(now=time.time() + 40 * 86400)

    r = client.get(f"/r/{token}")
    assert r.status_code == 410
    assert "expired" in r.text.lower()
    for ghost in ("None/100", "Nothing to chart", "no verdict"):
        assert ghost not in r.text


def test_an_unknown_report_token_gets_a_page_not_a_bare_heading(client):
    r = client.get("/r/definitely-not-a-real-token")
    assert r.status_code == 404
    assert "<h1>No such report</h1>" != r.text.strip()
    assert "stratify" in r.text.lower()


def test_an_expired_built_report_says_so(client):
    r = client.get("/report/definitely-not-a-real-token")
    assert r.status_code == 410
    assert "30 days" in r.text


# ---------------------------------------------------------------- backup

@pytest.mark.skipif(shutil.which("sqlite3") is None, reason="sqlite3 CLI not installed")
def test_backup_produces_an_archive_that_actually_restores(tmp_path, monkeypatch):
    """The only question a backup has to answer: after a total loss, does a key that
    worked before still work? Anything less is a file, not a backup.

    This runs the SHIPPED script -- not a reimplementation of it -- so the test cannot
    pass while the thing cron executes is broken.
    """
    # A realistic repo layout: a populated database and the .env holding the pepper.
    repo = tmp_path / "repo"
    (repo / "server" / "state").mkdir(parents=True)
    (repo / "server").joinpath("backup.sh").write_bytes(
        (REPO / "server" / "backup.sh").read_bytes())
    (repo / "server" / "backup.sh").chmod(0o755)
    pepper = "test-pepper-value-9f3a"
    (repo / ".env").write_text(f"STRATIFY_KEY_PEPPER={pepper}\n")

    db = repo / "server" / "state" / "service.sqlite"
    env = dict(os.environ, STRATIFY_SERVICE_DB=str(db), STRATIFY_KEY_PEPPER=pepper)
    make = ("import sys; sys.path.insert(0, %r)\n"
            "from server import store\n"
            "a, _ = store.create_account('restore@example.com')\n"
            "print(store.issue_key(a)[1])\n") % str(REPO)
    key = subprocess.run([sys.executable, "-c", make], env=env, cwd=REPO,
                         capture_output=True, text=True, check=True).stdout.strip()

    dest = tmp_path / "backups"
    run = subprocess.run(["bash", str(repo / "server" / "backup.sh")],
                         env=dict(env, STRATIFY_BACKUP_DIR=str(dest)),
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    archives = list(dest.glob("stratify-*.tar.gz"))
    assert len(archives) == 1

    # Total loss, then restore from the archive alone.
    restored = tmp_path / "restored"
    restored.mkdir()
    subprocess.run(["tar", "-xzf", str(archives[0]), "-C", str(restored)], check=True)
    assert (restored / "RESTORE.txt").exists(), "a restore needs instructions with it"

    recovered_pepper = [l.split("=", 1)[1].strip()
                        for l in (restored / "env").read_text().splitlines()
                        if l.startswith("STRATIFY_KEY_PEPPER=")][0]
    check = ("import sys; sys.path.insert(0, %r)\n"
             "from server import store\n"
             "print('YES' if store.authenticate(%r) else 'NO')\n") % (str(REPO), key)
    out = subprocess.run(
        [sys.executable, "-c", check], cwd=REPO, capture_output=True, text=True,
        env=dict(os.environ, STRATIFY_SERVICE_DB=str(restored / "service.sqlite"),
                 STRATIFY_KEY_PEPPER=recovered_pepper)).stdout.strip()
    assert out == "YES", "a key that worked before the loss must work after the restore"

    # And the pepper must be load-bearing, or the archive is carrying it for no reason.
    wrong = subprocess.run(
        [sys.executable, "-c", check], cwd=REPO, capture_output=True, text=True,
        env=dict(os.environ, STRATIFY_SERVICE_DB=str(restored / "service.sqlite"),
                 STRATIFY_KEY_PEPPER="not-the-pepper")).stdout.strip()
    assert wrong == "NO"


@pytest.mark.skipif(shutil.which("sqlite3") is None, reason="sqlite3 CLI not installed")
def test_backup_refuses_to_call_an_empty_database_a_backup(tmp_path):
    """Backing up the wrong path is at least as likely as corruption, and it fails
    silently: a valid, empty SQLite file passes every integrity check there is."""
    repo = tmp_path / "repo"
    (repo / "server" / "state").mkdir(parents=True)
    (repo / "server" / "backup.sh").write_bytes((REPO / "server" / "backup.sh").read_bytes())
    (repo / "server" / "backup.sh").chmod(0o755)
    subprocess.run(["sqlite3", str(repo / "server" / "state" / "service.sqlite"),
                    "CREATE TABLE accounts (id TEXT); CREATE TABLE api_keys (id TEXT);"],
                   check=True)
    run = subprocess.run(["bash", str(repo / "server" / "backup.sh")],
                         env=dict(os.environ, STRATIFY_BACKUP_DIR=str(tmp_path / "b")),
                         capture_output=True, text=True)
    assert run.returncode != 0
    assert "zero accounts" in run.stderr


# ---------------------------------------------------------------- directory review

def test_every_tool_carries_a_title_and_a_safety_hint():
    """Missing tool annotations are one of the two most common Connectors Directory
    rejections, and they are invisible until a reviewer looks: the server works perfectly
    without them. They also drive auto-permissions in Claude — a read-only tool can run
    without a per-call confirmation, so an unannotated read tool nags the user forever.

    Asserted for EVERY tool rather than a fixed list, so a tool added later cannot ship
    unannotated.
    """
    from server import tools as tools_mod
    for tool in tools_mod.TOOLS:
        ann = tool.get("annotations") or {}
        assert ann.get("title"), f"{tool['name']} has no annotations.title"
        assert "readOnlyHint" in ann, f"{tool['name']} declares no readOnlyHint"
        assert "destructiveHint" in ann, f"{tool['name']} declares no destructiveHint"
        # ChatGPT's portal refuses to scan a tool with any of the three hints unset. None
        # of ours reaches outside this service, so the answer is false everywhere.
        assert ann.get("openWorldHint") is False, f"{tool['name']} declares no openWorldHint"
        assert len(tool["name"]) <= 64, f"{tool['name']} exceeds the 64-character limit"
        assert tool.get("description"), f"{tool['name']} has no description"


def test_read_only_hints_match_what_the_tool_actually_does():
    """A tool that writes must not claim readOnlyHint, because Claude uses that hint to
    skip the confirmation prompt. Getting it wrong is worse than omitting it."""
    from server import tools as tools_mod
    writes = {"submit_feedback", "build_report"}
    for tool in tools_mod.TOOLS:
        ann = tool["annotations"]
        expected = tool["name"] not in writes
        assert ann["readOnlyHint"] is expected, (
            f"{tool['name']}: readOnlyHint={ann['readOnlyHint']} contradicts its behaviour")


def test_annotations_survive_the_tools_list_call(client):
    """The requirement is on the wire, not in the source: a reviewer reads tools/list."""
    listed = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1,
                                       "method": "tools/list"}).json()["result"]["tools"]
    assert listed, "tools/list returned nothing"
    for tool in listed:
        assert (tool.get("annotations") or {}).get("title"), tool["name"]


def test_the_privacy_policy_does_not_point_at_a_page_that_does_not_exist(client):
    """It promised "the address on the contact page" and /contact was a 404 — a dead
    pointer on the one document an Anthropic reviewer is guaranteed to read, and the
    stated cause of automatic rejection."""
    assert client.get("/contact").status_code == 200
    for page in ("/privacy", "/terms"):
        text = client.get(page).text
        assert "contact page" not in text.lower(), f"{page} still points at a dead page"


def test_the_support_address_appears_everywhere_once_it_is_configured(monkeypatch):
    """One variable, three pages. An address that disagrees with itself across the privacy
    policy, the terms and the contact page is worse than none."""
    import importlib
    from server import site as site_mod
    monkeypatch.setenv("STRATIFY_SUPPORT_EMAIL", "help@example.com")
    reloaded = importlib.reload(site_mod)
    try:
        for doc in (reloaded.PRIVACY, reloaded.TERMS, reloaded.CONTACT):
            assert "help@example.com" in doc["contact"], doc["title"]
    finally:
        monkeypatch.delenv("STRATIFY_SUPPORT_EMAIL", raising=False)
        importlib.reload(site_mod)


def test_no_address_is_published_while_none_can_receive_mail():
    """Publishing an address on a domain with no MX bounces silently, and a reader who
    wrote to it would believe they had reached someone. Saying "use the feedback tool" is
    the honest answer until the DNS exists."""
    from server import site as site_mod
    if not site_mod.SUPPORT_EMAIL:
        assert "@" not in site_mod.PRIVACY["contact"]
        assert "feedback tool" in site_mod.PRIVACY["contact"]


# ---------------------------------------------------------------- client reach

def test_every_advertised_client_snippet_says_how_it_authenticates():
    """A snippet that omits the credential produces a client that 401s on its first call,
    and the user blames the service.

    There are two legitimate shapes now. A header client must show where the key goes. A
    browser client must NOT show one -- ChatGPT, Gemini and Claude's connector UI all
    refuse a bearer key and run OAuth instead -- but it must say so, or the missing key
    reads as an omission rather than the point.
    """
    from server import site as site_mod
    snippets = site_mod.connect_snippets("https://mcp.example.com/mcp")
    assert {s["key"] for s in snippets} >= {
        "claude-code", "opencode", "gemini-cli", "codex", "browser"}
    for snip in snippets:
        body = snip["body"]
        assert "https://mcp.example.com/mcp" in body, snip["key"]
        has_key = "sk_live_" in body or "STRATIFY_API_KEY" in body
        says_signin = "sign in" in body.lower() or "no api key" in body.lower()
        assert has_key or says_signin, (
            f"{snip['key']} shows neither a credential nor how to sign in")


def test_the_publishable_manifests_are_valid_and_point_at_this_service():
    """These are what gets uploaded to the Gemini extension gallery and the MCP registry.
    A typo in either is discovered by a stranger whose client will not connect."""
    import json as _json
    root = Path(__file__).resolve().parents[2]
    url = "https://stratify-mcp.aeon-labs.site/mcp"

    gem = _json.loads((root / "packages" / "gemini-extension"
                       / "gemini-extension.json").read_text())
    assert gem["mcpServers"]["stratify"]["httpUrl"] == url
    assert "Authorization" in gem["mcpServers"]["stratify"]["headers"]
    assert (root / "packages" / "gemini-extension"
            / gem["contextFileName"]).exists(), "contextFileName names a missing file"

    reg = _json.loads((root / "packages" / "server.json").read_text())
    remote = reg["remotes"][0]
    assert remote["type"] == "streamable-http"
    assert remote["url"] == url
    assert remote["headers"][0]["isSecret"] is True, "an API key must be marked secret"


def test_the_url_key_route_stays_off_unless_deliberately_enabled():
    """A key in a URL is a key in an access log. This route exists for clients that can
    pass neither a header nor OAuth, and it must never become on-by-default: the flag is
    the whole control, and a default flip would silently start logging live credentials.
    """
    from server import app as app_module
    assert app_module.ALLOW_URL_KEY is False, (
        "STRATIFY_ALLOW_URL_KEY defaults on — enabling it also requires an nginx "
        "location block with access_log off, or keys land in the access log")


# ---------------------------------------------------------------- one report design

def test_the_full_format_uses_the_same_renderer_as_the_hosted_page():
    """Two renderers of one backtest is two sets of numbers waiting to disagree — and they
    did: the same result read +Rs 5.1k on one page and -Rs 4.99 L on the other, because
    fullreport.py applied a capital model and /r/ did not. Both were right about different
    questions and neither said which. `format='full'` now produces the same document."""
    src = (REPO / "server" / "tools.py").read_text()
    body = src[src.index("def _full_report"):]
    body = body[:body.index("\ndef ")] if "\ndef " in body[10:] else body
    assert "reportui.render" in body, "the full report must use the shared renderer"
    assert "fullreport.render" not in body, "fullreport.py is a second renderer; do not"


def test_the_full_format_still_buys_every_trade():
    """That is the whole reason it exists and is separately rate-limited: the stored
    payload was trimmed before it was saved, so the hosted page draws the first N."""
    src = (REPO / "server" / "tools.py").read_text()
    body = src[src.index("def _full_report"):src.index("def _full_report") + 4000]
    assert "MAX_TRADES_RETURNED" in body


def test_capital_arguments_seed_the_view_rather_than_fixing_it():
    """The controls stay live, so a reader handed a report at one capital can ask what
    another would have done without requesting a new one."""
    import inspect
    from server import capitalview, reportui as ru
    assert "capital" in inspect.signature(ru.render).parameters
    assert "capital" in inspect.signature(capitalview.render).parameters
    assert "capital" in inspect.signature(capitalview.series).parameters


# ---------------------------------------------------------------- mobile

def test_the_info_overlays_work_without_a_pointer():
    """They were hover-only: thirteen icons that did nothing at all when tapped, on the
    surface most likely to be read on a phone. The prose behind them is not decoration —
    it is where every section's explanation went when the ledes were removed."""
    src = (REPO / "server" / "reportui.py").read_text()
    js = src[src.index("_INFO_JS"):src.index("_INFO_JS") + 4000]
    assert "'click'" in js, "no click handler: the overlay is unreachable by touch"
    assert "touchstart" in js, "a tap also fires a synthetic mouseover; it must be ignored"
    # Tapping elsewhere is the only way to dismiss on a touchscreen.
    assert "else if(open)" in js or "else if (open)" in js


def test_the_info_icon_has_a_thumb_sized_hit_area():
    """18px reads best in the type; a finger needs about 44. The pseudo-element grows the
    target without fattening the dot or changing the row's height."""
    css = (REPO / "server" / "reportui.py").read_text()
    assert ".info::after" in css
    assert "44px" in css[css.index(".info::after"):css.index(".info::after") + 300]


def test_charts_that_scroll_say_so():
    """Every chart is authored wider than a phone, so it renders at native size and is
    clipped by the card — and a clipped chart reads as a broken chart. The edge shadow is
    painted by the scroll container, so it appears only where there is more to see."""
    css = (REPO / "server" / "reportlab.py").read_text()
    plot = css[css.index(".plot{"):css.index(".plot svg{")]
    assert "background-attachment:local" in plot, "no scroll-linked edge shadow"
    # Flicking a chart at its edge otherwise hands the gesture to the browser and
    # navigates back a page on iOS, losing the report mid-read.
    assert "overscroll-behavior-x:contain" in plot


def test_the_report_declares_a_viewport():
    """Without it a phone renders the page at 980px and scales it down, which makes every
    other mobile fix here irrelevant."""
    src = (REPO / "server" / "reportui.py").read_text()
    assert 'name="viewport"' in src and "width=device-width" in src
