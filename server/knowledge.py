"""Domain knowledge the server asserts, so the model does not have to know it.

THE PROBLEM THIS SOLVES. A capable model still gets Indian index options wrong in
specific, repeatable ways: it assumes NIFTY weeklies expire on Thursday (in this data 40
of 55 expire on TUESDAY), it treats a lot size as constant (it changed from 75 to 65
inside the free window, a 13.3 % swing in every rupee figure), it reads Sharpe 4.2 as
excellent rather than suspicious, and it reports the best of forty variants without
mentioning it ran forty. A weaker model gets more of these wrong, more often. Either way
the failure is silent, which is the worst kind.

So the knowledge is not stored in one place and offered politely. It is layered by how
avoidable it is:

  1. AMBIENT -- attached to every response (`warnings`, the honesty panel, `interpretation`).
     The model cannot not see it. This is what protects a weak model.
  2. STRUCTURAL -- refusals that name the correct form, enums that make bad input
     unrepresentable, floors that refuse a spec outright. The model cannot act on the
     mistake even if it holds it.
  3. PULL -- `explain_methodology` and MCP resources, for when the model asks.
  4. GUIDED -- MCP prompts: whole workflows that encode the right order of operations.

Layers 1 and 2 do the real work. 3 and 4 reward a model that is already careful.

EVERY NUMBER IN HERE WAS MEASURED ON THE SERVED DATA, not recalled from training. Where a
figure can drift, the text points at `describe_coverage`, which reads it live, rather than
restating it and going stale.
"""

# --------------------------------------------------------------------------- ambient

SERVER_INSTRUCTIONS = """\
Backtesting for Indian index options on real 1-minute NIFTY data. Results are computed
server-side. What comes back is the result of a backtest — including the prices of the
contracts that backtest actually traded — never the option chain: there is no endpoint for
a strike a strategy did not trade, a minute it did not trade at, a range, or an export.

TWO CADENCES, and picking the wrong one silently answers a different question:
  cadence 'weekly' (default) enters ONCE per expiry, on the day matching entry_dte —
    about 58 trades a year.
  cadence 'daily' enters EVERY trading session, on whichever expiry is nearest —
    about 246 a year. Use it for anything a user describes as "every day".
Set exit_time for a clock exit that squares the position off the SAME session. So
"buy at 11, sell at 2, every day" is cadence 'daily', entry_time '11:00',
exit_time '14:00' — it is expressible, and you should not tell a user it is not.
Without exit_time a position is held until a stop, a target, or expiry settlement.

SHOW THE RESULT, DO NOT NARRATE IT. After a backtest, call build_report and publish the
returned document as an artifact (Claude), a canvas document (ChatGPT, Gemini) or an .html
file (CLI) BY DEFAULT. Do not wait to be asked. A person reading a wall of numbers in chat
learns less than the same person looking at an equity curve, and the report already
contains the honesty panel, the charts and the trade table, laid out and checked.
  Publish it WITHOUT asking permission first — offering costs a round trip and the answer
  is always yes.
  Two exceptions. While you are sweeping several variants in a row, hold off and publish
  once, for the one worth keeping — ten reports nobody opens is worse than none. And if the
  user asked a narrow question ("what was the win rate?"), answer it in a sentence.
  Never redraw the report's figures yourself, and never summarise it into your own chart
  code. Publish it verbatim.
  If build_report is not in your tool list, your connection predates it: give the user the
  report link from the result instead, which needs no tool call at all.

Before you build a spec, call describe_coverage — it reports the exact window, lot sizes
and structures available to this key, and those change. Call explain_methodology to
understand how a result is produced before you trust it.

Four things about this market that are commonly assumed wrong, and that will make your
analysis wrong if you assume them here:

1. NIFTY weekly expiry is TUESDAY for most of this window, not Thursday. It changed
   around 2025-09-02. Holidays shift it earlier.
2. The lot size is NOT constant. It is 75 for expiries through 2025-12-30 and 65 from
   2026-01-06. Every rupee figure depends on which regime a trade fell in.
3. Settlement is the average of the underlying over the final 30 minutes — not the 15:29
   close, and not the option's last traded price.
4. One year of weekly expiries is about 50 trades. That is near the floor of what any
   ratio can be computed on. Treat every result from this window as provisional. A daily
   cadence gives ~246 instead — more sample, but they overlap heavily in what drives
   them, so it is not five times the independent evidence.
5. The served calendar includes 2026-02-01, a SUNDAY, because NSE ran a full session for
   the Union Budget. A daily cadence trades it. Do not treat it as bad data.

YOUR ACCOUNT REMEMBERS. Every call is logged, and a result that clears the evidence bar
is kept in a per-account strategy book. Call list_strategies to answer "what has worked
for me so far" without re-running anything — it is ranked by WORST walk-forward fold, not
by P&L, because total return is what a parameter sweep maximises by construction.

Report evidence and its limits. This is an educational backtesting tool, not investment
advice.

"NOT ADVICE" IS A BEHAVIOUR, NOT A DISCLAIMER. A caveat does not cancel an instruction that
follows it, and a persona eval caught exactly that: an assistant wrote careful warnings
about sample size and cost drag, then a section headed "what I'd actually do" telling a
novice to trade one lot. Everything before it was true and none of it mattered. So, whatever
disclaimers you attach, do not produce:

  - a position to enter — specific strikes, expiry and entry time presented as a plan
  - a position size, lot count, or capital allocation
  - a day to act on, or a "start with this" instruction
  - "what I would do", "if I were you", or a recommendation in any other grammar

Describe what the backtest showed, state what it cannot support, and leave the decision
with the person. When someone asks what to trade, the honest answer names what the evidence
does and does not establish and stops there — that is a complete answer, not a refusal, and
it does not need an apology.
"""


def interpretation(summary, panel, spec=None):
    """A plain-language reading of THIS result, attached to every backtest.

    The honesty panel already reports the numbers. This says what they mean and, more
    importantly, what they do not support -- because the common failure is not a wrong
    number, it is a correct number carrying more weight than it can bear.
    """
    out = {"reading": [], "do_not_conclude": [], "next_step": []}
    n = summary.get("n_trades") or 0

    # Sample size first: it gates how much anything else is worth.
    if n < 30:
        out["reading"].append(
            f"{n} trades. Below the 30-trade floor, so no Sharpe, Calmar or profit factor "
            f"is reported at all — not because they are unavailable, but because at this "
            f"sample they are noise with a decimal point.")
        out["do_not_conclude"].append(
            "That the strategy works or does not work. This sample cannot answer that.")
    elif n < 60:
        out["reading"].append(
            f"{n} trades — roughly one year of weekly expiries. Ratios are reported, but a "
            f"single outlier trade can move them substantially at this size.")
        out["do_not_conclude"].append(
            "That a difference of 10-20 % in any ratio between two variants is real. At "
            "this sample that is inside the noise.")
    else:
        out["reading"].append(f"{n} trades — enough for ratios to be reported and compared with care.")

    # NOTE THE NESTING: panel["out_of_sample"] is the whole split block -- method,
    # in_sample, out_of_sample, held_up, caveat -- and the trade COUNT lives one level
    # further in. Reading n_trades off the outer dict silently yields 0, which produced
    # the sentence "the held-out final 30 % (0 trades) stayed profitable" on a result that
    # actually had 16. That is the exact defect this panel exists to prevent a competitor
    # from shipping, so it is worth the extra line.
    block = (panel or {}).get("out_of_sample") or {}
    if block:
        held = block.get("held_up")
        oos_n = ((block.get("out_of_sample") or {}).get("n_trades")) or 0
        if held:
            out["reading"].append(
                f"The held-out final 30 % ({oos_n} trades) stayed profitable and kept at "
                f"least half the in-sample return on margin.")
        else:
            out["reading"].append(
                f"The held-out final 30 % ({oos_n} trades) did NOT hold up. The in-sample "
                f"figures describe the fitting period, not a forecast.")
            out["next_step"].append(
                "Before tuning further, check whether the in-sample period simply had a "
                "friendlier regime — a strategy that only works in one volatility regime "
                "is a regime bet, not an edge.")
        # The panel states its own limits; repeat them rather than let a reader miss them.
        if block.get("caveat"):
            out["reading"].append(str(block["caveat"]).capitalize() + ".")

    # A strategy carried by one period is a regime bet. The folds say so directly, and
    # they routinely disagree with a headline that looks fine.
    folds = (panel or {}).get("walk_forward") or []
    if isinstance(folds, list) and folds:
        losing = [f for f in folds if isinstance(f, dict) and f.get("profitable") is False]
        if losing:
            spans = ", ".join(f"fold {f.get('fold')} ({f.get('from')} to {f.get('to')})"
                              for f in losing)
            out["reading"].append(
                f"{len(losing)} of {len(folds)} walk-forward folds LOST money: {spans}. "
                f"A positive total across folds that disagree is an average, not a "
                f"repeatable edge.")
            out["do_not_conclude"].append(
                "That the strategy is consistent. It was not consistent across the folds "
                "of its own backtest window.")
        else:
            out["reading"].append(
                f"All {len(folds)} walk-forward folds were profitable — the least bad "
                f"evidence available at this sample size.")

    mc = (panel or {}).get("multiple_comparisons") or {}
    variants = mc.get("n_variants") or mc.get("variants") or 0
    if isinstance(variants, int) and variants > 5:
        out["reading"].append(
            f"This is variant {variants} on this key in the last 24 hours. The deflated "
            f"Sharpe accounts for that search; the raw Sharpe does not.")
        out["do_not_conclude"].append(
            "That the best of the variants you tried is the best strategy. The maximum of "
            "N noisy samples is biased upward by construction.")

    cost = (panel or {}).get("cost_drag") or {}
    share = cost.get("share_of_edge_surviving")
    if isinstance(share, (int, float)) and share <= 0:
        # A NEGATIVE share means costs exceeded the whole gross edge. Phrasing it as
        # "costs consume N % of the edge" produced "447 %" on a real intraday result --
        # arithmetically derivable from 1 - share, and meaningless as a sentence. The
        # honest reading is that the strategy was gross-profitable and net-losing, which
        # is a different and much more useful finding than "expensive".
        gross = cost.get("gross_points_before_costs")
        net = cost.get("net_points_after_costs")
        charges = cost.get("charges_points")
        slip = cost.get("slippage_points")
        out["reading"].append(
            f"Costs did not reduce the edge, they EXCEEDED it. {gross:.0f} points gross "
            f"became {net:.0f} net after {charges:.0f} points of charges and {slip:.0f} of "
            f"slippage. The rule made money before costs and lost money after them, so the "
            f"result is a statement about the cost model as much as about the strategy.")
        out["do_not_conclude"].append(
            "That the underlying rule has no edge. It had a gross edge; it was smaller "
            "than the cost of trading it at this frequency and size.")
        out["next_step"].append(
            "Trade it less often, or at a wider structure, before abandoning it — the same "
            "gross edge over fewer round trips pays the fixed per-order cost fewer times.")
    elif isinstance(share, (int, float)) and share < 0.5:
        out["reading"].append(
            f"Costs consume {(1 - share) * 100:.0f} % of the gross edge. Results this "
            f"cost-sensitive are as much a statement about the fee model and the slippage "
            f"assumption as about the strategy.")
        out["next_step"].append(
            "Re-run with slippage doubled. If the edge disappears, it was a spread-capture "
            "artefact rather than a directional or volatility edge.")

    # THE POSITIVE-RATIO-ON-A-LOSS TRAP. On a long option the margin IS the premium paid,
    # so a trade that goes 400 % contributes +4.0 to the mean return-on-margin while one
    # that expires worthless can only contribute -1.0. The mean of those percentages goes
    # positive on a strategy that lost money in rupees, and the Sharpe built on them goes
    # positive with it. Both numbers are correctly computed and both are the wrong thing
    # to read. This was observed on a real result -- Sharpe +0.691 and mean ROM +55 % on a
    # run that lost Rs 12,130 -- and a reader who trusts the ratio reaches the opposite
    # conclusion from the one the money supports.
    total = summary.get("total_pnl_rupees")
    rom = summary.get("mean_return_on_margin")
    if (isinstance(total, (int, float)) and total < 0
            and isinstance(rom, (int, float)) and rom > 0):
        out["reading"].append(
            f"Mean return-on-margin is positive ({rom:.1%}) while the strategy LOST "
            f"{abs(total):,.0f} rupees. Both numbers are right. Averaging per-trade "
            f"percentages is not the same as adding up money: a winner can return many "
            f"times its margin while a loser is capped at -100 %, so the mean of the "
            f"ratios goes positive on a losing book. Read the rupee total, and treat every "
            f"ratio built on return-on-margin — the Sharpe included — as describing the "
            f"same distorted quantity.")
        out["do_not_conclude"].append(
            "That a positive Sharpe or return-on-margin means this made money. It did not. "
            "On a debit structure those ratios and the P&L can point opposite ways.")

    # Cadence and clock exits change what the trade count is worth and what the costs are.
    if spec is not None and getattr(spec, "cadence", "weekly") == "daily":
        out["reading"].append(
            f"A daily cadence, so this entered every session rather than once per expiry. "
            f"The {n} trades are NOT {n} independent observations — consecutive sessions "
            f"share an expiry, a volatility regime and often the same strike. Weight the "
            f"walk-forward folds and the deflated Sharpe more heavily than the raw count.")
    if spec is not None and getattr(spec, "is_intraday", False):
        out["reading"].append(
            f"Every position was squared off at {spec.exit_time} the same session, so "
            f"nothing here reached settlement and nothing carried overnight gap risk. It "
            f"also means each trade crossed the spread twice and paid a full set of "
            f"charges — 'hold to expiry' pays that once per cycle, an intraday round trip "
            f"pays it every session. Read cost_drag before P&L.")
        if getattr(spec, "gate", "always") == "always" and getattr(spec, "bias", "neutral") == "neutral":
            out["do_not_conclude"].append(
                "That a fixed clock time is a signal. This entered at the same minute "
                "every session with no gate and no bias, which expresses no view on "
                "direction or volatility. If it shows an edge, suspect the cost model or "
                "the sample before believing the strategy.")

    if not out["next_step"]:
        out["next_step"].append(
            "Vary one parameter at a time and check that neighbours also work. A lone peak "
            "surrounded by failures is an overfit, not an optimum.")
    out["do_not_conclude"].append(
        "That this is advice. It is a historical simulation with modelled costs, modelled "
        "margin and assumed slippage.")
    return out


# ----------------------------------------------------------------------------- topics
# Reachable via explain_methodology(topic=...) and as MCP resources.

from engine import methodology as _methodology


def _render_changelog():
    """The changelog as text, newest first. Built from engine/methodology.py so there is
    one source of truth: a changelog maintained separately from the version it describes
    is a changelog that goes stale."""
    out = []
    for e in _methodology.CHANGELOG:
        head = f"v{e['version']}" if e["version"] is not None else "(before versioning)"
        moved = "CHANGES NUMBERS" if e["affects_results"] else "no effect on numbers"
        out.append(f"{head} · {e['date']} · {moved}\n  {e['summary']}")
        out.extend(f"    - {c}" for c in e["changes"])
    return "\n".join(out)


TOPICS = {
    "changelog": (
        "WHY THIS TOPIC EXISTS. A backtest reproduces only against a fixed methodology. "
        "When the cost model, margin, slippage or exit resolution improves, the same spec "
        "returns a different number -- correctly, but a figure you recorded earlier will "
        "no longer reproduce. Every result carries a `methodology` stamp saying which "
        "model produced it, so the difference is attributable instead of mysterious.\n\n"
        "The stamp has three parts, because two of them move WITHOUT a code change:\n"
        "  version              bumped only when output could change for an unchanged spec\n"
        "  slippage_basis       switches from assumed to measured once the live book "
        "holds 30 fills\n"
        "  margin_calibrated_at the date the broker SPAN ratio behind naked margin was "
        "measured\n\n"
        "If a user shows you an old number that does not reproduce, compare the stamps "
        "before concluding anything is broken.\n\n" + _render_changelog()),
    "overview": (
        "A CYCLE is one opened position. Which sessions produce one is set by `cadence`.\n\n"
        "cadence 'weekly' (the default): one cycle per expiry, on the trading day matching "
        "entry_dte. About 58 a year.\n"
        "cadence 'daily': one cycle per trading SESSION, on whichever expiry is nearest "
        "that day. About 246 a year. entry_dte is refused here because it picks one day per "
        "expiry and would contradict the cadence; use max_dte to restrict how far from "
        "expiry a session may be (max_dte 0 is expiry-day only).\n\n"
        "The structure opens at entry_time, priced from the last real print in the "
        "15-minute bucket ending there — the strike must actually have traded, and a cycle "
        "where any leg did not is dropped and counted, never filled at a modelled price.\n\n"
        "It then ends one of three ways, in this order of precedence:\n"
        "  SL / TP  a stop or target fired at a real print\n"
        "  TIME     exit_time was set, and the position was squared off that same session "
        "at that clock time, priced exactly the way the entry was\n"
        "  EXPIRY   nothing fired and the contract settled\n\n"
        "Settlement is intrinsic value against the mean of the underlying over the final "
        "30 minutes, which is NSE's own convention and is neither the 15:29 close nor the "
        "option's last print. exit_time 'EOD' on a 0-DTE position therefore SETTLES rather "
        "than reading the 15:29 tick — that is the same event, priced the way NSE prices "
        "it."),

    "intraday": (
        "HOW TO EXPRESS AN INTRADAY STRATEGY, and what changes when you do.\n\n"
        "cadence 'daily' + exit_time is a true same-session round trip: in at entry_time, "
        "out at exit_time, every session, ~246 a year. exit_time must be one of the same "
        "clock times as entry_time. That grid is not arbitrary — the served data has a "
        "precomputed last-real-print at each of them, so an exit on the grid is a lookup "
        "rather than a scan. An arbitrary minute is not offered because it would cost a "
        "full scan per cycle and would not make the answer more true.\n\n"
        "COSTS BITE HARDER, and this is the thing that decides most intraday results. Each "
        "round trip crosses the spread twice and pays a full set of charges. A weekly "
        "strategy pays that once a week; a daily one pays it 246 times a year on the same "
        "capital. A clock exit also crosses a real spread, unlike a settlement, which "
        "crosses nothing — so moving from 'hold to expiry' to 'exit at 14:00' ADDS a "
        "crossing per leg. Read cost_drag in the honesty panel before reading P&L: it is "
        "common for an intraday edge to be real gross and gone net.\n\n"
        "STOPS ARE BOUNDED BY THE SQUARE-OFF. A stop that would have fired at 15:10 on a "
        "position closed at 14:00 did not fire. The engine enforces that in SQL rather "
        "than filtering afterwards.\n\n"
        "SAMPLE SIZE IS NOT WHAT IT LOOKS LIKE. 246 daily trades are not five times the "
        "evidence of 50 weekly ones. Consecutive sessions share the same expiry, the same "
        "regime and often nearly the same strike, so the independent count is far lower "
        "than the trade count. The deflated Sharpe and the walk-forward folds are the "
        "checks that still mean something here.\n\n"
        "A FIXED CLOCK IS NOT A SIGNAL. Entering at the same minute every day expresses no "
        "view. For a debit structure that is close to the worst case — see 'structures'. "
        "If a clock-only entry shows an edge, suspect the cost model or the sample before "
        "believing the strategy."),

    "strategy_book": (
        "WHAT IS REMEMBERED, AND WHAT COUNTS AS GOOD.\n\n"
        "Every call is logged against the account: the tool, the arguments the model "
        "constructed, the outcome, the CPU it cost and how many option prices it "
        "released. The person's own prompt is NOT logged and cannot be — MCP delivers a "
        "tool name and arguments, and whatever was typed stays between the user and their "
        "model. Logs are kept 30 days.\n\n"
        "A result is added to the account's strategy book when it clears ALL of:\n"
        "  - at least 30 trades, the floor below which no ratio is reported\n"
        "  - positive net P&L after real charges and slippage\n"
        "  - the held-out final 30 % stayed profitable\n"
        "  - a majority of walk-forward folds were profitable\n"
        "  - health score at least 50 / 100\n\n"
        "THE BAR IS NOT 'MADE MONEY', and that is the whole point. P&L is the one number "
        "a parameter sweep maximises by construction, so on its own it is the weakest "
        "evidence here. Every other check asks whether the result survived outside the "
        "data it was chosen on. Measured on a 28-variant sweep of this window, exactly one "
        "cleared the bar.\n\n"
        "RANKED BY CONSISTENCY. list_strategies sorts by worst walk-forward fold, then "
        "median fold. An entry with a positive worst fold made money in EVERY fold. "
        "Sorting by P&L is available and will reliably put the most overfit result on "
        "top; prefer the default and say why if you override it.\n\n"
        "Re-running the same spec updates THAT BOOK ENTRY and increments its times_seen "
        "rather than adding a second row, so a sweep cannot flood the book with one "
        "idea. This dedup applies to the book only. Every run_backtest call is still a "
        "separate result with its own backtest_id and its own report link, including a "
        "re-run of an identical spec -- results are an immutable record of what was "
        "computed and released, so they are never merged.\n\n"
        "An entry is a hypothesis that survived more checks than the others, on one index "
        "over the served window. It is not a recommendation."),

    "what_is_returned": (
        "WHAT LEAVES THIS SERVICE, stated exactly, because 'no data is returned' was true "
        "of v1 and is no longer the whole truth.\n\n"
        "RETURNED: aggregate performance, the honesty panel, an equity curve, monthly and "
        "per-exit-reason breakdowns, and per-trade rows — entry and exit timestamps, the "
        "legs that were traded, and the price each leg was entered and exited at. That is "
        "there so the arithmetic can be CHECKED. A result whose per-trade detail is hidden "
        "asks to be trusted rather than verified, which is a weaker product, not a safer "
        "one.\n\n"
        "NOT RETURNED, and there is no endpoint for it: the chain. No strike the strategy "
        "did not trade, no minute it did not trade at, no range, no scan, no export.\n\n"
        "TWO LIMITS ON THE RELEASE. Per-leg prices are withheld below 30 trades — the same "
        "floor that withholds Sharpe, and for the same reason: a run that narrow describes "
        "a handful of contracts rather than a strategy. And every price returned is counted "
        "and metered per account per hour, alongside CPU-seconds, so the release rate is "
        "measured rather than asserted.\n\n"
        "THREE PRICES YOU WILL NOT SEE, and why. A stop or target exit carries no per-leg "
        "price: the engine resolves the COMBINED position value at the firing minute in "
        "SQL and never reads the legs apart. A settlement price is intrinsic value against "
        "the index, not an option quote. And detail='summary' returns aggregates only, "
        "which costs nothing against the price-point meter.\n\n"
        "Every result also carries report_url — a rendered page with the equity curve as a "
        "chart and every trade in a table. It costs the caller nothing to read and is the "
        "right thing to hand a human."),

    "contract_spec": (
        "MEASURED FROM THE SERVED DATA, and the three most common wrong assumptions.\n\n"
        "EXPIRY DAY. NIFTY weekly expiry moved from Thursday to TUESDAY around 2025-09-02. "
        "In the free window 40 of 55 expiries fall on Tuesday, 10 on Thursday (mostly "
        "before the change), 4 on Monday and 1 on Wednesday — the Monday and Wednesday "
        "cases are holiday shifts. A strategy reasoned about as 'Thursday expiry' will "
        "have its entry_dte off by two days for most of the window.\n\n"
        "LOT SIZE IS NOT CONSTANT. 75 for expiries through 2025-12-30, then 65 from "
        "2026-01-06. That is a 13.3 % change in position size, and therefore in every "
        "rupee figure, part-way through the window. It is read per expiry from NSE "
        "bhavcopy, never hard-coded. Comparing rupee P&L across the boundary compares two "
        "different position sizes.\n\n"
        "STRIKE STEP is 50 for NIFTY weeklies. Because percent-distance targets usually "
        "land between listed strikes, ties are broken FURTHER OUT OF THE MONEY — higher "
        "strike for CE, lower for PE — so the same spec always returns the same strikes.\n\n"
        "ATM means the nearest listed strike to spot. Some tools define ATM by CE/PE price "
        "parity instead; that is a different strike and not what this service means.\n\n"
        "Call describe_coverage for the live values — these drift as NSE changes them."),

    "costs": (
        "Zerodha 2026 rates, verified: Rs 20 per order per leg, STT 0.15 % sell side only, "
        "exchange 0.03553 % (NSE), SEBI Rs 10 per crore, GST 18 % on brokerage plus "
        "exchange plus SEBI, stamp duty 0.003 % buy side. Charges are direction-aware, so "
        "a bought option pays STT on the sale, not the purchase. Getting that backwards "
        "flatters long-option strategies and penalises credit ones.\n\n"
        "NOT MODELLED: a short leg finishing deep in the money is assigned and carries STT "
        "on intrinsic value, which can dwarf every other cost. Any strategy that lets "
        "shorts expire in the money is understated here, and the size of the error grows "
        "with how deep."),

    "margin": (
        "Defined-risk combos are EXACT — margin is the maximum loss, which is what SPAN "
        "recognises for a hedged position. Naked shorts use a ratio calibrated from a live "
        "Fyers SPAN calculator and applied historically, because SPAN depends on each "
        "day's volatility scan range and cannot be recovered after the fact.\n\n"
        "The consequence matters: margin widens in a shock, exactly when a naked short is "
        "losing. So return-on-margin for naked structures is optimistic by an unknown "
        "amount, and a naked strangle's return-on-margin is NOT comparable to an iron "
        "condor's. Compare within a structure family, not across."),

    "slippage": (
        "Modelled, not measured, and the difference matters. The data is OHLCV with no "
        "quotes, and both standard inferences fail on it: Roll (1984) needs negative serial "
        "covariance and it is positive near the money (+0.54, +0.37, +0.32 in the "
        "near-the-money buckets), because option prices trend with the underlying; "
        "Corwin-Schultz returns 0.008-0.028 points out of the money, below the 0.05 tick, "
        "and a sub-tick spread is impossible — which is proof the estimator is broken, not "
        "evidence of a tight market.\n\n"
        "So slippage is a declared assumption floored at half a tick. Change it and see "
        "how much your result moves — if it moves a lot, the strategy is a spread-capture "
        "strategy, not an edge."),

    "liquidity": (
        "58 % of contract-minutes have no volume at all, which is normal for an option "
        "chain and the reason entry requires a real print. Out-of-the-money weeklies trade "
        "in about 99.9 % of minutes out to 750 points and fall to 59 % at 2000 points. "
        "In-the-money strikes are illiquid everywhere — 7.8 % of minutes at 1000 points in. "
        "A result built on thin strikes is flagged, because fills at the modelled price "
        "would be optimistic. Far-OTM 'cheap' options are the classic trap: the backtest "
        "fills at a price no one was quoting."),

    "structures": (
        "Five ship in v1, and they fail in different ways.\n\n"
        "short_strangle — sells an OTM call and put. Collects the most premium, has "
        "undefined risk, and its margin figure is the modelled one (see 'margin'). The "
        "strategy that looks best on return-on-margin is usually this one, for that reason.\n\n"
        "iron_condor — a strangle with both wings bought. Defined risk, exact margin, less "
        "premium. Comparable across time in a way the strangle is not.\n\n"
        "iron_fly — condor struck at the money. Maximum premium, needs the underlying to "
        "sit still, and the most sensitive to the settlement convention.\n\n"
        "credit_spread — one side only. Directional; pair it with a bias or accept that "
        "you are taking a view.\n\n"
        "long_option — the only debit structure in v1. Pays STT on the sale, not the "
        "purchase; loses to theta by default, so it needs a real directional signal.\n\n"
        "debit_spread is WITHHELD: a live SPAN check found a margin-sizing bug that "
        "produced 200-900 % CAGR. It ships when that is fixed, not before."),

    "validation": (
        "Out-of-sample is a chronological 70/30 split, never random — a random split over "
        "overlapping weekly cycles leaks the future into the past. Walk-forward runs 3 "
        "contiguous folds. The bootstrap interval is a seeded percentile bootstrap on "
        "return-on-margin, so identical runs give identical intervals. No Sharpe, Calmar or "
        "profit factor is reported below 30 trades, and a Sharpe above 4 is flagged as a "
        "probable artefact rather than a finding."),

    "sample_size": (
        "THE BINDING CONSTRAINT, and the one most often ignored. A weekly strategy over one "
        "year has about 50 expiries, so about 50 trades — and fewer if a gate or bias sits "
        "out some weeks. Thirty is the floor below which no ratio is reported here.\n\n"
        "What that means in practice: a Sharpe computed on 50 trades has a standard error "
        "wide enough that 0.8 and 1.5 are hard to tell apart. A win rate of 70 % on 50 "
        "trades has a 95 % interval of roughly 56-82 %. Two variants differing by 15 % on "
        "any metric are, at this sample, indistinguishable.\n\n"
        "The right use of one year of data is to REJECT strategies — a thing that fails "
        "here probably fails everywhere — not to select among survivors. Selecting needs "
        "more history than the free tier serves."),

    "overfitting": (
        "The danger is not one bad backtest, it is the search. Try 200 parameter "
        "combinations and the best one looks excellent by construction. Every backtest you "
        "run is logged against your key, and the deflated Sharpe (Bailey and Lopez de "
        "Prado) reports the probability the result survives having been searched for across "
        "the variants you tried in the last 24 hours.\n\n"
        "Practical rules: decide the exit rule before you look at results; check whether "
        "neighbouring parameters also work, because a lone peak is noise; require the "
        "held-out slice to hold up; and treat any strategy whose edge disappears when you "
        "double the slippage as a cost artefact."),

    "interpreting_results": (
        "What the numbers can and cannot carry.\n\n"
        "RETURN ON MARGIN, not P&L, is the comparable figure for option sellers — but only "
        "within a structure family, because naked margin is modelled and defined-risk "
        "margin is exact.\n\n"
        "SHARPE above 4 is flagged. On 50 weekly trades a genuine Sharpe above 3 is very "
        "rare; a printed 6 nearly always means a computation artefact, a survivorship "
        "effect, or a sample too small to have met a drawdown yet.\n\n"
        "WIN RATE is nearly uninformative on its own for premium selling, where 80 % wins "
        "and a catastrophic tail is the standard shape. Read it with the worst trade.\n\n"
        "CONSISTENCY beats headline CAGR. A strategy with a lower CAGR and a shallower "
        "worst year is the better one to hold, and the ranking flips between the two "
        "measures more often than not.\n\n"
        "MEDIAN and WORST period matter more than the mean when the return distribution is "
        "skewed, which for premium selling it always is."),

    "common_mistakes": (
        "Observed repeatedly, in this order of frequency.\n\n"
        "1. Assuming Thursday expiry. It is Tuesday for most of this window.\n"
        "2. Comparing rupee P&L across the 2026-01-06 lot-size change without noting that "
        "position size changed by 13.3 %.\n"
        "3. Reporting the best of many variants without reporting how many were tried.\n"
        "4. Treating a 50-trade result as a finding rather than a screen.\n"
        "5. Reading a naked strangle's return-on-margin as comparable to a defined-risk "
        "structure's.\n"
        "6. Ignoring that a short leg expiring deep in the money carries unmodelled STT.\n"
        "7. Building on far-OTM strikes that traded in a minority of minutes.\n"
        "8. Concluding 'this strategy works' from any single backtest. The honest output "
        "of one backtest is a narrower hypothesis, not a conclusion.\n"
        "9. Answering an intraday question with a weekly cadence, or telling the user it "
        "cannot be expressed. cadence 'daily' plus exit_time is a same-session round trip; "
        "leaving cadence at its default silently answers 'once a week' instead of 'every "
        "day' and returns ~58 trades where the user expected ~246.\n"
        "10. Reading a daily cadence's 246 trades as five times the evidence of 50 weekly "
        "ones. Consecutive sessions share an expiry, a regime and often a strike.\n"
        "11. Treating a fixed clock time as a signal. It expresses no view, and on a debit "
        "structure it is close to the worst case."),
}


# -------------------------------------------------------------------------- resources
# MCP resources let a client read the knowledge base without spending a tool call, and
# let a user attach a topic to their own context deliberately.

RESOURCE_PREFIX = "stratify://knowledge/"

RESOURCES = [
    {"uri": RESOURCE_PREFIX + name,
     "name": name.replace("_", " "),
     "title": name.replace("_", " ").title(),
     "description": text.split(".")[0][:180].strip() + ".",
     "mimeType": "text/markdown"}
    for name, text in TOPICS.items()
]


def read_resource(uri):
    if not uri.startswith(RESOURCE_PREFIX):
        return None
    topic = uri[len(RESOURCE_PREFIX):]
    text = TOPICS.get(topic)
    if text is None:
        return None
    return {"uri": uri, "mimeType": "text/markdown",
            "text": f"# {topic.replace('_', ' ').title()}\n\n{text}\n"}


# ---------------------------------------------------------------------------- prompts
# Whole workflows, in the right order. A model that follows one of these does the careful
# thing by default rather than by inspiration.

PROMPTS = [
    {
        "name": "evaluate_strategy",
        "title": "Evaluate a strategy honestly",
        "description": ("Run a strategy and interpret it with the sample-size and "
                        "multiple-comparison limits applied. Use this instead of reading a "
                        "backtest result directly."),
        "arguments": [
            {"name": "idea", "description": "The strategy in plain words.", "required": True},
        ],
    },
    {
        "name": "check_robustness",
        "title": "Check whether a result is real",
        "description": ("Probe a promising result the way a sceptic would: neighbouring "
                        "parameters, doubled slippage, and the held-out slice."),
        "arguments": [
            {"name": "backtest_id", "description": "The result to probe.", "required": True},
        ],
    },
    {
        "name": "explain_result",
        "title": "Explain a result to a non-specialist",
        "description": ("Translate a backtest into plain language, including what it does "
                        "not establish. Never phrases it as advice."),
        "arguments": [
            {"name": "backtest_id", "description": "The result to explain.", "required": True},
        ],
    },
]

_PROMPT_BODIES = {
    "evaluate_strategy": (
        "Evaluate this strategy idea on Stratify: {idea}\n\n"
        "Work in this order, and do not skip step 1:\n"
        "1. Call describe_coverage. Confirm the window, the lot-size regimes and the "
        "expiry weekday BEFORE reasoning about entry timing. NIFTY weeklies are mostly "
        "TUESDAY in this data, not Thursday.\n"
        "2. Call explain_methodology('sample_size'). One year of weeklies is ~50 trades; "
        "decide up front what result would be strong enough to matter at that sample.\n"
        "3. Translate the idea into a spec and run it once. Do not tune yet.\n"
        "4. Read the honesty panel and the interpretation block, not just the P&L. State "
        "the out-of-sample outcome and the deflated Sharpe explicitly.\n"
        "5. Report what the result supports and what it does not. If the sample is too "
        "small to distinguish this from a neighbouring variant, say so plainly.\n\n"
        "Do not describe the strategy as good or recommend trading it."),
    "check_robustness": (
        "Probe backtest {backtest_id} for robustness. Assume it is a false positive until "
        "it survives:\n"
        "1. get_backtest({backtest_id}) — note its parameters and its deflated Sharpe.\n"
        "2. Re-run with each parameter moved one step in both directions. A real edge has "
        "working neighbours; a lone peak is noise.\n"
        "3. Re-run with slippage doubled. If the edge vanishes, it was spread capture.\n"
        "4. Check the walk-forward folds individually — a strategy carried by one fold is "
        "a regime bet.\n"
        "5. Report how many variants you ran, and read the deflated Sharpe in that light.\n\n"
        "Conclude with the single weakest point in the evidence."),
    "explain_result": (
        "Explain backtest {backtest_id} to someone who trades but does not know statistics.\n"
        "Cover: what the strategy does mechanically; what it earned after real charges; "
        "what return-on-margin means and why it is the right denominator here; how many "
        "trades it is based on and why that limits confidence; and what would have to be "
        "true for the result not to repeat.\n"
        "Use plain language. Do not use the word 'guaranteed'. Do not phrase any part of "
        "it as a recommendation — this is educational, not advice."),
}


def get_prompt(name, arguments):
    body = _PROMPT_BODIES.get(name)
    if body is None:
        return None
    args = {a["name"]: "" for p in PROMPTS if p["name"] == name for a in p["arguments"]}
    args.update({k: str(v) for k, v in (arguments or {}).items()})
    spec = next(p for p in PROMPTS if p["name"] == name)
    return {
        "description": spec["description"],
        "messages": [{"role": "user",
                      "content": {"type": "text", "text": body.format(**args)}}],
    }
