"""Universal invariants: things that must be true of EVERY trade of EVERY strategy.

WHY THIS EXISTS. On 2026-08-25 two bugs shipped past 231 passing tests. An "iron fly"
whose short and long PE legs landed on the same strike -- a position that did not exist --
reported 100.6 % return on margin in a single trade. An iron condor produced credit, max
loss and margin of exactly zero, and the zero margin was then silently dropped from the
mean-return-on-margin denominator. Both were reachable from the DEFAULT parameters.

Neither was subtle. They survived because every test in the suite picked a handful of
specs by hand, and the bugs only appear where a specific spec meets a specific thin
minute in the data. No amount of care choosing example specs finds that; only breadth does.

So this module states what must be true of any trade, and a runner applies it across a
wide grid of specs. It is not a substitute for the unit tests -- those pin behaviour that
was reasoned about. This pins behaviour nobody thought to reason about.

EVERY INVARIANT NAMES WHAT IT WOULD HAVE CAUGHT, or why it is here. An invariant nobody
can motivate is one that gets deleted the first time it fails inconveniently.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import backtest, detail, metrics                 # noqa: E402
from engine.config import contracts                          # noqa: E402
from engine import backtest as _bt                            # noqa: E402,F401
from engine import spec as spec_mod                          # noqa: E402


class Violation(Exception):
    pass


def _fail(rule, detail_text):
    raise Violation(f"{rule}: {detail_text}")


# ------------------------------------------------------------------ per trade

def check_trade(spec, t):
    """Everything that must hold for one trade, whatever produced it."""
    where = f"{t.entry_ts:%Y-%m-%d %H:%M} exp {t.expiry}"

    # --- the two bugs of 2026-08-25 -------------------------------------------------
    for side in ("CE", "PE"):
        sells = {l["strike"] for l in t.legs
                 if l["option_type"] == side and l["action"] == "SELL"}
        buys = {l["strike"] for l in t.legs
                if l["option_type"] == side and l["action"] == "BUY"}
        if sells & buys:
            _fail("self_cancelling_legs",
                  f"{where}: {side} SELL and BUY both at {sorted(sells & buys)}. The pair "
                  f"contributes no premium, no risk and no margin, so what remains is a "
                  f"different structure from the one specified")

    # The bound has to come from what the SPEC ASKED FOR, not a constant. The first version
    # used a flat 25 strike steps and flagged pct_offset 5.0 -- which legitimately places a
    # leg 1,276 points out -- as a drifted ATM. An invariant that cannot tell a requested
    # distance from an accidental one is not checking anything.
    step = contracts.strike_step(t.expiry, spec.symbol)
    requested = (float(spec.params.get("pct_offset", 0.0))
                 + float(spec.params.get("pct_width", 0.0))) / 100.0
    # Slack of 4 strike steps, not 2. The engine rounds to the nearest LISTED strike, and
    # a sparse chain can put that several steps past the requested distance -- measured at
    # 30 points beyond an 8 % offset. That is correct rounding, not drift.
    # Slack of 10 strike steps. The engine rounds to the nearest LISTED strike, and far
    # out of the money on expiry day the chain is sparse enough that the nearest one with a
    # real print can be 9 steps past the target -- measured at an 8 % offset, where the leg
    # landed 2,337 points out against a 1,900-point request.
    #
    # LEFT AS SLACK RATHER THAN A GUARD, deliberately: unlike a drifted ATM, this does not
    # silently corrupt margin -- offset_steps is measured from atm, which is separately
    # bounded. It gives the caller a strike further out than asked for, which is documented
    # "nearest listed" behaviour. Whether it should also be bounded is an open product
    # question, not a correctness bug, and is noted rather than decided here.
    allowed = t.spot_entry * requested + backtest.MAX_ATM_DRIFT_STEPS * step + 10 * step
    nearest = min((l["strike"] for l in t.legs), key=lambda k: abs(k - t.spot_entry))
    furthest = max((l["strike"] for l in t.legs), key=lambda k: abs(k - t.spot_entry))
    # Only iron_fly has a leg that is SUPPOSED to sit at the money. Every other structure
    # places its nearest leg pct_offset away on purpose, so "nearest leg is far from spot"
    # is the requested behaviour there and asserting otherwise flagged 20 correct results.
    if spec.structure == "iron_fly":
        if abs(nearest - t.spot_entry) > backtest.MAX_ATM_DRIFT_STEPS * step + step:
            _fail("atm_leg_drifted_from_spot",
                  f"{where}: the short leg of an iron fly is {abs(nearest - t.spot_entry):.0f} "
                  f"points from spot {t.spot_entry:.0f}. It is meant to be AT the money — "
                  f"drift was measured at 33 strike steps, which turned a fly into a "
                  f"deep-ITM vertical")
    if abs(furthest - t.spot_entry) > allowed:
        _fail("leg_further_out_than_requested",
              f"{where}: leg {furthest} is {abs(furthest - t.spot_entry):.0f} points from "
              f"spot, more than the {requested:.2%} the spec asked for plus rounding")

    # --- margin and charges ---------------------------------------------------------
    if not t.margin_pts > 0:
        _fail("margin_not_positive",
              f"{where}: margin {t.margin_pts}. metrics.py drops margin<=0 from the "
              f"return-on-margin mean WITHOUT saying so, so one zero silently changes what "
              f"the headline ratio was averaged over")
    if not t.charges_rupees > 0:
        _fail("charges_not_positive",
              f"{where}: charges {t.charges_rupees}. Every round trip pays brokerage, and a "
              f"free trade is a cost model that stopped being applied")
    if not t.margin_basis:
        _fail("margin_basis_missing",
              f"{where}: return-on-margin shipped without saying whether its denominator "
              f"is exact or a today-calibrated approximation applied to the past")

    # --- arithmetic -----------------------------------------------------------------
    expected = t.entry_credit_pts + t.exit_value_pts - t.slippage_pts
    if abs(t.pnl_pts - expected) > 1e-6:
        _fail("pnl_does_not_reconcile",
              f"{where}: pnl {t.pnl_pts} != credit {t.entry_credit_pts} + exit "
              f"{t.exit_value_pts} - slippage {t.slippage_pts}")
    rupees = t.pnl_pts * t.lot_size - t.charges_rupees
    if abs(t.pnl_rupees - rupees) > 0.01:
        _fail("rupee_pnl_does_not_reconcile", f"{where}: {t.pnl_rupees} != {rupees}")
    if t.slippage_pts < 0:
        _fail("negative_slippage", f"{where}: slippage {t.slippage_pts} is a credit")

    # --- time -----------------------------------------------------------------------
    if t.exit_ts <= t.entry_ts:
        _fail("exit_before_entry", f"{where}: exit {t.exit_ts} <= entry {t.entry_ts}")
    if t.exit_ts.date() > t.expiry:
        _fail("exit_after_expiry",
              f"{where}: exit {t.exit_ts} is after the contract expired")
    if t.entry_date > t.expiry:
        _fail("entered_after_expiry", f"{where}: entered after expiry")
    if spec.exit_time is not None:
        if t.exit_ts.date() != t.entry_ts.date():
            _fail("timed_exit_survived_its_session",
                  f"{where}: exit_time {spec.exit_time} was set but the position closed on "
                  f"{t.exit_ts.date()}, a different day from entry")
        if t.exit_reason == "EXPIRY" and t.entry_date != t.expiry:
            _fail("timed_exit_reached_settlement",
                  f"{where}: a squared-off position settled at expiry")
        exit_m = t.exit_ts.hour * 60 + t.exit_ts.minute
        if exit_m > spec.exit_minute:
            _fail("exit_after_the_square_off",
                  f"{where}: exit at {t.exit_ts:%H:%M} is after exit_time "
                  f"{spec.exit_time}. A stop that fires after the position closed is "
                  f"lookahead")

    # --- legs -----------------------------------------------------------------------
    if len(t.legs) != spec.n_legs:
        _fail("wrong_leg_count",
              f"{where}: {len(t.legs)} legs, {spec.structure} has {spec.n_legs}")
    for l in t.legs:
        if l["premium_pts"] <= 0:
            _fail("leg_priced_at_zero",
                  f"{where}: {l['action']} {l['option_type']} {l['strike']} entered at "
                  f"{l['premium_pts']}. Entry pricing requires a real print")
        if l["strike"] % step:
            _fail("strike_off_the_grid",
                  f"{where}: strike {l['strike']} is not a multiple of {step:.0f}")

    # --- structure-specific ---------------------------------------------------------
    if spec.structure in ("iron_condor", "iron_fly", "credit_spread"):
        width = backtest._width_pts(t.legs)
        if width and t.margin_pts > width + 1e-6:
            _fail("defined_risk_margin_exceeds_width",
                  f"{where}: margin {t.margin_pts} > width {width}. A hedged combo cannot "
                  f"lose more than its width")
    if spec.structure == "long_option":
        if t.entry_credit_pts >= 0:
            _fail("debit_structure_took_a_credit",
                  f"{where}: long_option entered at credit {t.entry_credit_pts}")
        if abs(t.margin_pts + t.entry_credit_pts) > 1e-6:
            _fail("long_option_margin_is_not_the_premium",
                  f"{where}: margin {t.margin_pts} != premium paid {-t.entry_credit_pts}")
    elif spec.is_credit and t.entry_credit_pts < 0:
        # ZERO is allowed and is not a defect. A 0.25 %-wide spread on expiry day has both
        # legs printing at the tick floor, so the net credit is genuinely 0 -- real data,
        # and a fill a real trader would have got. A NEGATIVE credit would mean a credit
        # structure paid a net debit, which cannot happen if the legs are ordered right.
        _fail("credit_structure_paid_a_debit",
              f"{where}: {spec.structure} entered at {t.entry_credit_pts}, a net debit")


# ------------------------------------------------------------------ per result

def check_result(spec, result, summary, rich):
    """Invariants over the whole run, which per-trade checks cannot see."""
    n = len(result.trades)
    if summary["n_trades"] != n:
        _fail("trade_count_disagrees", f"summary {summary['n_trades']} vs {n} trades")

    if n:
        total = sum(t.pnl_rupees for t in result.trades)
        if abs(summary["total_pnl_rupees"] - total) > 0.02:
            _fail("total_pnl_disagrees",
                  f"summary {summary['total_pnl_rupees']} vs {total}")

        rows = rich["equity_curve"]["rows"]
        if len(rows) != n:
            _fail("curve_length_disagrees", f"{len(rows)} points for {n} trades")
        # Rounded to whole rupees per point, so the tolerance grows with the count.
        if abs(rows[-1][2] - summary["total_pnl_rupees"]) > n:
            _fail("curve_does_not_end_at_the_headline",
                  f"curve ends {rows[-1][2]} vs headline {summary['total_pnl_rupees']}")
        if min(r[3] for r in rows) - summary["max_drawdown_rupees"] > n:
            _fail("curve_drawdown_disagrees",
                  f"{min(r[3] for r in rows)} vs {summary['max_drawdown_rupees']}")

        # The ROM denominator must cover every trade, or the ratio describes a subset the
        # reader was never told about. This is the silent-drop bug, stated as a rule.
        counted = sum(1 for t in result.trades if t.margin_pts > 0)
        if counted != n:
            _fail("rom_silently_averaged_over_a_subset",
                  f"{counted} of {n} trades have positive margin")

        # A ratio below the floor is a ratio about a sample, not a strategy.
        if n < metrics.MIN_TRADES_FOR_RATIOS and summary.get("ratios") is not None:
            _fail("ratios_below_the_floor", f"ratios reported on {n} trades")

        # Anti-oracle floors are post-run checks; they must have actually been applied.
        if result.n_contracts < spec_mod.MIN_DISTINCT_CONTRACTS:
            _fail("anti_oracle_floor_not_enforced",
                  f"{result.n_contracts} distinct contracts")

    # Disclosure: a skipped cycle must be counted somewhere a reader will see it.
    if result.notes:
        for note in result.notes:
            if "skipped" in note and not any(ch.isdigit() for ch in note):
                _fail("undisclosed_skip", f"note gives no count: {note!r}")


def check_all(spec, result, summary, rich):
    for t in result.trades:
        check_trade(spec, t)
    check_result(spec, result, summary, rich)
