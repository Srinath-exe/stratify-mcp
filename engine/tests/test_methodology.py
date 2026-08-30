"""The version stamp is only worth anything if it is maintained.

A stamp nobody bumps is worse than no stamp: it asserts that nothing changed while the
numbers move underneath it. These tests cannot know whether a given commit changed the
maths -- nothing can -- so they guard the things that CAN be checked mechanically: that
the changelog stays well-formed, that the version is actually reachable in it, and that
the version really does reach the places that depend on it.
"""
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PKG))

from engine import methodology                                    # noqa: E402


def test_the_current_version_has_a_changelog_entry():
    """A bump with no entry is the failure this whole mechanism exists to prevent: the
    number moves, the stamp moves, and nothing anywhere says what happened."""
    entry = methodology.current()
    assert entry is not None, f"VERSION is {methodology.VERSION} with no CHANGELOG entry"
    assert entry["summary"], "the current entry has no summary"


def test_versions_are_unique_and_descend():
    """Newest first, and no version reused. A duplicate would make `since()` ambiguous
    about which model a stored result came from."""
    versions = [e["version"] for e in methodology.CHANGELOG if e["version"] is not None]
    assert versions == sorted(set(versions), reverse=True), versions
    assert methodology.VERSION == max(versions), "VERSION is not the newest entry"


@pytest.mark.parametrize("entry", methodology.CHANGELOG,
                         ids=[str(e["version"]) for e in methodology.CHANGELOG])
def test_every_entry_is_well_formed(entry):
    assert set(entry) == {"version", "date", "affects_results", "summary", "changes"}
    assert isinstance(entry["affects_results"], bool), \
        "affects_results must be a real bool -- it is the field a client branches on"
    assert entry["changes"], "an entry with no changes explains nothing"
    assert all(isinstance(c, str) and c.strip() for c in entry["changes"])


def test_the_stamp_carries_the_two_things_that_move_without_a_code_change():
    """Slippage flips from assumed to measured at 30 live fills, and naked margin uses a
    SPAN ratio calibrated on a date. Either can change a result with nobody editing code,
    so a version integer alone would call two different computations the same thing."""
    stamp = methodology.stamp()
    assert stamp["version"] == methodology.VERSION
    assert "slippage_basis" in stamp and stamp["slippage_basis"]
    assert "margin_calibrated_at" in stamp


def test_the_stamp_survives_a_missing_paper_book():
    """The paper-trading book is an optional runtime input. A deployment without one must
    still produce a stamp, not raise -- see vendor/README.md."""
    from engine.config import slippage

    def boom(*a, **k):
        raise FileNotFoundError("no paper book here")

    original = slippage.measured_half_spread_pts
    slippage.measured_half_spread_pts = boom
    try:
        assert methodology.stamp()["slippage_basis"]
    finally:
        slippage.measured_half_spread_pts = original


def test_since_separates_a_cosmetic_bump_from_one_that_moves_numbers():
    """This is the question a client actually asks: my figure does not reproduce -- is
    that because you changed the maths, or because I made a mistake?"""
    cosmetic = [{"version": 3, "date": "d", "affects_results": False, "summary": "s",
                 "changes": ["c"]}]
    material = [{"version": 3, "date": "d", "affects_results": True, "summary": "s",
                 "changes": ["c"]}]
    original = methodology.CHANGELOG
    try:
        methodology.CHANGELOG = cosmetic
        assert methodology.results_changed_since(2) is False
        methodology.CHANGELOG = material
        assert methodology.results_changed_since(2) is True
        assert methodology.results_changed_since(3) is False, "a version is not after itself"
    finally:
        methodology.CHANGELOG = original


def test_the_version_is_inside_the_spec_hash():
    """THE REASON THE HASH EXISTS. The strategy book is keyed on (account, spec_hash), so
    without the version a re-run under a new methodology would overwrite the old row's
    statistics in place -- the user's recorded Sharpe would change with no event to point
    at. With it, the two runs are two entries.
    """
    from engine import spec as spec_mod
    from server import tools

    parsed = spec_mod.parse({"structure": "iron_condor",
                             "params": {"pct_offset": 1.0, "pct_width": 1.0, "entry_dte": 2},
                             "entry_time": "09:30"})
    before = tools._spec_hash(parsed)
    original = methodology.VERSION
    try:
        methodology.VERSION = original + 1
        assert tools._spec_hash(parsed) != before, \
            "spec_hash ignored the methodology version; a book row would be overwritten"
    finally:
        methodology.VERSION = original
    assert tools._spec_hash(parsed) == before, "hash is not stable for a fixed version"
