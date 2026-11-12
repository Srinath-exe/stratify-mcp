"""Admission control under a surge: slower answers, bounded queue, honest refusals.

The property that matters is not "it refuses" but WHERE it refuses. A service with no
global cap degrades into swap and timeouts; one that refuses instantly turns a half-second
overlap into an error a model reports as a failure. These tests pin both edges.
"""
import os
import sys
import tempfile
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def q(monkeypatch):
    db = os.path.join(tempfile.mkdtemp(), "s.sqlite")
    monkeypatch.setenv("STRATIFY_SERVICE_DB", db)
    monkeypatch.setenv("STRATIFY_KEY_PEPPER", "test-pepper")
    for m in [m for m in list(sys.modules) if m.startswith("server.")]:
        del sys.modules[m]
    from server import quota, store
    store.DB_PATH = __import__("pathlib").Path(db)
    return quota


def test_free_tier_has_a_global_cap(q):
    """Without one the ceiling is (accounts x per-account limit), which is not a ceiling."""
    assert q.TIER_GLOBAL_CONCURRENCY["free"] is not None
    assert q.TIER_GLOBAL_CONCURRENCY["free"] > 0


def test_global_cap_refuses_beyond_the_limit(q, monkeypatch):
    monkeypatch.setattr(q, "TIER_GLOBAL_CONCURRENCY", {"free": 2})
    monkeypatch.setattr(q, "ADMISSION_WAIT_SECONDS", 0)      # measure the cap, not the wait
    held = [q.CONCURRENCY.acquire(f"acct{i}", 5, tier="free") for i in range(2)]
    with pytest.raises(q.QuotaExceeded) as e:
        q.CONCURRENCY.acquire("acct9", 5, tier="free")
    assert e.value.limit == "tier_concurrency"
    assert e.value.retry_after_seconds > 0        # a 429 with no Retry-After is a retry loop
    for t in held:
        q.CONCURRENCY.release(t)
    q.CONCURRENCY.release(q.CONCURRENCY.acquire("acct9", 5, tier="free"))


def test_a_burst_waits_instead_of_erroring(q, monkeypatch):
    """A slot freed mid-wait must be taken, not refused -- that is the whole point."""
    monkeypatch.setattr(q, "TIER_GLOBAL_CONCURRENCY", {"free": 1})
    monkeypatch.setattr(q, "ADMISSION_WAIT_SECONDS", 2.0)
    first = q.CONCURRENCY.acquire("acctA", 5, tier="free")
    threading.Timer(0.4, lambda: q.CONCURRENCY.release(first)).start()
    t0 = time.time()
    second = q.CONCURRENCY.acquire("acctB", 5, tier="free")   # must NOT raise
    waited = time.time() - t0
    q.CONCURRENCY.release(second)
    assert 0.3 < waited < 2.0, f"expected a short wait, waited {waited:.2f}s"


def test_the_wait_is_bounded_and_then_refuses(q, monkeypatch):
    monkeypatch.setattr(q, "TIER_GLOBAL_CONCURRENCY", {"free": 1})
    monkeypatch.setattr(q, "ADMISSION_WAIT_SECONDS", 0.5)
    held = q.CONCURRENCY.acquire("acctA", 5, tier="free")
    t0 = time.time()
    with pytest.raises(q.QuotaExceeded):
        q.CONCURRENCY.acquire("acctB", 5, tier="free")
    waited = time.time() - t0
    q.CONCURRENCY.release(held)
    assert waited < 1.5, f"wait was not bounded: {waited:.2f}s"


def test_an_account_refusal_does_not_wait(q, monkeypatch):
    """The caller is its own bottleneck; holding a worker thread for it helps nobody."""
    monkeypatch.setattr(q, "TIER_GLOBAL_CONCURRENCY", {"free": 99})
    monkeypatch.setattr(q, "ADMISSION_WAIT_SECONDS", 3.0)
    held = q.CONCURRENCY.acquire("acctA", 1, tier="free")
    t0 = time.time()
    with pytest.raises(q.QuotaExceeded) as e:
        q.CONCURRENCY.acquire("acctA", 1, tier="free")
    waited = time.time() - t0
    q.CONCURRENCY.release(held)
    assert e.value.limit == "concurrency"
    assert waited < 0.5, f"account refusal should be immediate, took {waited:.2f}s"


def test_paid_stays_capped_below_free(q):
    """Heavy paid work must never be able to starve the free tier: measured, four paid
    backtests in flight cost free traffic 28 % of its throughput."""
    assert q.TIER_GLOBAL_CONCURRENCY["pro"] < q.TIER_GLOBAL_CONCURRENCY["free"]
