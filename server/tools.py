"""MCP tool surface.

Six tools (decision F1). Descriptions are written for a model to read, not a human to
skim: an agent picks a tool from its description alone, so each one says what it returns
and what it will refuse. Registry data shows descriptions are commonly truncated near 100
characters, so the first sentence carries the meaning.

`search` and `fetch` are thin aliases with a single string parameter, which is the shape
ChatGPT's deep research requires (decision F4). They open a channel we would otherwise be
excluded from and cost almost nothing.
"""
import hashlib
import json
import time

from engine import (backtest, detail, honesty, index_series, methodology, metrics,
                    signals, spec as spec_mod)
from engine.config import charges, contracts, liquidity, margin, slippage
from engine import db as engine_db

from engine import simulate, strategy as strategy_mod  # noqa: E402
from . import (appview, artifact, book, feedback as feedback_mod, fullreport,
               knowledge, sizing, store)

# A full year of daily cadence is ~246 trades; returning every one of them with legs is
# roughly 150 KB, which is most of a model's useful attention spent on a table it will
# summarise anyway. The default returns the complete equity curve and breakdowns -- the
# things an aggregate CANNOT be recovered from -- plus enough trades to check the
# arithmetic, and says how to get the rest.
STANDARD_TRADES = 25

SPEC_SCHEMA = {
    "type": "object",
    "required": ["structure", "params"],
    "additionalProperties": False,
    "properties": {
        "structure": {"type": "string", "enum": sorted(spec_mod.STRUCTURE_PARAMS),
                      "description": "Option structure to trade."},
        "symbol": {"type": "string", "enum": ["NIFTY"],
                   "description": "Free tier serves NIFTY only."},
        "params": {
            "type": "object",
            "description": ("Structure parameters. pct_offset and pct_width are percent "
                            "of spot. sl_mult is a multiple of the credit received; "
                            "sl_pct and tp_pct are fractions of premium paid. entry_dte "
                            "is days to expiry at entry. direction is CE or PE for "
                            "directional structures, and must be omitted when a bias is "
                            "set."),
            "properties": {
                "pct_offset": {"type": "number", "minimum": 0, "maximum": 20},
                "pct_width": {"type": "number", "minimum": 0, "maximum": 20},
                "sl_mult": {"type": "number", "exclusiveMinimum": 0},
                "sl_pct": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
                "tp_pct": {"type": "number", "exclusiveMinimum": 0},
                "entry_dte": {"type": "integer", "minimum": 0, "maximum": 45},
                "direction": {"type": "string", "enum": ["CE", "PE"]},
            },
        },
        "entry_time": {"type": "string", "enum": list(spec_mod.ENTRY_TIMES),
                       "description": "IST. EOD is 15:29, the last tradeable minute."},
        "exit_time": {
            "type": "string", "enum": list(spec_mod.EXIT_TIMES),
            "description": ("IST clock exit — squares the position off the SAME session, "
                            "so it never reaches expiry. Omit to hold until a stop, a "
                            "target or settlement. Must be after entry_time. Set this to "
                            "express an intraday round trip such as in at 11:00, out at "
                            "14:00.")},
        "cadence": {
            "type": "string", "enum": list(spec_mod.CADENCES),
            "description": ("'weekly' (default) enters ONCE per expiry, on the day matching "
                            "entry_dte — about 58 trades a year. 'daily' enters EVERY "
                            "trading session on whichever expiry is nearest — about 246. "
                            "Use 'daily' for anything described as 'every day'.")},
        "max_dte": {
            "type": "integer", "minimum": 0, "maximum": 45,
            "description": ("cadence 'daily' only: skip sessions where the nearest expiry "
                            "is further out than this. max_dte 0 is expiry-day only.")},
        "gate": {"type": "string", "description": "Entry filter; 'always' to disable."},
        "bias": {"type": "string",
                 "description": ("Chooses the side each cycle for directional structures. "
                                 "'neutral' to use a fixed direction instead.")},
        "period": {
            "type": "object", "additionalProperties": False,
            "properties": {"from": {"type": "string"}, "to": {"type": "string"}},
            "description": "YYYY-MM-DD, inside 2025-07-01 to 2026-06-30.",
        },
    },
}

# The open protocol. A position is an explicit LEG LIST; what to do about it is a RULE
# LIST. Nothing here is gated by tier -- the only thing a paid tier buys is a longer
# window of history.
_COMPARE = {"type": "object", "additionalProperties": False, "properties": {
    "gt": {"type": "number"}, "gte": {"type": "number"},
    "lt": {"type": "number"}, "lte": {"type": "number"}, "eq": {"type": "number"},
    "between": {"type": "array", "items": {"type": "number"},
                "minItems": 2, "maxItems": 2},
    "leg": {"type": "integer", "minimum": 0,
            "description": "required for leg_* fields and spot_beyond_strike: which leg, "
                           "numbered from 0 in the order you listed them"}}}

_STRIKE = {"description":
    "How to pick the strike. One of: {\"pct_offset\": 1.0} percent from spot (negative "
    "for puts) | {\"points_offset\": 200} | \"atm\" | {\"strike\": 24000} | "
    "{\"premium_near\": 50} the strike whose last real print is nearest 50 points | "
    "{\"delta_near\": 0.20} | {\"from_leg\": {\"leg\": 0, \"pct\": 0.5}} relative "
    "to another leg. Add {\"ref\": \"entry\"} to measure from the spot at entry rather "
    "than the spot now."}

_LEG = {"type": "object", "required": ["side", "type", "strike"],
        "additionalProperties": False, "properties": {
    "side": {"type": "string", "enum": ["sell", "buy"]},
    "type": {"type": "string", "enum": ["CE", "PE"]},
    "qty": {"type": "integer", "minimum": 1, "maximum": 100,
            "description": "lots of THIS leg relative to the others. Unequal quantities "
                           "are how a ratio spread is written."},
    "expiry": {"type": "string", "enum": ["near", "next", "far"],
               "description": "'near' is the nearest expiry at entry; 'next' is the one "
                              "after, which is how a calendar or diagonal is written."},
    "strike": _STRIKE, "label": {"type": "string"}}}

STRATEGY_SCHEMA = {
    "type": "object", "required": ["legs"], "additionalProperties": False,
    "description": (
        "An open strategy: any legs, any rules. Use this whenever the idea does not fit a "
        "preset — ratio spreads, calendars, diagonals, jade lizards, broken wings, "
        "delta- or premium-selected strikes, per-leg stops, rolling a tested side, "
        "trailing stops, entry conditions on the credit available, and book-level rules "
        "like standing down after three losers."),
    "properties": {
        "name": {"type": "string"},
        "symbol": {"type": "string", "enum": ["NIFTY"]},
        "legs": {"type": "array", "minItems": 1, "maxItems": 12, "items": _LEG,
                 "description": "What to open. Leg order defines the indices rules use."},
        "entry": {"type": "object", "additionalProperties": False, "properties": {
            "cadence": {"type": "string", "enum": ["weekly", "daily", "monthly"],
                        "description": "weekly = one entry per weekly expiry; monthly = "
                                       "one per monthly expiry (the last of its calendar "
                                       "month); daily = one per session."},
            "time": {"type": "string",
                     "description": "ANY minute of the session, e.g. '09:20'. Not a grid."},
            "dte": {"type": "integer", "minimum": 0, "maximum": 60,
                    "description": "weekly/monthly only: days before expiry to enter. "
                                   "Defaults to 4 weekly, 21 monthly."},
            "max_dte": {"type": "integer", "minimum": 0, "maximum": 60,
                        "description": "daily only: skip sessions further than this from "
                                       "expiry."},
            "when": {"type": "object",
                     "description": "Optional gate on the cycle — the REASON for taking "
                                    "the trade. combined_premium is the credit on offer, "
                                    "so {\"combined_premium\": {\"gte\": 80}} means "
                                    "'only if I collect 80 points'. Market state is here "
                                    "too: "
                                    + ", ".join(sorted(strategy_mod.MARKET_FIELDS))
                                    + ". e.g. {\"vix\": {\"gte\": 15}}, "
                                    "{\"prev_day_move_pct\": {\"lte\": -1}}, "
                                    "{\"day_of_week\": {\"eq\": 1}} for Mondays. "
                                    "All are knowable before the session — none can see "
                                    "the day's own close."}}},
        "rules": {"type": "array", "maxItems": 24, "description":
            "Checked every minute, in order; the first match fires. Fields: "
            + ", ".join(sorted(strategy_mod.FIELDS)) +
            ". Actions: \"close\" | {\"close_legs\": [0]} | {\"open\": [leg,...]} | "
            "{\"roll\": {\"legs\": [0], \"to\": strike}} | "
            "{\"close_and_open\": {\"close\": [0], \"open\": [leg]}}.",
            "items": {"type": "object", "required": ["when", "then"],
                      "additionalProperties": False, "properties": {
                "when": {"type": "object"}, "then": {},
                "max_times": {"type": "integer", "minimum": 1, "maximum": 100},
                "label": {"type": "string"}}}},
        "exit": {"type": "object", "additionalProperties": False, "properties": {
            "time": {"type": "string",
                     "description": "hard square-off at this minute on the entry day."},
            "when": {"type": "object"}}},
        "max_adjustments": {"type": "integer", "minimum": 0, "maximum": 50,
                            "description": "how many times the rules may change the "
                                           "position in one trade. Default 4."},
        "portfolio": {"type": "object", "additionalProperties": False, "properties": {
            "stop_after_losses": {"type": "integer", "minimum": 1},
            "stop_after_drawdown_pct": {"type": "number"},
            "stop_after_profit_pct": {"type": "number"},
            "skip_after_loss": {"type": "boolean"},
            "max_trades": {"type": "integer", "minimum": 1}},
            "description": "Rules over the SEQUENCE of trades, which no per-trade "
                           "condition can express."},
        "resolution": {"type": "integer", "enum": [1, 5, 15],
                       "description": "minutes per rule check. 1 is the default and the "
                                      "honest one."},
        "period": {"type": "object", "additionalProperties": False, "properties": {
            "from": {"type": "string"}, "to": {"type": "string"}}},
    },
}

EITHER_SPEC = {"description":
    "Either a preset spec (structure + params) or an open strategy (legs + rules). Use "
    "the open form for anything the presets cannot say.",
    "oneOf": [SPEC_SCHEMA, STRATEGY_SCHEMA]}


TOOLS = [
    {
        "name": "run_backtest",
        "description": (
            "Backtest an Indian index option strategy on real 1-minute NIFTY options data. "
            "Returns P&L after real charges and slippage, return-on-margin, and an honesty "
            "panel: out-of-sample split, walk-forward folds, bootstrap interval, and a "
            "deflated Sharpe that accounts for how many variants you have already tried. "
            "Refuses windows too narrow to be meaningful, and reports no ratios below 30 "
            "trades. Two spec forms: a PRESET (structure + params) for the common shapes, "
            "or an OPEN STRATEGY (legs + rules) for anything else — any number of legs at "
            "any strikes on any expiry, strikes chosen by percent, points, premium or "
            "delta, entry at any minute, and rules that CHANGE the position while it is "
            "live (roll a tested leg, close one side, add a hedge, trail a stop) plus "
            "book-level rules like standing down after three losers. Nothing here is "
            "restricted by tier; a paid tier only widens the date window."),
        "inputSchema": {
            "type": "object", "required": ["spec"], "additionalProperties": False,
            "properties": {
                "spec": EITHER_SPEC,
                "lots": {"type": "integer", "minimum": 1, "maximum": 100},
                "detail": {
                    "type": "string", "enum": ["summary", "standard", "full"],
                    "description": (
                        "How much per-trade data to return. 'standard' (default) is the "
                        "equity curve, breakdowns and the first "
                        f"{STANDARD_TRADES} trades with their leg prices. 'full' returns "
                        f"up to {detail.MAX_TRADES_RETURNED} trades — ask for it when the "
                        "caller wants to audit or chart every trade. 'summary' returns "
                        "aggregates only, and is the cheapest to read.")},
            },
        },
        # Attach the inline chart view. The host fetches this template
        # directly by URI, so it never passes through the model's
        # context and costs nothing per call -- and the model cannot
        # forget to show it, because showing it is not its decision.
        "_meta": {"ui": {"resourceUri": appview.URI,
                         "visibility": ["model", "app"]}},
    },
    {
        "name": "describe_coverage",
        "description": (
            "What data is available: symbols, date range, resolution, structures, gates, "
            "biases, the cost model, and every known gap. Call this before building a spec."),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "explain_methodology",
        "description": (
            "How a result is produced and how to judge it: entry pricing, settlement, "
            "margin, slippage, the honesty rubric, and what each check can and cannot "
            "prove. Read this before trusting any backtest, including ours."),
        "inputSchema": {
            "type": "object", "additionalProperties": False,
            "properties": {"topic": {
                "type": "string",
                "enum": sorted(knowledge.TOPICS)}},
        },
    },
    {
        "name": "get_backtest",
        "description": ("Retrieve a previous backtest result by its id — honesty panel, "
                        "equity curve and per-trade detail, exactly as first computed."),
        "inputSchema": {
            "type": "object", "required": ["backtest_id"], "additionalProperties": False,
            "properties": {"backtest_id": {"type": "string"},
                           "detail": {"type": "string",
                                      "enum": ["summary", "standard", "full"]}},
        },
        # Attach the inline chart view. The host fetches this template
        # directly by URI, so it never passes through the model's
        # context and costs nothing per call -- and the model cannot
        # forget to show it, because showing it is not its decision.
        "_meta": {"ui": {"resourceUri": appview.URI,
                         "visibility": ["model", "app"]}},
    },
    {
        "name": "list_strategies",
        "description": (
            "Strategies from THIS account's history that held up under out-of-sample and "
            "walk-forward checks, not merely ones that made money. Ranked by worst "
            "walk-forward fold — consistency, not size. Call it to answer 'what has "
            "worked for me so far?' without re-running anything."),
        "inputSchema": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "order": {"type": "string", "enum": ["consistency", "health", "pnl"],
                          "description": ("'consistency' (default) sorts by worst "
                                          "walk-forward fold, then median fold. 'pnl' "
                                          "sorts by total P&L and is the ranking most "
                                          "likely to put an overfit at the top.")},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
    },
    {
        "name": "search",
        "description": ("Search what this service covers — symbols, dates, structures, "
                        "signals, methodology. Returns ids usable with fetch."),
        "inputSchema": {
            "type": "object", "required": ["query"], "additionalProperties": False,
            "properties": {"query": {"type": "string"}},
        },
    },
    {
        "name": "fetch",
        "description": "Fetch a document or backtest result by id, as returned by search.",
        "inputSchema": {
            "type": "object", "required": ["id"], "additionalProperties": False,
            "properties": {"id": {"type": "string"}},
        },
    },
    {
        "name": "submit_feedback",
        "description": (
            "Report a bug, request a feature, flag a data gap, or say what worked. Use "
            "this whenever the user expresses a problem with this service or wishes it "
            "did something it does not — do not just apologise to them, file it. If a "
            "backtest was involved, pass its backtest_id: that attaches the exact spec "
            "and the recent call trail so the issue can be reproduced without a reply. "
            "Tell the user you filed it and give them the returned id."),
        "inputSchema": {
            "type": "object", "required": ["title", "body"], "additionalProperties": False,
            "properties": {
                "title": {"type": "string", "maxLength": feedback_mod.MAX_TITLE,
                          "description": "One line naming the problem or request."},
                "body": {"type": "string", "maxLength": feedback_mod.MAX_BODY,
                         "description": ("What was expected, what happened, and any spec "
                                         "involved. Write it from the user's report, not "
                                         "from your own summary of it.")},
                "category": {"type": "string", "enum": sorted(feedback_mod.CATEGORIES),
                             "description": "Omit it and it will be inferred from the text."},
                "severity": {"type": "string", "enum": sorted(feedback_mod.SEVERITIES)},
                "backtest_id": {"type": "string",
                                "description": "The result this is about, if any."},
            },
        },
    },
    {
        "name": "build_report",
        "description": (
            "Turn a stored backtest into a finished, self-contained Stratify report — one "
            "HTML document with the honesty panel, equity and drawdown curves, "
            "walk-forward folds, the gross-to-net breakdown, a month grid and the trade "
            "table. PUBLISH THE RETURNED HTML VERBATIM AS AN ARTIFACT (Claude), a canvas "
            "document (ChatGPT, Gemini), or write it to a .html file (CLI clients). Do not "
            "rewrite it, summarise it into your own chart code, or regenerate the figures "
            "— the numbers in it came from the backtest, and anything you redraw from a "
            "table is a second source that can disagree with the first. It needs no "
            "network, no libraries and no build step, and it renders on light and dark. "
            "Use it when someone asks for a report, a summary they can keep, something to "
            "share, or an artifact."),
        "inputSchema": {
            "type": "object", "required": ["backtest_id"], "additionalProperties": False,
            "properties": {
                "backtest_id": {"type": "string",
                                "description": "From a previous run_backtest."},
                "format": {
                    "type": "string", "enum": ["artifact", "link", "full"],
                    "description": (
                        "'artifact' (default) returns the whole document to publish. "
                        "'link' returns only the hosted URL — far cheaper in tokens, and "
                        "the right choice when the user just wants to look at it rather "
                        "than keep it. 'full' builds the FULL STRATEGY REPORT and returns "
                        "its link: the strategy's rules in plain English, what it did to "
                        "₹10 lakh of capital, every trade plotted on a zoomable NIFTY "
                        "chart, the evidence panel, and the capital curve. Ask for it "
                        "whenever someone wants to really understand a strategy rather "
                        "than glance at it. It is rate limited.")},
                "capital": {"type": "integer", "minimum": 100000, "maximum": 100000000,
                            "description": "format 'full' only. Starting capital in "
                                           "rupees. Default 1,000,000."},
                "deploy_pct": {"type": "number", "minimum": 1, "maximum": 100,
                               "description": "format 'full' only. Percent of capital "
                                              "used as margin on any one trade. "
                                              "Default 10."},
                "risk_pct": {"type": "number", "minimum": 0.1, "maximum": 100,
                             "description": "format 'full' only. Size by RISK instead of "
                                            "margin: the percent of capital the trade is "
                                            "allowed to lose in its worst case (2 means "
                                            "'risk 2% per trade'). Only works where the "
                                            "position has a bounded worst case — a naked "
                                            "short does not, and the call is refused with "
                                            "that reason rather than sized off a guess. "
                                            "Overrides deploy_pct."},
            },
        },
    },
    {
        "name": "my_feedback",
        "description": ("Reports this account has filed, and where each one stands. Use "
                        "it to answer 'did that bug I reported ever get fixed?'."),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


class ToolError(Exception):
    """A refusal the user can act on. Always carries a reason."""


def _spec_hash(spec):
    if hasattr(spec, "legs"):
        # The open protocol hashes its whole declaration: every leg, every rule and every
        # bound changes the trades, and a field left out of the key would let genuinely
        # different searches count as one attempt against the deflated Sharpe.
        return _digest(json.dumps(spec.raw, sort_keys=True, default=str))
    return _preset_spec_hash(spec)


def _digest(material):
    """Hash the spec TOGETHER WITH the methodology version.

    The hash is the strategy book's dedup key, so without the version a re-run after a
    methodology change would overwrite the old row's statistics in place -- the user's
    recorded Sharpe would move with no event to point at. Including it means the two runs
    are two entries, each attributable to the model that produced it.

    It is also the deflated-Sharpe variant counter's key. Two runs of one spec under
    different methodologies genuinely are two trials of a hypothesis, so counting them
    separately is right there too.
    """
    return hashlib.sha256(
        f"m{methodology.VERSION}|{material}".encode()).hexdigest()[:16]


def _preset_spec_hash(spec):
    # Every field that changes the trades has to be in here. The hash is what the
    # deflated-Sharpe variant counter deduplicates on, so a field left out would let a
    # caller run genuinely different strategies that all count as one attempt -- which is
    # the multiple-comparisons problem the panel exists to report.
    return _digest(repr((
        spec.structure, sorted(spec.params.items()), spec.entry_time, spec.exit_time,
        spec.cadence, spec.max_dte, spec.gate, spec.bias, spec.date_from,
        spec.date_to)))


def run_backtest(arguments, context):
    raw = arguments.get("spec")
    try:
        lots = int(arguments.get("lots", 1))
    except (TypeError, ValueError):
        raise ToolError("lots must be a whole number") from None
    if not 1 <= lots <= 100:
        raise ToolError("lots must be between 1 and 100")
    level = arguments.get("detail", "standard")
    if level not in ("summary", "standard", "full"):
        raise ToolError("detail must be one of summary, standard, full")
    # TWO SHAPES, ONE TOOL. A spec with "legs" is the open protocol -- an explicit leg
    # list plus rules that may change the position while it is live. Anything else is the
    # v1 preset form, kept working unchanged because thousands of stored results and the
    # strategy book speak it. The engines agree exactly on the physics: an identical
    # position produces identical gross points, slippage, net points and margin in both.
    general = isinstance(raw, dict) and "legs" in raw
    try:
        if general:
            parsed = strategy_mod.parse(raw, window=spec_mod.window_for(
                context.get("tier")))
        else:
            parsed = spec_mod.parse(raw, tier=context.get("tier"))
    except (spec_mod.SpecError, strategy_mod.StrategyError) as exc:
        raise ToolError(str(exc)) from None

    # Usage is recorded by the dispatcher for every tool, including when this raises,
    # so a spec rejected late still costs the caller what it cost the box.
    t0 = time.process_time()
    wall0 = time.time()
    try:
        result = (simulate.run(parsed, lots=lots) if general
                  else backtest.run(parsed, lots=lots))
    except (spec_mod.SpecError, strategy_mod.StrategyError) as exc:
        raise ToolError(str(exc)) from None
    cpu = time.process_time() - t0

    summary = metrics.summarise(result)
    panel = honesty.panel(result, summary, api_key_id=context["key_id"])

    # Built at full width, then TRIMMED BEFORE IT IS STORED.
    #
    # The earlier design stored the full record whatever `detail` asked for, reasoning that
    # the report should always have everything. That quietly made the price meter
    # decorative: `detail='summary'` was metered at zero and stored every per-leg price
    # anyway, so the cheapest and most private-looking call was the cheapest way to harvest
    # -- read it straight back out of the unauthenticated report URL, or out of
    # get_backtest at detail='full'. An audit put the effective ceiling near 98,000 prints
    # an hour against a stated limit of 20,000.
    #
    # The rule now is simply: WHAT IS PERSISTED IS WHAT WAS RELEASED. The report renders
    # the stored record, so it can never show more than the caller asked for and paid for.
    rich = detail.build(result, max_trades=detail.MAX_TRADES_RETURNED)

    payload = {
        "spec": raw,
        "summary": summary,
        "honesty": panel,
        # A plain-language reading of THIS result, and explicitly what it does not
        # support. The panel reports numbers; this is the layer that stops a correct
        # number being carried further than it can bear -- which is the failure mode a
        # weaker model falls into every time.
        "interpretation": knowledge.interpretation(summary, panel, parsed),
        "cost_seconds": {"cpu": round(cpu, 3), "wall": round(time.time() - wall0, 3)},
        # WHICH MODEL PRODUCED THIS. Without it a stored figure cannot be attributed, and
        # a methodology improvement silently rewrites every number a user has recorded.
        # `slippage_basis` and `margin_calibrated_at` are in here because both can change
        # the answer with no code change at all -- see engine/methodology.py.
        "methodology": methodology.stamp(),
    }
    payload.update(rich)
    payload["data_release"] = _release_note(rich)
    payload = _trim(payload, level)
    spec_hash = _spec_hash(parsed)
    backtest_id, token = store.save_result(
        context["key_id"], json.dumps(raw), spec_hash, json.dumps(payload, default=str),
        methodology_json=json.dumps(payload["methodology"]))
    payload["backtest_id"] = backtest_id
    payload["report_url"] = f"{context['base_url']}/r/{token}"
    payload["report_note"] = (
        "The report page renders exactly what this call released — at detail='standard' "
        "the equity curve, the monthly bars and the returned trades; at detail='summary' "
        "the panel and aggregates with no prices at all. It is a shareable link, so it "
        "shows what you chose to disclose and nothing more.")
    # Keep it if it cleared the bar. The CHECKS are returned either way -- a near miss is
    # more useful than "not saved", because it names which piece of evidence was missing.
    qualifies, checks = book.evaluate(summary, panel)
    kept = {"qualified": qualifies, "checks": checks,
            "bar": book.BAR_DESCRIPTION["what_qualifies"],
            "why_not_just_pnl": book.BAR_DESCRIPTION["why_not_just_pnl"]}
    if qualifies:
        saved = store.record_strategy(book.entry_for(
            context["account_id"], backtest_id, spec_hash, raw, summary, panel, checks))
        kept.update(entry_id=saved["entry_id"], times_seen=saved["times_seen"],
                    note=("saved to this account's strategy book. list_strategies returns "
                          "it, ranked by worst walk-forward fold rather than by P&L"))
    else:
        failed = [k for k, v in checks.items() if not v["pass"]]
        kept["note"] = (f"not saved: {', '.join(failed)}. The book holds results that held "
                        f"up outside the data they were chosen on, not results that made "
                        f"money.")
    payload["strategy_book"] = kept

    # METERED ON WHAT ACTUALLY LEFT, not on what was computed. detail='summary' returns no
    # prices, so it must cost nothing against the release budget -- otherwise the cheapest,
    # most private way to ask a question would be charged as though it were the most
    # exposing one, and callers would learn to avoid it.
    context["price_points"] = (payload.get("data_release") or {}).get(
        "price_points_released", 0)
    return payload


def _trim(payload, level, keep=None):
    """Cut the payload down to what the caller asked for, BEFORE it is stored.

    This is a data-release control, not a formatting one. Everything downstream -- the
    report page, a later get_backtest, anyone the link is shared with -- reads the stored
    record, so trimming here is what makes the price meter mean anything.
    """
    if level == "full":
        return payload
    out = dict(payload)
    if level == "summary":
        for k in ("trades", "equity_curve", "trade_detail"):
            out.pop(k, None)
        # The release note travels with the response, so it has to describe THIS response.
        # Carrying the full-width count into a summary would claim a disclosure that did
        # not happen -- and would then be metered as one.
        out["data_release"] = dict(payload.get("data_release") or {},
                                   price_points_released=0,
                                   note="detail='summary' returned no prices at all")
        out["detail_note"] = (
            "detail='summary': aggregates only. Call again with detail='standard' for the "
            "equity curve and per-trade rows, or open report_url, which always has them.")
        return out
    keep = STANDARD_TRADES if keep is None else keep
    trades = payload.get("trades") or []
    if len(trades) > keep:
        out["trades"] = trades[:keep]
        note = dict(payload.get("trade_detail") or {})
        note["trades_returned"] = keep
        note["truncation"] = (
            f"showing the first {keep} of {note.get('trades_total', len(trades))} trades. "
            f"Every aggregate here -- P&L, drawdown, the equity curve, the breakdowns -- "
            f"covers ALL of them. For the rest, open report_url or call again with "
            f"detail='full'")
        out["trade_detail"] = note
        out["data_release"] = dict(payload.get("data_release") or {},
                                   price_points_released=_count_points(out["trades"]))
    return out


# ---------------------------------------------------------------- response digest

def _fmt_money(v):
    if v is None:
        return "n/a"
    a = abs(v)
    s = (f"Rs {a / 1e7:.2f}Cr" if a >= 1e7 else f"Rs {a / 1e5:.2f}L"
         if a >= 1e5 else f"Rs {a / 1000:.1f}k" if a >= 1000 else f"Rs {a:,.0f}")
    return ("-" if v < 0 else "") + s


def _attached(payload):
    """One honest sentence about what structuredContent on THIS response contains."""
    have = payload.get("trades") or []
    td = payload.get("trade_detail") or {}
    total = td.get("trades_total")
    parts = []
    if payload.get("equity_curve"):
        parts.append(f"the equity curve ({len(payload['equity_curve'].get('rows') or [])} points)")
    if payload.get("breakdown"):
        parts.append("monthly and other breakdowns")
    parts.append("every honesty component")
    if have:
        prices = "with leg prices" if td.get("prices_included") else "without leg prices"
        if total and len(have) < total:
            parts.append(f"the first {len(have)} of {total} trades {prices} "
                         f"(re-run with detail='full' for all {total})")
        else:
            parts.append(f"all {len(have)} trades {prices}")
    else:
        parts.append("NO per-trade rows - this response was summary-level "
                     "(re-run with detail='standard' or 'full' for them)")
    return ("ATTACHED in structuredContent: " + ", ".join(parts) +
            ". Read it for a specific number. Do not transcribe it.")


def digest(payload):
    """A short brief for the MODEL, carried as the text content block.

    WHY THIS EXISTS. A standard backtest payload is ~50 KB, and it used to be sent twice --
    once as text, once as structuredContent. 84% of it is the equity curve, the breakdowns
    and the trade rows: data the charts need and prose cannot use. A model handed 13,000
    tokens of tables narrates tables, which is exactly the wall of text users complained
    about, and it cost about 26,000 tokens per call to produce.

    So the text block is now this digest and structuredContent carries the full payload
    unchanged. Nothing is withheld -- a client that reads structuredContent still gets
    everything, storage is untouched, and the release meter is unaffected because a digest
    releases no prices. What changes is what the model reads FIRST, and therefore what it
    writes back.
    """
    if not isinstance(payload, dict) or "summary" not in payload:
        return None
    s, h = payload.get("summary") or {}, payload.get("honesty") or {}
    spec = payload.get("spec") or {}
    ratios = s.get("ratios") or {}
    oos = h.get("out_of_sample") or {}
    wf = h.get("walk_forward") or []
    mc = h.get("multiple_comparisons") or {}
    cd = h.get("cost_drag") or {}
    interp = payload.get("interpretation") or {}
    win = s.get("win_rate")
    rom = s.get("mean_return_on_margin")

    lines = [
        f"{spec.get('structure', '?')} on NIFTY weeklies, "
        f"{(s.get('period') or {}).get('from', '?')} to {(s.get('period') or {}).get('to', '?')}",
        f"VERDICT {h.get('verdict', '?')} - health {h.get('health_score', '?')}/100",
        "",
        f"  net {_fmt_money(s.get('total_pnl_rupees'))} over {s.get('n_trades', '?')} trades"
        f"   win rate {win * 100:.0f}%" if win is not None else "",
        f"  max drawdown {_fmt_money(s.get('max_drawdown_rupees'))}"
        f"   return on margin {rom * 100:.2f}% per trade" if rom is not None else "",
        f"  sharpe {ratios.get('sharpe', 'n/a')}   profit factor "
        f"{ratios.get('profit_factor', 'n/a')}   peak margin "
        f"{s.get('peak_margin_points', 'n/a')} pts/lot",
        f"  charges {_fmt_money(s.get('total_charges_rupees'))} - already deducted above",
        "",
        "EVIDENCE",
        f"  out of sample: {'held up' if oos.get('held_up') else 'DID NOT hold up'}"
        f" (chronological 70/30)",
        f"  walk-forward: {sum(1 for f in wf if f.get('profitable'))} of {len(wf)} folds "
        f"profitable, worst fold "
        f"{_fmt_money(min((f.get('pnl_rupees', 0) for f in wf), default=None))}",
        # The panel's own wording, verbatim. Paraphrasing it produced
        # "100% probability the edge is real" from a field that actually means
        # "survives having been searched for across 2 variants" -- the scope clause is
        # the entire content of the claim, and dropping it inverts the product's posture.
        f"  deflated sharpe: {mc.get('reading', 'n/a')}"
        f" [{mc.get('basis', '')}]",
        f"  costs: gross {cd.get('gross_points_before_costs', '?')} pts, net "
        f"{cd.get('net_points_after_costs', '?')} pts after charges and slippage",
    ]
    for x in (interp.get("reading") or [])[:2]:
        lines.append(f"  note: {x}")
    for x in (interp.get("do_not_conclude") or [])[:1]:
        lines.append(f"  do NOT conclude: {x}")

    # A daily-cadence year at detail='full' is ~160 KB. Clients differ in what they will
    # carry, and a silently dropped structuredContent looks exactly like a server bug --
    # which is how it was reported. Say the size so the failure is diagnosable.
    size = len(json.dumps(payload, default=str))
    big = ([f"NOTE: this result is {size // 1024} KB. If structuredContent did not reach "
            f"you, that is your client dropping an oversized field, not missing data - "
            f"re-run with detail='standard' for a smaller response, or open the report "
            f"link, which always has everything."] if size > 120_000 else [])

    lines += big + [
        "",
        # Describe what is ACTUALLY attached. This line used to promise "full detail ...
        # the trades with their leg prices" on every response, including ones trimmed to
        # 25 rows or to no trades at all -- and a caller who went looking for what it
        # promised, found less, and filed a bug was entirely right to.
        _attached(payload),
        "",
        "HOW TO ANSWER: lead with the verdict and the one number that decides it, in a few "
        "sentences. Do not reproduce the tables, the equity curve or the trade list as "
        "text - that is what the report is for.",
        # The URL is stated FIRST and unconditionally, because it works in every client
        # with no tool call at all. build_report is mentioned second and hedged: a client
        # that connected before the tool existed has a cached list without it, and
        # advertising a tool the caller cannot see is worse than not advertising one.
        f"REPORT: {payload.get('report_url', '')}",
        "  That link renders this exact result and needs no further call. Give it to the "
        "user.",
        "  DEFAULT BEHAVIOUR: publish the report rather than describing it. Do not ask "
        "first. Hold off only while sweeping variants, then publish once for the keeper.",
        f"  If your client lists a tool called build_report, calling it with backtest_id "
        f"{payload.get('backtest_id', '')} returns the same report as one self-contained "
        "HTML document to publish as an artifact the user can keep and share -- offer "
        "that too. If it is not in your tool list, your connection predates it: the link "
        "above still works, and reconnecting the connector will pick the tool up.",
    ]
    return "\n".join(l for l in lines if l is not None)


def _count_points(trades):
    """Real option prints in a set of returned rows.

    COUNTED ON PRICES, NOT ON LEGS. It counted `len(legs)` on the assumption that a leg
    always carries a price -- true until price-free rows began publishing strikes, which
    are rule OUTPUTS rather than quotes (the replay track publishes them on the same
    basis). Counting legs then billed a caller a hundred prints for a response that
    contained none.

    A settlement price is intrinsic value against the index and discloses nothing about
    the option table; a stop or target exit resolved in SQL has no per-leg price at all.
    Both are free. A traded exit -- a clock exit, a roll, a rule close -- is a real print
    and is counted.
    """
    traded_exit = {"TIME", "ROLL", "ADJUST", "RULE"}
    total = 0
    for t in trades:
        legs = t.get("legs") or []
        total += sum(1 for l in legs if "entry_price" in l)
        for l in legs:
            if "exit_price" not in l:
                continue
            if l.get("closed_by") in traded_exit or (
                    l.get("closed_by") is None and t.get("exit_reason") == "TIME"):
                total += 1
    return total


def _release_note(rich):
    """Say plainly what left the building. A service that returns prices and does not
    count them is asserting a policy rather than operating one."""
    return {
        "price_points_released": rich.get("price_points_released", 0),
        "what_this_is": (
            "the number of real option prints in this response — one per leg entered, "
            "plus one per leg squared off on the clock. Stop and target exits carry no "
            "per-leg price, and a settlement price is intrinsic value against the index, "
            "not an option quote."),
        "what_is_not_available": (
            "the chain. There is no endpoint that returns a strike this strategy did not "
            "trade, a minute it did not trade at, a range, a scan or an export. What you "
            "see is what the backtest touched."),
        "metered": ("counted per account alongside CPU-seconds, so the release rate is "
                    "measured rather than assumed"),
    }


def describe_coverage(arguments, context):
    """What THIS caller can reach, not what exists.

    The unauthenticated version of this endpoint reported the full seven-year history to
    anyone who asked, because it queried without binding a tier. That is not a data leak --
    no prices are returned -- but it advertises a window the caller cannot actually use,
    and the fix is the same one that enforces the boundary everywhere else: run as the
    tier's ClickHouse user and let the row policy answer.
    """
    tier = context.get("tier") or "free"
    token = engine_db.use_tier(tier)
    try:
        return _coverage(tier)
    finally:
        engine_db.current_user.reset(token)


def _coverage(tier):
    win_from, win_to = spec_mod.window_for(tier)
    coverage = engine_db.rows(
        f"SELECT min(trade_date) AS d0, max(trade_date) AS d1, "
        f"uniqExact(trade_date) AS days, uniqExact(expiry_date) AS expiries, "
        f"uniqExact(strike_price) AS strikes FROM {engine_db.DATABASE}.contract_day "
        f"WHERE trade_date BETWEEN %(a)s AND %(b)s",
        {"a": win_from, "b": win_to})[0]
    lots = engine_db.rows(
        f"SELECT lot_size, min(expiry_date) AS first, max(expiry_date) AS last "
        f"FROM {engine_db.DATABASE}.contract_spec "
        f"WHERE expiry_date BETWEEN %(a)s AND %(b)s "
        f"GROUP BY lot_size ORDER BY first",
        {"a": win_from, "b": win_to})
    return {
        "symbol": "NIFTY",
        "resolution": "1-minute",
        "tier": tier,
        # Published so a caller holding an older result can see the methodology moved,
        # and ask explain_methodology('changelog') what changed and whether it moved
        # numbers. Without this a client can only discover a change by noticing a figure
        # it recorded no longer reproduces.
        "methodology": methodology.stamp(),
        "from": str(coverage["d0"]), "to": str(coverage["d1"]),
        "tier_window": {"from": str(win_from), "to": str(win_to)},
        "trading_days": coverage["days"], "expiries": coverage["expiries"],
        "strikes": coverage["strikes"],
        "structures": {k: {"required": sorted(v["required"]),
                           "optional": sorted(v["optional"])}
                       for k, v in spec_mod.STRUCTURE_PARAMS.items()},
        "gates": signals.gate_names(),
        "biases": signals.bias_names(),
        "entry_times": list(spec_mod.ENTRY_TIMES),
        "exit_times": list(spec_mod.EXIT_TIMES),
        "cadences": {
            "weekly": ("one entry per expiry, on the day matching entry_dte — about 58 a "
                       "year in this window. The default."),
            "daily": ("one entry per trading session, on whichever expiry is nearest — "
                      "about 246 a year. Combine with exit_time for a same-session round "
                      "trip. entry_dte is refused; use max_dte instead."),
        },
        "exit_rules": {
            "clock": ("set exit_time to square off the same session at that time, priced "
                      "from the last real print in the bucket ending there"),
            "stop_target": ("sl_mult / sl_pct / tp_pct fire at a real print, and are "
                            "bounded by exit_time when one is set"),
            "settlement": ("intrinsic value against the mean of the index over the final "
                           "30 minutes, when nothing else fires"),
        },
        "returns": {
            "aggregates": "summary, honesty panel, equity curve, monthly and other breakdowns",
            "per_trade": ("entry and exit timestamps, the legs traded, and the price each "
                          "leg was entered and exited at — withheld below "
                          f"{detail.PRICE_DETAIL_MIN_TRADES} trades, and metered"),
            "never": ("the chain: no strike a strategy did not trade, no minute it did not "
                      "trade at, no range, no scan, no export"),
        },
        "lot_sizes": [{"lot_size": r["lot_size"], "first_expiry": str(r["first"]),
                       "last_expiry": str(r["last"])} for r in lots],
        "withheld": {
            "debit_spread": ("withheld from v1: a live SPAN margin check found a "
                             "margin-sizing bug that produced implausible returns"),
            "raw_data": ("no endpoint returns the option chain. A result carries the "
                         "prices of the contracts that backtest actually traded, so the "
                         "arithmetic can be checked; there is no way to ask for any other "
                         "strike, any other minute, a range or an export"),
        },
        "known_gaps": list(charges.DISCLOSED_GAPS) + [
            "Naked-short margin is a Fyers SPAN ratio calibrated today and applied "
            "historically; it widens in a shock, so return-on-margin on naked structures "
            "is optimistic.",
            "Slippage is modelled, not measured — see explain_methodology('slippage').",
            "3 of 246 days (2025-07-21, 2025-08-20, 2025-08-21) have spot backfilled by "
            "put-call parity where the index feed was short.",
        ],
    }


# Moved to knowledge.py, where it is also served as MCP resources and kept in one
# place with the ambient instructions and the per-result interpretation.
METHODOLOGY = knowledge.TOPICS


def explain_methodology(arguments, context):
    topic = arguments.get("topic", "overview")
    if topic not in METHODOLOGY:
        raise ToolError(f"unknown topic {topic!r}; available: {', '.join(METHODOLOGY)}")
    return {"topic": topic, "explanation": METHODOLOGY[topic],
            "all_topics": sorted(METHODOLOGY)}


def get_backtest(arguments, context):
    row = store.get_result(arguments["backtest_id"], key_id=context["key_id"])
    if row is None:
        raise ToolError(f"no backtest {arguments['backtest_id']!r} on this key")
    payload = json.loads(row["payload_json"])
    payload["backtest_id"] = row["backtest_id"]
    payload["report_url"] = f"{context['base_url']}/r/{row['report_token']}"
    level = arguments.get("detail", "standard")
    if level not in ("summary", "standard", "full"):
        raise ToolError("detail must be one of summary, standard, full")
    out = _trim(payload, level)
    # A record is TRIMMED BEFORE IT IS STORED -- that is the release control that makes
    # the price meter mean anything. The consequence is that detail='full' on a record
    # saved at 'standard' cannot return trades that were never persisted, and until now it
    # returned the shorter list in silence. A caller reasonably read that as data loss and
    # filed it as a bug. Say plainly what happened and how to actually get the rest.
    stored = payload.get("trade_detail") or {}
    have, total = len(payload.get("trades") or []), stored.get("trades_total")
    if level == "full" and total and have < total:
        out["detail_note"] = (
            f"detail='full' could not be honoured for this record. It was SAVED at a "
            f"narrower detail level, and this service stores exactly what it released -- "
            f"so only {have} of {total} trades were ever persisted and the rest do not "
            f"exist to return. This is a deliberate release control, not data loss. To get "
            f"all {total}, re-run the same spec with detail='full'; the aggregates here "
            f"(P&L, drawdown, equity curve, every breakdown) already cover all {total}.")
    # METER THE RE-READ. This handler set no price count at all, so the dispatcher recorded
    # zero -- and a stored result can be fetched again and again. Re-reading a release is
    # still a release: the prints cross the boundary each time, and an unmetered second
    # route defeats the meter on the first.
    context["price_points"] = _count_points(out.get("trades") or [])
    return out


def search(arguments, context):
    query = (arguments.get("query") or "").lower()
    hits = [{"id": f"doc:{k}", "title": f"Methodology — {k}", "text": v[:300]}
            for k, v in METHODOLOGY.items()
            if not query or query in k or query in v.lower()]
    hits.append({"id": "doc:coverage", "title": "Data coverage",
                 "text": "Symbols, dates, structures, gates, biases, lot sizes, known gaps."})
    for row in store.recent_results(context["key_id"], limit=10):
        hits.append({"id": row["backtest_id"], "title": "Backtest result",
                     "text": row["spec_json"][:300]})
    return {"results": hits[:20]}


def fetch(arguments, context):
    ident = arguments["id"]
    if ident == "doc:coverage":
        return describe_coverage({}, context)
    if ident.startswith("doc:"):
        return explain_methodology({"topic": ident[4:]}, context)
    return get_backtest({"backtest_id": ident}, context)


BOOK_FIELDS = ("entry_id", "backtest_id", "structure", "cadence", "n_trades",
               "pnl_rupees", "mean_rom", "sharpe", "profit_factor",
               "max_drawdown_rupees", "peak_margin_points", "health_score", "verdict",
               "deflated_sharpe", "oos_held_up", "folds_profitable", "folds_total",
               "worst_fold_rupees", "median_fold_rupees", "times_seen",
               "methodology_version")


def list_strategies(arguments, context):
    order = arguments.get("order", "consistency")
    if order not in ("consistency", "health", "pnl"):
        raise ToolError("order must be one of consistency, health, pnl")
    try:
        limit = int(arguments.get("limit", 25))
    except (TypeError, ValueError):
        raise ToolError("limit must be a whole number") from None
    if not 1 <= limit <= 100:
        raise ToolError("limit must be between 1 and 100")

    rows = store.list_strategies(context["account_id"], limit=limit, order=order)
    entries = []
    for r in rows:
        entry = {k: r.get(k) for k in BOOK_FIELDS}
        entry["spec"] = json.loads(r["spec_json"]) if r.get("spec_json") else None
        entry["first_seen"] = r.get("created_at")
        entry["last_seen"] = r.get("updated_at")
        # No report link here on purpose: the book stores summary statistics, not the
        # result's share token. get_backtest(backtest_id) resolves both, and routing
        # through it keeps one place that checks the result still belongs to this caller.
        entries.append(entry)
    return {
        "account_id": context["account_id"],
        "order": order,
        "n_entries": len(entries),
        "strategies": entries,
        "bar": book.BAR_DESCRIPTION,
        "how_to_read": (
            "worst_fold_rupees is the ranking key: it is the WORST of the walk-forward "
            "folds, so an entry with a positive one made money in every fold. Use "
            "get_backtest with an entry's backtest_id for its full result and honesty "
            "panel. An entry is a surviving hypothesis, not a recommendation."),
    }


def submit_feedback(arguments, context):
    """File one report. Rejections are ToolErrors so the model sees a reason and can fix
    the call, rather than a generic failure it will retry verbatim."""
    try:
        result = feedback_mod.submit(
            account_id=context["account_id"], tier=context.get("tier", "free"),
            title=arguments.get("title"), body=arguments.get("body"),
            category=arguments.get("category"), severity=arguments.get("severity"),
            backtest_id=arguments.get("backtest_id"), source="mcp")
    except feedback_mod.FeedbackError as exc:
        raise ToolError(str(exc)) from exc
    filed = ("Filed." if result["is_new"] else
             "You had already reported this; the existing item was updated and its report "
             "count incremented.")
    return {
        "feedback_id": result["feedback_id"],
        "filed_as": {"category": result["category"], "severity": result["severity"],
                     "tags": result["tags"]},
        "is_new": result["is_new"],
        "message": (
            f"{filed} Reference {result['feedback_id']}. It was categorised as "
            f"'{result['category']}' at severity '{result['severity']}'. The spec and your "
            f"recent calls were attached automatically, so no further detail is needed "
            f"unless you have some. Ask with my_feedback to see where it stands."),
        "tell_the_user": ("Give them the feedback_id. Do not promise a timeline — this "
                          "files a report, it does not schedule a fix."),
    }


# A full report re-runs the strategy and pulls seven years of index candles, so it costs
# real work where the other formats cost none. It is metered separately for that reason --
# and because it is the one output a caller might be tempted to loop over.
FULL_REPORTS_PER_HOUR = 10


def _full_report(arguments, context, row, payload, backtest_id):
    """The standard strategy report: rules, capital, every trade on the index, evidence."""
    allowed, used = store.full_report_quota(context["account_id"], FULL_REPORTS_PER_HOUR)
    if not allowed:
        raise ToolError(
            f"Full-report limit reached ({FULL_REPORTS_PER_HOUR} an hour on this account; "
            f"{used} used). A full report re-runs the strategy and loads the whole index "
            f"series, so it is metered separately from ordinary backtests. The report from "
            f"any earlier backtest is still at its own link, and detail='standard' "
            f"backtests are unaffected.")

    capital = int(arguments.get("capital") or sizing_default_capital())
    deploy = float(arguments.get("deploy_pct") or 10.0) / 100.0
    risk = arguments.get("risk_pct")
    risk = None if risk in (None, "") else float(risk) / 100.0

    # Re-run the stored spec to get EVERY trade. The stored payload was trimmed to what was
    # released, and a capital curve built from the first 300 of 380 trades silently ends
    # the strategy eighteen months early -- which looks like a result rather than a bug.
    spec = spec_mod.parse(json.loads(row["spec_json"]), tier=context.get("tier", "free"))
    result = backtest.run(spec, lots=1)
    chart_rows = detail.rows_for_chart(result)
    if not chart_rows:
        raise ToolError("that backtest produced no trades, so there is nothing to plot")

    period = (payload.get("summary") or {}).get("period") or {}
    candles = index_series.daily(period.get("from") or str(spec.date_from),
                                 period.get("to") or str(spec.date_to))
    url = f"{context['base_url']}/r/{row['report_token']}"
    html_doc = fullreport.render(payload, chart_rows, candles, backtest_id,
                                 capital=capital, deploy=deploy, report_url=url)
    token = store.save_full_report(backtest_id, context["account_id"], html_doc)
    full_url = f"{context['base_url']}/report/{token}"
    try:
        sized = sizing.apply(chart_rows, capital=capital, deploy=deploy, risk_pct=risk)
    except sizing.SizingError as exc:
        # A refusal the caller can act on, not a 500. The report itself is already built
        # and still at its link; only the sizing view could not be produced.
        raise ToolError(str(exc)) from exc
    return {
        "backtest_id": backtest_id,
        "full_report_url": full_url,
        "message": ("The full strategy report is ready. GIVE THE USER THIS LINK — it is "
                    "the whole strategy on one page and it is not something to summarise."),
        "contains": ["the strategy's rules in plain English",
                     f"what it did to {capital:,} rupees of capital",
                     f"all {len(chart_rows)} trades plotted on a zoomable NIFTY chart",
                     "the evidence panel", "the capital and drawdown curve",
                     "the trade table with leg prices"],
        "headline": {
            "starting_capital": sized["starting_capital"],
            "ending_capital": sized["ending_capital"],
            "return_pct": sized["return_pct"],
            "cagr_pct": sized["cagr_pct"],
            "max_drawdown_pct": sized["max_drawdown_pct"],
            "trades": sized["trades_taken"],
            "deploy_pct": sized["deploy_pct"],
        },
        # NOT "quota": the dispatcher overwrites that key with the account-wide
        # snapshot after every handler returns, so anything a handler puts there is lost.
        "full_report_quota": {"per_hour": FULL_REPORTS_PER_HOUR,
                              "used_this_hour": used + 1},
        "bytes": len(html_doc),
        "why_a_link_not_a_document": (
            "It embeds a charting library and seven years of index candles — roughly "
            "75,000 tokens if it crossed this conversation. Served over HTTP it is gzipped "
            "and cached, and it can be forwarded to someone who has no API key."),
    }


def sizing_default_capital():
    from . import sizing as _s
    return _s.DEFAULT_CAPITAL


def my_feedback(arguments, context):
    rows = store.feedback_for_account(context["account_id"], limit=25)
    return {
        "n_reports": len(rows),
        "reports": [{"feedback_id": r["feedback_id"], "filed_at": r["ts"],
                     "title": r["title"], "category": r["category"],
                     "severity": r["severity"], "status": r["status"],
                     "times_reported": r["times_seen"]} for r in rows],
        "statuses_mean": {
            "new": "received, not yet looked at",
            "reopened": "was closed, then reported again",
            "triaged": "read and categorised, not started",
            "in_progress": "being worked on",
            "fixed": "shipped",
            "wontfix": "deliberately not doing it",
            "duplicate": "merged into another report"},
    }


# The document is large. Returning it is deliberate -- see the tool description -- but a
# caller who only wants to look at the report should not pay for the bytes, which is what
# format='link' is for.
def build_report(arguments, context):
    backtest_id = (arguments.get("backtest_id") or "").strip()
    row = store.get_result(backtest_id)
    if row is None or store.key_owner(row["key_id"]) != context["account_id"]:
        # Same wording whether it does not exist or belongs to someone else: a different
        # message for each turns this into an oracle for guessing other people's ids.
        raise ToolError(f"No backtest {backtest_id!r} on this account.")
    payload = json.loads(row["payload_json"])
    url = f"{context['base_url']}/r/{row['report_token']}"
    if arguments.get("format") == "link":
        return {"backtest_id": backtest_id, "report_url": url,
                "message": "The hosted report renders exactly what that backtest released."}

    if arguments.get("format") == "full":
        return _full_report(arguments, context, row, payload, backtest_id)

    # What is drawn is bounded by what was released, so a summary-detail backtest yields a
    # report with no per-trade section -- the document cannot become a richer channel than
    # the call that produced it.
    document = artifact.render(payload, backtest_id, report_url=url)
    detail = payload.get("trade_detail") or {}
    return {
        "backtest_id": backtest_id,
        "report_url": url,
        "mime_type": "text/html",
        "document": document,
        "bytes": len(document),
        "contains": {
            "honesty_panel": True,
            "charts": ["equity and drawdown", "walk-forward folds", "gross to net",
                       "month grid", "trade P&L distribution", "exit reasons"],
            "trades_shown": detail.get("trades_returned", 0),
            "per_leg_prices": bool(detail.get("prices_included")),
        },
        "how_to_publish": {
            "claude": ("Create an Artifact with type text/html and this exact document as "
                       "its content. Do not edit it."),
            "chatgpt": "Open it in Canvas as an HTML document, unedited.",
            "gemini": "Open it in Canvas as an HTML document, unedited.",
            "cli_or_api": ("Write it to a .html file and tell the user the path. It opens "
                           "in any browser with no server."),
            "constraints_already_met": ("Self-contained: no external requests, no CDN, no "
                                        "fonts to fetch. Theme-aware. Safe to publish as-is "
                                        "in a sandboxed artifact."),
        },
        "tell_the_user": ("Say what the report concluded — the verdict and the one number "
                          "that matters — rather than describing the document. They can "
                          "see the document."),
    }


HANDLERS = {
    "run_backtest": run_backtest,
    "build_report": build_report,
    "submit_feedback": submit_feedback,
    "my_feedback": my_feedback,
    "list_strategies": list_strategies,
    "describe_coverage": describe_coverage,
    "explain_methodology": explain_methodology,
    "get_backtest": get_backtest,
    "search": search,
    "fetch": fetch,
}
