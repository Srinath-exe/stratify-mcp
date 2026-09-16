"""The public pages and the waitlist.

The pages are read by strangers deciding whether to sign up, so what they can say is
bounded by what the engine can do, and the waitlist is a free unauthenticated write, so
what it can be made to do is bounded by a rate limit.
"""
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server import app as app_mod, quota, site, store  # noqa: E402
from engine import honesty, strategy  # noqa: E402


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


# ---------------------------------------------------------------- pages

def test_every_public_page_renders_without_a_session(client):
    for path in ("/", "/explore", "/pricing", "/docs", "/privacy", "/terms", "/contact"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert "Stratify" in r.text


def test_the_landing_page_puts_the_button_before_the_fold(client):
    """For somebody whose only software is a chat box, the page must answer "what do I
    press" before it answers "what is it made of"."""
    html = client.get("/").text
    steps = html.index('class="steps"')
    exchange = html.index('class="exchange"')
    assert steps < exchange, "the three steps must come before the feature material"
    assert "STEP 1" in html and "STEP 2" in html and "STEP 3" in html


def test_the_explore_page_cannot_promise_what_the_engine_refuses():
    """Every capability named on the page must exist in the engine's own vocabulary.
    The page is prose, so this checks the vocabulary the prose was written FROM rather
    than parsing English -- but the lists that drive the prose are asserted here."""
    text = " ".join(t + " " + b for g in site.EXPLORE["groups"] for t, b in g["items"])
    text = text.lower()
    # Strike selection methods the page advertises exist as STRIKE_KEYS.
    for word, key in (("percent", "pct_offset"), ("points", "points_offset"),
                      ("delta", "delta_near"), ("premium", "premium_near"),
                      ("money", "atm"), ("another leg", "from_leg")):
        assert word in text and key in strategy.STRIKE_KEYS, key
    # Position actions.
    for word, key in (("roll", "roll"), ("close", "close"), ("re-open", "close_and_open")):
        assert word in text and key in strategy.ACTIONS, key
    # Market conditions.
    assert "vix" in text and "vix" in strategy.MARKET_FIELDS
    assert "gap" in text and "gap_pct" in strategy.MARKET_FIELDS
    assert "day of the week" in text and "day_of_week" in strategy.MARKET_FIELDS
    # Book-level rules.
    for word, key in (("three losers", "stop_after_losses"),
                      ("drawdown", "stop_after_drawdown_pct"),
                      ("skip", "skip_after_loss"),
                      ("resume", "resume_after_days")):
        assert word in text and key in strategy.PORTFOLIO_KEYS, key


def test_pricing_names_no_price_for_premium(client):
    """There is no price. Inventing one -- even "from ₹999" -- would be a promise the
    business cannot yet keep, on the page people screenshot."""
    html = client.get("/pricing").text
    prem = html[html.index('class="card plan soon"'):]
    assert "Not priced yet" in prem
    assert "₹" not in prem.split("waitlist")[0].replace("₹0", ""), "a rupee figure on Premium"


def test_pricing_free_tier_numbers_come_from_the_quota_module(client):
    html = client.get("/pricing").text
    assert f"{quota.TIERS['free'].requests_per_hour} backtests an hour" in html


# ---------------------------------------------------------------- waitlist

def test_waitlist_is_idempotent(client):
    a = client.post("/v1/waitlist", json={"email": "Person@Example.com"}).json()
    b = client.post("/v1/waitlist", json={"email": "person@example.com"}).json()
    assert a["created"] is True and b["created"] is False
    assert b["ok"] is True, "a repeat is confirmation of interest, not an error"
    assert store.waitlist_count() == 1


def test_signed_in_user_joins_with_their_own_address_and_no_form(client):
    account_id, _ = store.create_account("member@example.com")
    client.cookies.set("stratify_session", store.session_token(account_id))
    r = client.post("/waitlist", data={})
    assert r.status_code == 200
    assert store.on_waitlist("member@example.com")
    rows = store.waitlist_rows()
    assert rows[0]["account_id"] == account_id, "the account must be attached"
    # And the page now says so, with no button to press again.
    page = client.get("/pricing").text
    assert "on the list" in page and "Join the waitlist" not in page


def test_anonymous_visitor_needs_a_valid_email(client):
    assert client.post("/waitlist", data={"email": "nope"}).status_code == 400
    assert client.post("/waitlist", data={}).status_code == 400
    assert client.post("/waitlist", data={"email": "ok@example.com"}).status_code == 200


def test_waitlist_is_rate_limited_per_address(client, monkeypatch):
    """A free unauthenticated write, so one curl loop could fill the table."""
    monkeypatch.setattr(quota, "WAITLIST_PER_IP_PER_HOUR", 3)
    codes = [client.post("/v1/waitlist", json={"email": f"p{i}@example.com"}).status_code
             for i in range(5)]
    assert codes[:3] == [200, 200, 200]
    assert codes[-1] == 429


def test_waitlist_email_is_escaped_on_the_page(client):
    """The address is echoed back on the pricing page for a signed-in member."""
    account_id, _ = store.create_account('"><script>alert(1)</script>@x.com')
    client.cookies.set("stratify_session", store.session_token(account_id))
    page = client.get("/pricing").text
    assert "<script>alert(1)</script>" not in page


def test_every_page_hands_out_the_pinned_mcp_url_not_the_web_host(client, monkeypatch):
    """The web host answers on /mcp too, which is exactly why this bug survived: the wrong
    URL worked. It breaks OAuth discovery -- the protected-resource metadata names the MCP
    host as `resource` and a client rejects the mismatch -- so what is printed on the
    dashboard and in the docs must be the pinned protocol host."""
    monkeypatch.setenv("STRATIFY_PUBLIC_MCP_URL", "https://mcp.example.test/mcp")
    account_id, _ = store.create_account("url@example.com")
    client.cookies.set("stratify_session", store.session_token(account_id))
    # The landing page prints no URL at all on purpose -- it is for people who will paste
    # into a chat app and never see one -- so it is checked only for the wrong host.
    for path in ("/app", "/docs"):
        html = client.get(path).text
        assert "https://mcp.example.test/mcp" in html, path
    for path in ("/app", "/docs", "/", "/explore"):
        assert "https://testserver/mcp" not in client.get(path).text, (
            f"{path} still derives the MCP URL from the web host")


def test_the_explore_page_indicator_claim_matches_the_engine():
    """The page says 2 to 250 days; the parser had better agree."""
    text = " ".join(b for g in site.EXPLORE["groups"] for _, b in g["items"])
    assert f"{strategy.INDICATOR_MIN} to {strategy.INDICATOR_MAX} days" in text
    assert "yesterday" in text.lower(), "the look-ahead boundary must be stated"


def test_the_rules_card_reads_an_indicator_gate_as_english():
    from server import rulecard
    card = rulecard.describe({
        "legs": [{"side": "sell", "type": "CE", "strike": {"pct_offset": 1.0}}],
        "entry": {"cadence": "weekly", "dte": 3, "time": "09:30",
                  "when": {"all": [{"rsi_14": {"lt": 30}},
                                   {"ema_9_vs_21_pct": {"gt": 0}}]}}})
    entry = " ".join(i["text"] for i in card["entry"]).lower()
    assert "14-day rsi" in entry and "falls below 30" in entry
    assert "9-day ema against the 21-day" in entry
    assert "rsi_14" not in entry, "raw field names must not leak into the prose"


def test_mark_is_served_in_every_directory_size(client):
    """Each directory asks for a different size: ChatGPT a 64px PNG under 5 KB, Claude an
    icon URL or SVG, browsers a favicon. All must be first-party and cached a year."""
    for name, ctype in (("logo.png", "image/png"), ("icon-64.png", "image/png"),
                        ("icon-192.png", "image/png"), ("icon-512.png", "image/png"),
                        ("icon-1024.png", "image/png"), ("icon-512-dark.png", "image/png")):
        r = client.get(f"/static/{name}")
        assert r.status_code == 200, name
        assert r.headers["content-type"].startswith(ctype)
        assert "immutable" in r.headers["cache-control"]
    assert len(client.get("/static/icon-64.png").content) < 5000
    assert client.get("/favicon.ico").status_code == 200
    assert client.get("/static/../app.py").status_code in (404, 400)
    assert client.get("/static/app.py").status_code == 404
    home = client.get("/").text
    assert 'rel="icon" href="/favicon.ico"' in home and 'src="/static/icon-64.png"' in home


def test_contact_page_names_the_maintainer_with_every_channel(client):
    t = client.get("/contact").text
    for needle in ("mailto:1406srinath@gmail.com", "tel:+919025723158",
                   "https://linkedin.com/in/srinath-exe", "https://github.com/Srinath-exe",
                   "https://x.com/Srinath_exe"):
        assert needle in t, needle
    assert 'rel="me noopener" target="_blank"' in t
