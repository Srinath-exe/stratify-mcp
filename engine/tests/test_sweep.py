"""A subset of the invariant sweep, small enough to run on every push.

The full sweep (tests/sweep.py) is a job: minutes of wall clock and hundreds of specs. This
takes seconds and covers every structure at both cadences, so the shape of bug it exists
for -- one reachable from ordinary parameters that no hand-written example happened to hit
-- cannot come back silently between nightly runs.

The invariants themselves live in tests/invariants.py so this and the full sweep check
exactly the same rules. A rule that held here and not there would be worse than no rule.
"""
import sys
from pathlib import Path

import pytest

# The directory holding engine/ -- i.e. this checkout, wherever it is cloned.
# Was `parents[3] / "stratify_mcp"`, which resolved to a SIBLING directory of that
# name and silently tested a different copy of the code from any other layout.
PKG = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PKG))

from engine import backtest, detail, metrics, spec as spec_mod   # noqa: E402
from tests import invariants, sweep                              # noqa: E402


@pytest.mark.parametrize("raw", sweep.build_specs(quick=True),
                         ids=lambda r: f"{r['structure']}-{r.get('cadence', 'weekly')}"
                                       f"-{r.get('exit_time', 'hold')}")
def test_invariants_hold(raw):
    try:
        spec = spec_mod.parse(raw)
        result = backtest.run(spec)
    except spec_mod.SpecError:
        pytest.skip("refused by the validator, which is a documented outcome")
    if not result.trades:
        pytest.skip("no trades")
    invariants.check_all(spec, result, metrics.summarise(result), detail.build(result))


def test_the_sweep_actually_covers_every_structure():
    """A grid that quietly stopped generating a structure would pass silently forever."""
    covered = {s["structure"] for s in sweep.build_specs(quick=True)}
    assert covered == set(spec_mod.STRUCTURE_PARAMS)


def test_the_sweep_covers_both_cadences_and_a_clock_exit():
    specs = sweep.build_specs(quick=True)
    assert {s.get("cadence", "weekly") for s in specs} == {"weekly", "daily"}
    assert any(s.get("exit_time") for s in specs)
