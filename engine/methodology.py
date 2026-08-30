"""What produced a number, and when that changed.

THE PROBLEM. A backtest is reproducible only against a fixed methodology. Improve the
cost model, tighten a fill, correct a margin floor, and the same spec returns a different
answer -- correctly, but silently. Every stored result then shows figures this engine
would no longer produce, and nothing on the page says so.

That has already happened here. On 2026-08-25 stop-loss and target resolution moved from
the daily close to the 1-minute bar the live system actually watches. It was the right
fix. It cut measured P&L by 9.8 % and pushed five strategies below the bar they had been
selected on -- and every result recorded before it became quietly unreproducible.

So results carry a stamp. `VERSION` is bumped when, and only when, a change could alter
the numbers a given spec produces. Not for a new tool, not for a faster query, not for a
better-worded refusal. The test suite asserts the distinction stays real.

TWO THINGS IN THE STAMP ARE NOT THE VERSION, and that is deliberate. Slippage switches
from an assumed half-spread to one measured from live fills the moment the paper-trading
book holds 30 of them, and naked margin uses a SPAN ratio calibrated on a specific date.
Both change the answer without anybody editing code. A version integer alone would call
two different computations the same thing, so the stamp records the BASIS as well.

WHAT THE STAMP IS FOR:
  - `spec_hash` includes the version, so a re-run under new methodology becomes a NEW
    strategy-book entry instead of silently overwriting the old one's statistics.
  - `results` stores it, so a report can say which methodology produced it.
  - `describe_coverage` publishes it, so a caller can notice it moved.
  - `explain_methodology("changelog")` says what changed and whether it moved numbers.
"""
from .config import margin, slippage

# Bump ONLY when output could change for an unchanged spec. See CHANGELOG below, and the
# test in engine/tests/test_methodology.py that keeps this honest.
VERSION = 1

# Newest first. `affects_results` is the whole point of the entry: True means an unchanged
# spec can return different numbers across this boundary.
#
# Entries with `version: None` pre-date the stamp. They are reconstructed from the commit
# history and the decision record, and are here because a user comparing an old screenshot
# to a fresh run deserves to find the reason rather than assume one of them is broken.
CHANGELOG = [
    {
        "version": 1,
        "date": "2026-08-30",
        "affects_results": False,
        "summary": "Methodology versioning introduced. No computation changed.",
        "changes": [
            "Results, and the strategy book, now record the methodology that produced "
            "them. Before this, a stored figure could not be attributed to a model.",
            "spec_hash now includes the methodology version, so re-running a spec after "
            "a methodology change adds a book entry rather than overwriting one.",
        ],
    },
    {
        "version": None,
        "date": "2026-08-29",
        "affects_results": True,
        "summary": "Two margin faults that flattered return-on-margin.",
        "changes": [
            "ATM selection could drift up to 33 strikes from spot before refusing, so a "
            "structure could be priced off contracts nowhere near the ones requested.",
            "Naked-short margin ignored quantity: a hundred short calls blocked the same "
            "capital as one. Return-on-margin for size-varying naked positions was "
            "overstated by up to 100x.",
            "The offsetting-legs guard discarded legitimate ratio spreads. It now refuses "
            "only contracts that net to exactly zero.",
        ],
    },
    {
        "version": None,
        "date": "2026-08-25",
        "affects_results": True,
        "summary": "Stop-loss and target resolution moved to the 1-minute bar.",
        "changes": [
            "Exits were resolved against the daily close while the live system watches "
            "every 15 seconds, so a stop that fired intraday was missed. Measured effect: "
            "P&L fell 9.8 % and five strategies dropped below their selection bar.",
            "Defined-risk margin gained the MIN_MARGIN_PCT_OF_NOTIONAL floor already used "
            "by the research pipeline and paper trading. A live SPAN check found real "
            "broker margin runs 16-17x the theoretical max loss for a tight combo, so "
            "iron condors, credit spreads and iron flies had been undermargined.",
            "Two chart resolutions (5-minute and 15-minute) had never executed at all: a "
            "literal % in a parameterised query raised before the SQL was sent.",
        ],
    },
]


def stamp():
    """The methodology fingerprint to attach to a result.

    `version` alone is not enough -- see this module's docstring. Both extra fields are
    read live, so a result records the basis that was actually in force when it ran.
    """
    try:
        _, note = slippage.measured_half_spread_pts()
    except Exception:                       # a missing paper book is the documented case
        note = "assumed (no measured book available)"
    try:
        calibrated = margin.calibrated_at()
    except Exception:
        calibrated = None
    return {"version": VERSION, "slippage_basis": note, "margin_calibrated_at": calibrated}


def current():
    """The changelog entry for the version in force."""
    return next((e for e in CHANGELOG if e["version"] == VERSION), None)


def since(version):
    """Entries newer than `version`, newest first.

    A client that stored `version` alongside a result can ask what has moved since, and
    tell the difference between "we improved the wording" and "your number changed".
    """
    if version is None:
        return list(CHANGELOG)
    return [e for e in CHANGELOG
            if e["version"] is not None and e["version"] > version]


def results_changed_since(version):
    """True if any bump after `version` could alter an unchanged spec's numbers."""
    return any(e["affects_results"] for e in since(version))
