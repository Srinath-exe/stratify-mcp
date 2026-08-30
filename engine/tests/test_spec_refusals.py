"""Spec-layer refusals: an unsupported request must be REFUSED with a reason, never
crash. Both cases here were found by a capability probe -- a battery of deliberately
exotic strategy asks run against the parser -- and both reached the caller as a raw
traceback."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engine import spec as spec_mod  # noqa: E402


# ---------------------------------------------------------------- malformed values
# Both of these reached the caller as a raw traceback rather than a sentence. Found by a
# capability probe that asked for things the spec cannot express -- the point being that
# an unsupported request must be REFUSED with a reason, never crash.

def test_a_parameter_that_is_not_a_number_is_refused_not_crashed():
    """`float("1.0 + iv")` is a ValueError, not a SpecError. A user writing an expression
    where a fixed value belongs got a traceback instead of being told that parameters
    cannot depend on the market."""
    for bad in ("1.0 + iv", [1.0, 1.5], {"if_vol_gt": 20}, True):
        with pytest.raises(spec_mod.SpecError) as e:
            spec_mod.parse({"structure": "short_strangle", "params": {"pct_offset": bad}},
                       tier="pro")
        assert "must be a number" in str(e.value)


def test_a_required_parameter_given_as_null_is_refused_not_crashed():
    with pytest.raises(spec_mod.SpecError) as e:
        spec_mod.parse({"structure": "short_strangle", "params": {"pct_offset": None}},
                   tier="pro")
    assert "given as null" in str(e.value)
