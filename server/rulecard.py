"""The strategy's own rules, in English, at the top of its report.

WHY THIS IS THE FIRST THING ON THE PAGE. A report that opens with a P&L figure asks the
reader to trust a number before they know what produced it. Opening with the rules inverts
that: entry, exit, target, stop and adjustments are stated first, so every number below is
read as the consequence of something the reader has already seen.

IT PARSES THROUGH THE ENGINE, DELIBERATELY. The spec on a stored payload is raw JSON, and
the tempting shortcut is to walk that dict and render it. That would be a SECOND reader of
the protocol, free to drift from the one that ran the backtest -- and the failure mode is
silent: a report describing a stop the engine never applied. So the raw spec goes through
`strategy.parse` (open protocol) or `spec.parse` (preset) and this module renders the
PARSED form. If parsing fails the card says so instead of inventing a description.

WHAT COUNTS AS A STOP. The protocol has no `stop_loss` keyword -- a stop is a rule whose
condition is a loss threshold and whose action is `close`. Classification therefore happens
here, by reading the condition, and the rules that resist classification fall through to
"other rules" rather than being forced into a bucket they do not belong in.
"""
import html

from engine import spec as spec_mod, strategy

# The trader's words for each testable quantity, plus the unit its bound carries. The
# engine's FIELDS dict explains each field to a model; this is the same set said to a
# person, in a phrase that reads inside a sentence.
FIELD_WORDS = {
    "pnl_pts":            ("position P&L", " pts"),
    "pnl_rupees":         ("position P&L", ""),
    "pnl_pct_of_credit":  ("P&L against the credit taken in", "%"),
    "pnl_pct_of_max":     ("P&L against the most it could make", "%"),
    "combined_premium":   ("cost to close the position", " pts"),
    "credit_kept_pct":    ("credit still unspent", ""),
    "leg_mark":           ("the leg's price", " pts"),
    "leg_mark_mult":      ("the leg's price against what it opened at", "×"),
    "leg_mark_delta":     ("the leg's move", " pts"),
    "leg_pnl_pts":        ("the leg's own P&L", " pts"),
    "spot":               ("the index", ""),
    "spot_move_pct":      ("the index's move since entry", "%"),
    "spot_move_pts":      ("the index's move since entry", " pts"),
    "spot_beyond_strike": ("how far the index is past the strike", " pts"),
    "vix":                ("India VIX", ""),
    "vix_prev_close":     ("VIX at yesterday's close", ""),
    "vix_change_pct":     ("VIX's move today", "%"),
    "prev_day_move_pct":  ("yesterday's index move", "%"),
    "gap_pct":            ("this morning's gap", "%"),
    "realised_vol_20d":   ("20-day realised volatility", "%"),
    "day_of_week":        ("the weekday", ""),
    "time":               ("the clock", ""),
    "minutes_held":       ("time held", " min"),
    "dte":                ("days to expiry", ""),
    "drawdown_from_peak": ("points given back from the best mark", " pts"),
    "runup_from_trough":  ("points recovered from the worst mark", " pts"),
    "adjustments_done":   ("adjustments already fired", ""),
}

def _indicator_words(field):
    """Prose for the parametric indicator family, which cannot live in FIELD_WORDS
    because its names are generated. Falls back to the raw name for anything else."""
    from engine import strategy as _strategy
    try:
        spec = _strategy.indicator_field(field)
    except _strategy.StrategyError:
        spec = None
    if not spec:
        return (field.replace("_", " "), "")
    k, n, m = spec["kind"], spec["n"], spec["m"]
    if k == "rsi":
        return (f"the index's {n}-day RSI", "")
    if k == "close_vs_sma":
        return (f"yesterday's close against the {n}-day average", "%")
    if k == "close_vs_ema":
        return (f"yesterday's close against the {n}-day EMA", "%")
    if k == "ema_cross":
        return (f"the {n}-day EMA against the {m}-day", "%")
    if k == "sma_cross":
        return (f"the {n}-day average against the {m}-day", "%")
    return (field, "")


CMP_WORDS = {"gt": "goes above", "gte": "reaches", "lt": "falls below",
             "lte": "falls to", "eq": "is", "between": "is between"}

# Fields that are already a signed MOVE. "yesterday's index move falls to -1%" is the
# generic template applied to something that has its own vocabulary; a trader says
# "yesterday the index fell 1% or more".
MOVE_FIELDS = {"prev_day_move_pct": "the index", "gap_pct": "the market",
               "spot_move_pct": "the index", "spot_move_pts": "the index",
               "vix_change_pct": "VIX"}

DAYS = {1: "Monday", 2: "Tuesday", 3: "Wednesday", 4: "Thursday", 5: "Friday",
        6: "Saturday", 7: "Sunday"}

# Fields whose value going UP is the position going WRONG. `leg_mark_mult >= 1.6` is a
# short leg tripling in price, which is a stop however it is spelled; without this it
# would classify as neither target nor stop and fall through to "other".
ADVERSE_UP = {"leg_mark_mult", "leg_mark", "leg_mark_delta", "combined_premium",
              "drawdown_from_peak", "spot_beyond_strike"}


def _e(v):
    return html.escape("" if v is None else str(v))


def where_words(st):
    """A strike selector, said with a DIRECTION rather than a sign.

    `strategy._strike_words` renders a put at -1.5% as "-1.5% from spot", which is exact
    and reads badly: below is not a negative number to a person, it is a direction. Every
    other selector is delegated so this stays a formatting difference, not a second
    reading of the protocol.
    """
    k = st.get("kind")
    if k == "pct_offset":
        v = float(st["value"])
        if v == 0:
            return "at the money"
        return f"{abs(v):g}% {'above' if v > 0 else 'below'} spot"
    if k == "points_offset":
        v = float(st["value"])
        if v == 0:
            return "at the money"
        return f"{abs(v):.0f} points {'above' if v > 0 else 'below'} spot"
    if k == "atm":
        return "at the money"
    if k == "strike":
        return f"at {float(st['value']):.0f}"
    if k == "from_leg":
        n = int(st["leg"]) + 1
        return (f"{abs(float(st['pct'])):g}% beyond leg {n}" if st.get("pct") is not None
                else f"{abs(float(st['points'])):.0f} points beyond leg {n}")
    return strategy._strike_words(st)


def legs_named(v):
    """[0] -> 'leg 1'; [0, 1] -> 'legs 1 and 2'; 'all' -> 'every leg'. One-based, because
    the report numbers legs the way it lists them."""
    if v == "all" or v is None:
        return "every leg"
    n = [int(x) + 1 for x in v]
    if len(n) == 1:
        return f"leg {n[0]}"
    return "legs " + ", ".join(str(x) for x in n[:-1]) + f" and {n[-1]}"


def action_words(a):
    d = a["do"]
    if d == "close":
        return "close the position"
    if d == "close_legs":
        return f"close {legs_named(a['legs'])}"
    if d == "open":
        return f"open {len(a['legs'])} more leg{'s' if len(a['legs']) != 1 else ''}"
    if d == "roll":
        return f"roll {legs_named(a['legs'])} to {where_words(a['to'])}"
    return (f"close {legs_named(a['legs'])} and open {len(a['open'])} "
            f"more leg{'s' if len(a['open']) != 1 else ''}")


def _num(v):
    """Bounds arrive as floats. 70.0 should read as 70, and 1.6 as 1.6."""
    if isinstance(v, (list, tuple)):
        return " and ".join(_num(x) for x in v)
    f = float(v)
    return str(int(f)) if f == int(f) else f"{f:g}"


# ------------------------------------------------------------------- conditions

def cond_words(c, short=False):
    """A parsed condition as a sentence fragment: 'the loss reaches 80% of the credit'.

    `short=True` returns the compact form used in the two-line gloss and on the criterion
    chips -- '80% of credit' rather than the whole clause.
    """
    if c is None:
        return None
    op = c.get("op")
    if op in ("all", "any"):
        joiner = " and " if op == "all" else " or "
        return joiner.join(cond_words(x, short) for x in c["of"])
    if op == "not":
        return "it is not the case that " + cond_words(c["of"][0])

    field, cmp_, bound = c["field"], c["cmp"], c["bound"]
    leg = c.get("leg")

    # --- the cases with their own vocabulary --------------------------------
    if field == "day_of_week" and cmp_ == "eq":
        d = DAYS.get(int(bound), str(int(bound)))
        return d if short else f"it is {d}"
    if field == "time":
        fmt = (strategy.fmt_minute(int(bound)) if cmp_ != "between"
               else f"{strategy.fmt_minute(int(bound[0]))}–"
                    f"{strategy.fmt_minute(int(bound[1]))}")
        return fmt if short else f"the clock {CMP_WORDS[cmp_]} {fmt}"
    if field == "minutes_held":
        h = _held(bound)
        return h if short else f"it has been held {h}"
    if field == "dte":
        return (f"{_num(bound)} DTE" if short else
                f"there {'is' if float(bound) == 1 else 'are'} {_num(bound)} "
                f"day{'s' if float(bound) != 1 else ''} to expiry")
    if field == "leg_mark_mult":
        n = f"leg {leg + 1}" if leg is not None else "the leg"
        return (f"{_num(bound)}× on {n}" if short else
                f"{n}'s price {CMP_WORDS[cmp_]} {_num(bound)}× what it opened at")
    if field in MOVE_FIELDS and cmp_ in ("lte", "lt", "gte", "gt"):
        who, b = MOVE_FIELDS[field], float(bound)
        unit = "%" if field.endswith("_pct") else " points"
        fell = (cmp_ in ("lte", "lt")) == (b <= 0)
        verb = "fell" if (b < 0 or (b == 0 and cmp_ in ("lte", "lt"))) else "rose"
        if short:
            return f"{who} {verb} {_num(abs(b))}{unit}"
        tail = "or more" if fell else "or less"
        return f"{who} {verb} {_num(abs(b))}{unit} {tail}"

    # --- P&L, said as profit or as loss -------------------------------------
    base = ("credit" if field == "pnl_pct_of_credit"
            else "the most it could make" if field == "pnl_pct_of_max" else None)
    if field.startswith("pnl_") and base and cmp_ in ("lte", "lt") and float(bound) < 0:
        b = _num(abs(float(bound)))
        return f"{b}% of {base}" if short else f"the loss reaches {b}% of {base}"
    if field.startswith("pnl_") and base and cmp_ in ("gte", "gt") and float(bound) > 0:
        b = _num(bound)
        return f"{b}% of {base}" if short else f"profit reaches {b}% of {base}"

    words, unit = FIELD_WORDS.get(field) or _indicator_words(field)
    legs = f" (leg {leg + 1})" if leg is not None else ""
    if short:
        return f"{_num(bound)}{unit}"
    return f"{words}{legs} {CMP_WORDS[cmp_]} {_num(bound)}{unit}"


def _held(minutes):
    """90 -> '90 minutes'; 390 -> '6h 30m'; 1440 -> '1 day'."""
    m = int(float(minutes))
    if m < 120:
        return f"{m} minutes"
    if m < 1440:
        return f"{m // 60}h" + (f" {m % 60}m" if m % 60 else "")
    d = m / 1440
    return f"{d:g} day{'s' if d != 1 else ''}"


def _is_profit_trigger(c):
    """Does this condition fire because the position is WINNING?"""
    if c is None or c.get("op") != "cmp":
        return False
    f, cmp_, b = c["field"], c["cmp"], c["bound"]
    if f.startswith("pnl_") or f in ("credit_kept_pct", "runup_from_trough"):
        if cmp_ in ("gte", "gt") and float(b) > 0:
            return True
    if f == "combined_premium" and cmp_ in ("lte", "lt"):
        return True          # bought the position back cheap: that is a target
    return False


def _is_loss_trigger(c):
    """Does this condition fire because the position is LOSING?"""
    if c is None or c.get("op") != "cmp":
        return False
    f, cmp_, b = c["field"], c["cmp"], c["bound"]
    if f.startswith("pnl_") and cmp_ in ("lte", "lt") and float(b) < 0:
        return True
    if f in ADVERSE_UP and cmp_ in ("gte", "gt"):
        return True
    return False


def _is_clock_trigger(c):
    if c is None or c.get("op") != "cmp":
        return False
    return c["field"] in ("minutes_held", "time", "dte")


# ------------------------------------------------------------------- open protocol

def _from_strategy(st, raw):
    legs = [{"side": l["side"], "qty": l["qty"], "type": l["type"],
             "where": where_words(l["strike"]), "expiry": l["expiry"]}
            for l in st.legs]

    entry = [{"text": ("Every session" if st.cadence == "daily"
                       else f"Every {st.cadence} expiry, {st.entry_dte} "
                            f"day{'s' if st.entry_dte != 1 else ''} out"),
              "short": "daily" if st.cadence == "daily" else f"{st.entry_dte}d out"},
             {"text": f"Entered at {strategy.fmt_minute(st.entry_minute)}",
              "short": strategy.fmt_minute(st.entry_minute)}]
    if st.entry_when is not None:
        entry.append({"text": "Only when " + cond_words(st.entry_when),
                      "short": cond_words(st.entry_when, short=True)})

    target, stop, adjust, other, timed = [], [], [], [], []
    for r in st.rules:
        c, a = r["when"], r["then"]
        cap = (f" (up to {r['max_times']} times)"
               if r.get("max_times", 1) not in (1, None) else "")
        short = cond_words(c, short=True)
        if a["do"] == "close":
            if _is_profit_trigger(c):
                target.append({"text": f"Closed when {cond_words(c)}{cap}",
                               "short": short})
            elif _is_loss_trigger(c):
                stop.append({"text": f"Closed when {cond_words(c)}{cap}", "short": short})
            elif _is_clock_trigger(c):
                timed.append({"text": f"Closed once {cond_words(c)}{cap}", "short": short})
            else:
                other.append({"text": f"Closed when {cond_words(c)}{cap}", "short": short})
        else:
            adjust.append({"text": f"When {cond_words(c)}, {action_words(a)}{cap}",
                           "short": short})

    exit_ = list(timed)
    if st.exit_minute is not None:
        exit_.insert(0, {"text": f"Squared off at {strategy.fmt_minute(st.exit_minute)}",
                         "short": strategy.fmt_minute(st.exit_minute)})
    if not exit_:
        exit_.append({"text": ("Held to expiry" if not (target or stop or other)
                               else "Held to expiry if nothing above fires"),
                      "short": "expiry"})

    return {"legs": legs, "entry": entry, "exit": exit_, "target": target,
            "stop": stop, "adjust": adjust, "other": other,
            "shape": strategy.shape_name(st.legs),
            "max_adjustments": st.max_adjustments if adjust else None,
            "is_credit": st.is_credit, "protocol": "open"}


# ------------------------------------------------------------------- preset

# The preset structures, said as leg lists. backtest._build_legs is the authority; these
# mirror it so a preset report shows the same position an open-protocol one would.
def _preset_legs(structure, p):
    off, wid = p.get("pct_offset"), p.get("pct_width")
    d = p.get("direction", "CE")
    up = lambda v: ("at the money" if not v else f"{abs(float(v)):g}% above spot")
    dn = lambda v: ("at the money" if not v else f"{abs(float(v)):g}% below spot")
    wide = (abs(float(off or 0)) + abs(float(wid or 0)))
    if structure == "short_strangle":
        return [_L("sell", "CE", up(off)), _L("sell", "PE", dn(off))]
    if structure == "credit_spread":
        near, far = (up, up) if d == "CE" else (dn, dn)
        return [_L("sell", d, near(off)), _L("buy", d, far(wide))]
    if structure == "iron_condor":
        return [_L("sell", "CE", up(off)), _L("buy", "CE", up(wide)),
                _L("sell", "PE", dn(off)), _L("buy", "PE", dn(wide))]
    if structure == "iron_fly":
        return [_L("sell", "CE", "at the money"), _L("buy", "CE", up(wid)),
                _L("sell", "PE", "at the money"), _L("buy", "PE", dn(wid))]
    if structure == "long_option":
        return [_L("buy", d, up(off) if d == "CE" else dn(off))]
    return []


def _L(side, typ, where):
    return {"side": side, "qty": 1, "type": typ, "where": where, "expiry": "near"}


def _from_spec(sp, raw):
    p = sp.params or {}
    ndb = getattr(sp, "entry_days_before", None)
    if sp.cadence == "daily":
        when, short = "Every session", "daily"
    elif ndb is not None:
        when = ("Every weekly expiry, on expiry day itself" if ndb == 0 else
                f"Every weekly expiry, {ndb} trading session{'s' if ndb != 1 else ''} before it")
        short = f"T-{ndb}"
    elif sp.entry_dte is not None:
        when = f"Every weekly expiry, {sp.entry_dte} day{'s' if sp.entry_dte != 1 else ''} out"
        short = f"{sp.entry_dte}d out"
    else:
        when, short = "Every weekly expiry", "weekly"
    entry = [{"text": when, "short": short},
             {"text": f"Entered at {sp.entry_time}", "short": str(sp.entry_time)}]
    if sp.gate and sp.gate != "always":
        g = sp.gate.replace("_", " ")
        entry.append({"text": f"Only when the {g} gate is open", "short": g})
    if getattr(sp, "overlay", None):
        cut = sp.overlay_cutoff
        entry.append({"text": f"Skipped when 20-day realised volatility is above {cut:g}%",
                      "short": f"vol \u2264 {cut:g}%"})
    if sp.bias and sp.bias != "neutral":
        entry.append({"text": f"Directional bias: {sp.bias}", "short": str(sp.bias)})

    target, stop, exit_ = [], [], []
    if sp.exit_time:
        exit_.append({"text": f"Squared off at {sp.exit_time}", "short": str(sp.exit_time)})
    # The preset exits are hard-coded per structure in backtest._exit_trigger. Read the
    # same params it reads, so the card cannot claim a stop the engine does not apply.
    if sp.structure in ("short_strangle", "credit_spread") and p.get("sl_mult") is not None:
        v = _num(float(p["sl_mult"]) * 100)
        stop.append({"text": f"Closed when the loss reaches {v}% of the credit taken in",
                     "short": f"{v}% of credit"})
    if sp.structure == "credit_spread" and p.get("tp_pct") is not None:
        v = _num(float(p["tp_pct"]) * 100)
        target.append({"text": f"Closed when profit reaches {v}% of the credit taken in",
                       "short": f"{v}% of credit"})
    if sp.structure == "long_option":
        if p.get("sl_pct") is not None:
            v = _num(float(p["sl_pct"]) * 100)
            stop.append({"text": f"Closed when the premium falls {v}% below what was paid",
                         "short": f"−{v}% on premium"})
        if p.get("tp_pct") is not None:
            v = _num(float(p["tp_pct"]) * 100)
            target.append({"text": f"Closed when the premium rises {v}% above what was paid",
                           "short": f"+{v}% on premium"})
    if not exit_:
        exit_.append({"text": ("Held to expiry" if not (target or stop)
                               else "Held to expiry if nothing above fires"),
                      "short": "expiry"})

    return {"legs": _preset_legs(sp.structure, p), "entry": entry, "exit": exit_,
            "target": target, "stop": stop, "adjust": [], "other": [],
            "shape": sp.structure, "max_adjustments": None,
            "is_credit": sp.is_credit, "protocol": "preset"}


# ------------------------------------------------------------------- entry point

def describe(raw):
    """-> the card, or a card carrying `error` when the spec will not parse."""
    blank = {"legs": [], "entry": [], "exit": [], "target": [], "stop": [],
             "adjust": [], "other": [], "shape": "", "max_adjustments": None,
             "is_credit": None, "protocol": None}
    if not isinstance(raw, dict) or not raw:
        return dict(blank, error="no strategy specification was stored with this result")
    try:
        card = (_from_strategy(strategy.parse(raw), raw) if "legs" in raw
                else _from_spec(spec_mod.parse(raw), raw))
    except Exception as exc:                                   # noqa: BLE001
        # A stored spec that no longer parses is a real event -- the protocol moved under
        # a saved result. Say which, rather than rendering an empty card.
        return dict(blank, error=f"this specification no longer parses: {exc}")
    card["title"] = title(card["shape"])
    card["gloss"] = gloss(card)
    card["error"] = None
    return card


def title(shape):
    named = {"naked_short": "Naked short option",
             "long_option": "Long option",
             "short_strangle": "Short strangle",
             "long_strangle": "Long strangle",
             "short_straddle": "Short straddle",
             "long_straddle": "Long straddle",
             "vertical_spread": "Vertical spread",
             "credit_spread": "Credit spread",
             "iron_condor": "Iron condor",
             "iron_fly": "Iron fly",
             "calendar": "Calendar spread",
             "diagonal": "Diagonal spread",
             "ratio": "Ratio spread"}
    if shape in named:
        return named[shape]
    # shape_name falls back to custom_6_leg / multi_expiry_2_leg for anything it does not
    # recognise. "Custom 6 leg" is a slug with a capital letter on it; say it in words.
    words = {2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six", 7: "Seven",
             8: "Eight", 9: "Nine", 10: "Ten", 11: "Eleven", 12: "Twelve"}
    for prefix, tail in (("custom_", "-leg position"), ("multi_expiry_", "-leg, two expiries")):
        if (shape or "").startswith(prefix):
            digits = "".join(ch for ch in shape if ch.isdigit())
            if digits:
                return f"{words.get(int(digits), digits)}{tail}"
    return (shape or "Custom").replace("_", " ").capitalize()


def gloss(card):
    """Two lines. The first says what it holds and when; the second, how it gets out.

    Two rather than one because those are two different facts, and a reader whose only
    question is "does this have a stop" should not have to parse a clause to find out.
    """
    legs = card["legs"]
    if not legs:
        return ["", ""]
    sells = [l for l in legs if l["side"] == "sell"]
    buys = [l for l in legs if l["side"] == "buy"]

    if len(legs) > 4:
        # Beyond four legs a leg-by-leg sentence stops being readable. Say the shape.
        near = min((l["where"] for l in sells), key=len, default="")
        one = (f"{len(legs)} legs — {len(sells)} short, {len(buys)} long"
               + (f", the shorts {near}" if near and "money" not in near else "") + ".")
    else:
        # Expiry diversity is a property of the WHOLE position, not of the shorts or the
        # longs alone -- a calendar has one leg in each list, so computing it per-list
        # would drop the only thing that distinguishes it from a position netting to zero.
        multi = len({l["expiry"] for l in legs}) > 1
        parts = []
        if sells:
            parts.append(f"sells {_leg_phrase(sells, multi)}")
        if buys:
            parts.append(f"buys {_leg_phrase(buys, multi)}")
        one = " and ".join(parts).capitalize() + "."

    when = card["entry"][0]["text"] if card["entry"] else ""
    at = (card["entry"][1]["text"].replace("Entered at ", "")
          if len(card["entry"]) > 1 else "")
    gate = (card["entry"][2]["text"] if len(card["entry"]) > 2 else "")
    one += f" {when}" + (f" at {at}" if at else "") + "."
    if gate:
        one += f" {gate}."

    # --- line two: the way out, most decisive first -------------------------
    outs = []
    if card["target"]:
        outs.append(f"takes profit at {card['target'][0]['short']}")
    if card["stop"]:
        outs.append(f"cuts at {card['stop'][0]['short']}")
    for o in card["other"]:
        outs.append(f"closes on {o['short']}")
    if card["adjust"]:
        n = len(card["adjust"])
        outs.append(f"and adjusts {n} way{'s' if n != 1 else ''} before either")
    hold = card["exit"][0]["text"] if card["exit"] else "Held to expiry"
    if outs:
        joined = ", ".join(outs[:-1]) + (
            (" " if outs[-1].startswith("and") else ", ") + outs[-1]
            if len(outs) > 1 else outs[0])
        two = f"{joined.capitalize()}. Otherwise {hold[0].lower() + hold[1:]}."
    else:
        two = f"{hold}. No target, no stop, no adjustment."
    return [one, two]


def _leg_phrase(legs, two_expiries=False):
    """'a call and a put 1% out' when symmetric, leg by leg when not."""
    names = {"CE": "call", "PE": "put"}
    wheres = {l["where"] for l in legs}
    if (len(legs) == 2 and len(wheres) == 1 and not two_expiries
            and {l["type"] for l in legs} == {"CE", "PE"}):
        return f"a call and a put {next(iter(wheres))}"
    # A symmetric pair written as two directions -- 1% above / 1% below -- is the same
    # sentence with the distance said once.
    if len(legs) == 2 and {l["type"] for l in legs} == {"CE", "PE"}:
        d = {w.split("%")[0] for w in wheres if "%" in w}
        if len(d) == 1 and all("spot" in w for w in wheres):
            return f"a call and a put {next(iter(d))}% either side of spot"
    out = []
    for l in legs:
        q = "" if l["qty"] == 1 else f"{l['qty']}× "
        exp = (f" in the {'next' if l['expiry'] == 'next' else 'near'} expiry"
               if two_expiries else "")
        out.append(f"{q}a {names.get(l['type'], l['type'])} {l['where']}{exp}")
    return " and ".join(out)
