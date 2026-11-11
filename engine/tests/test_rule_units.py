"""The units bug, the silent rule, and the unlabelled exit.

All three are the same failure wearing different clothes: the backtest completed, the
numbers looked ordinary, and the strategy that ran was not the strategy that was
described. An app review found it before we did.
"""
import pytest

from engine import simulate as sim
from engine.strategy import (FIELD_ALIASES, FIELDS, FRACTION_LIMITS, StrategyError,
                             parse_condition)


def test_every_pct_field_is_really_per_cent_and_every_fraction_says_frac():
    """The suffix has to carry the unit, or nothing else can be trusted to.

    `pnl_pct_of_credit` held a fraction while `spot_move_pct` next to it held per cent.
    A model told "take profit at 60%" wrote 60, which is unreachable, so the rule never
    fired and fifty trades ran to expiry with a stop that could not trigger.
    """
    for name in FIELDS:
        if name.endswith("_frac") or "_frac_of_" in name:
            continue
        assert "_pct" not in name or name in (
            "spot_move_pct", "vix_change_pct", "prev_day_move_pct", "gap_pct",
        ), f"{name} says pct but is not per cent"


def test_the_classic_unit_mistake_is_refused_with_the_conversion():
    with pytest.raises(StrategyError) as e:
        parse_condition({"pnl_frac_of_credit": {"gte": 60}}, "rules[0].when", 2)
    assert "0.6" in str(e.value) and "FRACTION" in str(e.value)

    with pytest.raises(StrategyError) as e:
        parse_condition({"pnl_frac_of_credit": {"lte": -100}}, "rules[0].when", 2)
    assert "-1.0" in str(e.value)


def test_the_old_names_still_parse_and_mean_the_same_thing():
    got = parse_condition({"pnl_pct_of_credit": {"gte": 0.6}}, "rules[0].when", 2)
    assert got["field"] == "pnl_frac_of_credit"
    assert got["bound"] == pytest.approx(0.6)
    assert set(FIELD_ALIASES.values()) <= set(FRACTION_LIMITS)


def test_a_weekday_gets_a_weekday_example_not_a_price_one():
    """The message used to suggest {"lte": -80} for day_of_week, which is meaningless."""
    with pytest.raises(StrategyError) as e:
        parse_condition({"day_of_week": "THU"}, "entry.when", 2)
    assert '{"eq": 4}' in str(e.value)
    with pytest.raises(StrategyError) as e:
        parse_condition({"day_of_week": {"eq": 9}}, "entry.when", 2)
    assert "Thursday" in str(e.value)


def test_a_rule_exit_is_named_after_its_rule():
    """'Which one closed it, the stop or the target?' has to be answerable."""
    assert sim._exit_label("stop 2x credit") == "RULE: stop 2x credit"
    assert sim._exit_label("") == "RULE"
    assert sim._exit_label(None) == "RULE"
    assert sim._exit_label("flat by Monday", "EXIT RULE") == "EXIT RULE: flat by Monday"


def test_the_rules_card_converts_the_fraction_to_a_percentage():
    """The threshold is a fraction; the sentence is a percentage.

    It was printed straight through, so a 60% target rendered as "0.6% of credit" and a
    stop at the whole credit as "1% of credit" -- both reading as thresholds that would
    fire instantly, on a card sitting above a run where neither rule had fired at all.
    """
    from server import rulecard

    def says(raw):
        return rulecard.cond_words(parse_condition(raw, "rules[0].when", 2))

    assert says({"pnl_frac_of_credit": {"gte": 0.6}}) == "profit reaches 60% of credit"
    assert says({"pnl_frac_of_credit": {"lte": -1.0}}) == "the loss reaches 100% of credit"
    assert says({"pnl_frac_of_max": {"gte": 0.25}}) == (
        "profit reaches 25% of the most it could make")
    # A field that really is per cent must NOT be multiplied.
    assert "1.5%" in says({"spot_move_pct": {"gte": 1.5}})
