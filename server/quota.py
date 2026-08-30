"""Rate limiting and quotas — the only thing standing between a self-serve key and the box.

Access is deliberately unguarded in every other respect: public signup, self-serve key, no
approval and no manual review. That makes these limits load-bearing rather than a
formality, so they are enforced on THREE independent dimensions, because any single one
can be gamed:

  * requests per hour   — stops a naive loop
  * CPU-seconds per hour — stops a small number of very expensive queries
  * concurrency          — stops a burst from monopolising the box regardless of the above

Free tier (decision D2): 100 backtests/hour, 60 CPU-seconds/hour, 2 concurrent. At the
measured ~1.5 CPU-seconds for a full-year backtest, 60 CPU-seconds is roughly 40 heavy
runs or many hundreds of cheap ones — generous for real use, tight against abuse.

METERED PER ACCOUNT, NOT PER KEY. Metering per key made the whole system decorative:
signup was unthrottled, so anyone could mint a fresh key with a fresh allowance in a loop.
Keys are for rotation and revocation; the ACCOUNT is the unit that gets an allowance, and
signup itself is throttled per source address below.

Every rejection says which limit was hit and when it clears. A 429 that does not tell you
when to come back just produces a tighter retry loop.
"""
import os
import threading
import time
from dataclasses import dataclass

from . import store

WINDOW_SECONDS = 3600


@dataclass(frozen=True)
class Tier:
    name: str
    requests_per_hour: int
    cpu_seconds_per_hour: float
    max_concurrent: int
    # A FOURTH dimension, added when results started carrying real option prints.
    # Sized against what the surface can even reach: 232,172 contract-days x 9 precomputed
    # clock times = 2.09 M price points, and only the ones a strategy actually trades. At
    # 20,000 an hour, touching 1 % of that reachable surface takes a day of uninterrupted
    # maximum-rate requests assuming zero overlap -- and overlap is near-total in practice,
    # because strike selection follows spot. CPU binds first for any realistic caller; this
    # is the limit that binds a caller whose purpose is extraction rather than research.
    price_points_per_hour: int = 20_000


TIERS = {
    "free":  Tier("free", 100, 60.0, 2, 20_000),
    "plus":  Tier("plus", 1000, 900.0, 6, 400_000),
    "pro":   Tier("pro", 10000, 7200.0, 16, 4_000_000),
    # Capacity testing only. Never issued to a real account -- it exists so a load test
    # measures the machine rather than the rate limiter, which is otherwise the first
    # thing it hits.
    "bench": Tier("bench", 10_000_000, 1e9, 512, 10**12),
}


# Signup throttle. Without this the per-account quota is decorative, because a new
# account is a new allowance. These are deliberately generous for a human (nobody signs up
# three times in an hour by accident) and ruinous for a loop.
# How many requests of each tier may be in flight across the WHOLE service. Sized against
# the worker count: heavy paid work is allowed at most a third of the workers, so free
# traffic always has somewhere to land. Without this cap, four concurrent paid backtests
# cost the free tier 28 % of its throughput.
# Tunable, because the right number depends on worker count and on how heavy the paid
# workload actually is -- both of which are deployment facts, not constants.
_PAID_CAP = int(os.getenv("STRATIFY_PAID_GLOBAL_CONCURRENCY", "2"))
TIER_GLOBAL_CONCURRENCY = {"free": None, "plus": _PAID_CAP, "pro": _PAID_CAP,
                           "bench": None}

SIGNUPS_PER_IP_PER_HOUR = 3
SIGNUPS_PER_IP_PER_DAY = 10
MAX_ACTIVE_KEYS_PER_ACCOUNT = 5


class QuotaExceeded(Exception):
    def __init__(self, limit, message, retry_after_seconds):
        super().__init__(message)
        self.limit = limit
        self.message = message
        self.retry_after_seconds = retry_after_seconds


class Concurrency:
    """Cross-process concurrency counter, backed by the service database.

    It was in-process, which was correct only at one worker -- and one worker caps the
    service at a single core. Measured on this box a request costs 0.026 CPU-seconds, so
    one process ceilings at roughly 38 requests per second while seven cores sit idle.
    Moving the counter into SQLite is what makes `--workers N` safe: without it, N workers
    would each hand out the full concurrency limit independently.
    """

    def acquire(self, account_id, limit, tier="free"):
        token = f"{os.getpid()}:{threading.get_ident()}:{time.time():.6f}"
        refused = store.acquire_slot(account_id, limit, token, tier=tier,
                                     tier_limit=TIER_GLOBAL_CONCURRENCY.get(tier))
        if refused == "account":
            raise QuotaExceeded(
                "concurrency",
                f"{store.active_slots(account_id)} requests are already running on this "
                f"account; the limit is {limit}. Wait for one to finish rather than "
                f"retrying immediately.",
                retry_after_seconds=5)
        if refused == "tier":
            raise QuotaExceeded(
                "tier_concurrency",
                f"the service is already running the maximum "
                f"{TIER_GLOBAL_CONCURRENCY.get(tier)} concurrent {tier}-tier backtests. "
                f"These are heavy queries and are capped so they cannot starve everyone "
                f"else; retry shortly.",
                retry_after_seconds=3)
        return token

    def release(self, token):
        if token:
            store.release_slot(token)

    def active(self, account_id):
        return store.active_slots(account_id)


CONCURRENCY = Concurrency()


def tier_for(row):
    return TIERS.get(row["tier"], TIERS["free"])


def check_signup(ip, now=None):
    """Throttle account creation per source address. Raises QuotaExceeded."""
    now = now if now is not None else time.time()
    ip_hash = store.hash_ip(ip or "unknown")
    hourly = store.signups_since(ip_hash, now - 3600)
    if hourly >= SIGNUPS_PER_IP_PER_HOUR:
        raise QuotaExceeded(
            "signups_per_hour",
            f"{hourly} accounts have been created from this address in the last hour. "
            f"If you need another key for an existing account, use POST /v1/keys.",
            retry_after_seconds=3600)
    daily = store.signups_since(ip_hash, now - 86400)
    if daily >= SIGNUPS_PER_IP_PER_DAY:
        raise QuotaExceeded(
            "signups_per_day",
            f"{daily} accounts have been created from this address today.",
            retry_after_seconds=86400)
    return ip_hash


def check_key_issuance(account_id):
    n = store.active_key_count(account_id)
    if n >= MAX_ACTIVE_KEYS_PER_ACCOUNT:
        raise QuotaExceeded(
            "keys_per_account",
            f"this account already has {n} active keys, the maximum is "
            f"{MAX_ACTIVE_KEYS_PER_ACCOUNT}. Revoke one before issuing another.",
            retry_after_seconds=0)


def snapshot(account_id, tier, now=None):
    """Usage readout that NEVER raises.

    `check` raises by design, and it was also being used for the after-the-call readout --
    so a request that consumed the last unit of its own quota raised on the way out,
    after the work was already done, and took the worker process down with it. Reporting
    usage and enforcing a limit are different jobs and now have different functions.
    """
    now = now if now is not None else time.time()
    n_requests, cpu, pts = store.usage_since(account_id, now - WINDOW_SECONDS)
    return {"requests_used": n_requests, "requests_limit": tier.requests_per_hour,
            "cpu_seconds_used": round(cpu, 3),
            "cpu_seconds_limit": tier.cpu_seconds_per_hour,
            "price_points_used": int(pts),
            "price_points_limit": tier.price_points_per_hour,
            "concurrent_active": CONCURRENCY.active(account_id),
            "concurrent_limit": tier.max_concurrent,
            "metered_on": "account",
            "exhausted": n_requests >= tier.requests_per_hour
                         or cpu >= tier.cpu_seconds_per_hour
                         or pts >= tier.price_points_per_hour}


def check(account_id, tier, key_id=None, now=None):
    """Raises QuotaExceeded, or returns the current usage snapshot."""
    now = now if now is not None else time.time()
    n_requests, cpu, pts = store.usage_since(account_id, now - WINDOW_SECONDS)
    if n_requests >= tier.requests_per_hour:
        raise QuotaExceeded(
            "requests_per_hour",
            f"{n_requests} of {tier.requests_per_hour} backtests used in the last hour "
            f"on the {tier.name} tier.",
            retry_after_seconds=_retry_after(account_id, now))
    if cpu >= tier.cpu_seconds_per_hour:
        raise QuotaExceeded(
            "cpu_seconds_per_hour",
            f"{cpu:.1f} of {tier.cpu_seconds_per_hour:.0f} CPU-seconds used in the last "
            f"hour on the {tier.name} tier. Narrower periods and fixed entry times cost "
            f"far less.",
            retry_after_seconds=_retry_after(account_id, now))
    if pts >= tier.price_points_per_hour:
        raise QuotaExceeded(
            "price_points_per_hour",
            f"{int(pts)} of {tier.price_points_per_hour} option prices returned in the "
            f"last hour on the {tier.name} tier. Results carry the prices of the contracts "
            f"a strategy actually traded, and that release is metered. "
            f"detail='summary' returns aggregates and costs nothing against this limit; "
            f"report_url shows every trade without spending it either.",
            retry_after_seconds=_retry_after(account_id, now))
    return {"requests_used": n_requests, "requests_limit": tier.requests_per_hour,
            "cpu_seconds_used": round(cpu, 3),
            "cpu_seconds_limit": tier.cpu_seconds_per_hour,
            "price_points_used": int(pts),
            "price_points_limit": tier.price_points_per_hour,
            "concurrent_active": CONCURRENCY.active(account_id),
            "concurrent_limit": tier.max_concurrent,
            "metered_on": "account"}


def _retry_after(account_id, now):
    """Seconds until the oldest call in the window ages out — the real answer, not a
    constant. Callers that are told the truth stop hammering."""
    t = store.oldest_usage(account_id, now - WINDOW_SECONDS)
    if t is None:
        return WINDOW_SECONDS
    return max(1, int(t + WINDOW_SECONDS - now))
