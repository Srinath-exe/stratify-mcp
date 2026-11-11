"""The chain this service serves, and the one it does not."""
from engine.config import contracts


def test_only_the_standard_strike_grid_is_served():
    """NIFTY's listed options are not all one instrument.

    The weekly and monthly chain trades a 50-point strike grid; the long-dated series
    trades 500 and 1000-point grids and is a different product, measured from a fraction
    of the observations. Two of those expiries fell inside the served window with a lot
    size of 50 between the correct 75 and 65 regimes -- so coverage published three
    overlapping lot sizes, and any cycle selecting one would have been sized a third
    wrong in rupees.
    """
    table = contracts._spec_table("NIFTY")
    assert table, "no contract spec loaded"
    steps = {r["strike_step"] for r in table.values()}
    assert steps == {contracts.standard_strike_step("NIFTY")}
    assert steps == {50}


def test_the_lot_size_regimes_in_the_served_window_are_the_two_real_ones():
    import datetime as dt

    table = contracts._spec_table("NIFTY")
    window = {d: r for d, r in table.items()
              if dt.date(2025, 7, 1) <= d <= dt.date(2026, 6, 30)}
    assert {r["lot_size"] for r in window.values()} == {75, 65}
    # And they do not overlap: 75 ends before 65 begins.
    last75 = max(d for d, r in window.items() if r["lot_size"] == 75)
    first65 = min(d for d, r in window.items() if r["lot_size"] == 65)
    assert last75 < first65
