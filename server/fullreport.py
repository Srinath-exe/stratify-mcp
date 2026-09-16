"""The strategy report: a tearsheet a professional can act on, not a results dump.

WHAT CHANGED AND WHY. The first version answered "what did it make". That is the least
interesting question anyone asks about a systematic strategy, and it is the only one most
retail backtesters answer. This version is built around the questions a person actually
has to settle before risking money:

    is the edge real, or the best of many things that were tried
    where did the result come from -- a few trades, or one year, or one volatility regime
    how long was I under water, not just how deep
    what does the loss tail look like next to the win body
    what capital did it really need, and what did it cost to run
    would I have stuck with it

THE ORDER IS THE ARGUMENT. A verdict and the findings that produced it come first, because
a reader who stops after one screen should stop with the right conclusion rather than with
the headline return. Then the numbers, then the account through time, then where the money
came from, then the risk shape, then the market it traded, then the evidence, then the
rules. Everything persuasive is placed after something that qualifies it.

THE TRADE TABLE IS GONE. Three hundred and eighty-five rows of numbers is a dataset, not a
report: nobody reads it, and the two trades that decided the outcome are invisible inside
it. The same trades are drawn as a scatter -- when, return on margin, size -- where the
outliers, the clustering and any decay of the edge are one look, and any point can be
clicked for its row. The per-leg prices that a table would have carried are still released
under the same metered budget, in the tool response, where a caller can audit them.

WHAT IT RELEASES. Index candles, which are not the asset and go out in full. Trade
timestamps, P&L and margin, which are the caller's own results. Every panel on this page is
computed from PRICE-FREE rows -- so drawing all of it costs nothing against the per-account
price meter.
"""
import datetime as dt
import html
import json
import pathlib
import statistics

from . import analytics, design, sizing

LWC = (pathlib.Path(__file__).resolve().parent / "vendor" /
       "lightweight-charts.js").read_text()

STRUCTURE_PROSE = {
    "short_strangle": ("Sells an out-of-the-money call and an out-of-the-money put on the "
                       "same expiry, collecting both premiums. It profits when the index "
                       "stays between the two strikes and loses without a defined limit "
                       "if it moves far through either."),
    "iron_condor": ("Sells an out-of-the-money call and put, and buys further-out options "
                    "on each side as protection. Profit is capped at the credit "
                    "collected; loss is capped at the width of the wings minus that "
                    "credit."),
    "iron_fly": ("Sells a call and a put at the money and buys protection on both sides. "
                 "It collects far more premium than a condor and needs the index to "
                 "finish very close to where it started."),
    "credit_spread": ("Sells one option and buys a further-out one on the same side, "
                      "collecting the difference. Directional: it profits if the index "
                      "stays away from the sold strike, and the maximum loss is fixed."),
    "long_option": ("Buys a single option outright. The most that can be lost is the "
                    "premium paid; the gain is open-ended but time decay works against it "
                    "every day it is held."),
}

GATE_PROSE = {
    "always": "Every cycle is taken. No filter.",
    "avoid_shock": "Cycles following an outsized move in the index are skipped.",
    "fade_shock": "Only cycles following an outsized move are taken.",
    "rsi_neutral": "Only cycles where the index is neither overbought nor oversold.",
}

VERDICT_TONE = {"strong_evidence": "ok", "moderate_evidence": "ok",
                "mixed_evidence": "warn", "weak_evidence": "warn",
                "insufficient_evidence": "bad", "no_evidence": "bad"}


# ------------------------------------------------------------------- formatting

def _e(v):
    return html.escape("" if v is None else str(v))


def _money(v, dp=0):
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e7:
        s = f"₹{a / 1e7:.2f} Cr"
    elif a >= 1e5:
        s = f"₹{a / 1e5:.2f} L"
    elif a >= 1000:
        s = f"₹{a / 1000:.1f}k"
    else:
        s = f"₹{a:,.{dp}f}"
    return ("−" if v < 0 else "") + s


def _rs(v, dp=0):
    """Exact rupees with separators -- for tables, where a reader is checking arithmetic."""
    if v is None:
        return "—"
    return ("−" if v < 0 else "") + f"₹{abs(v):,.{dp}f}"


def _pct(v, dp=1):
    """v is already a percentage. U+2212, not a hyphen: the page mixes rupee figures that
    use the real minus with percentages that did not, and the two read as different kinds
    of negative."""
    return "—" if v is None else f"{v:.{dp}f}%".replace("-", "\u2212")


def _fpct(v, dp=1, sign=False):
    """v is a fraction."""
    if v is None:
        return "—"
    s = f"{v * 100:.{dp}f}%".replace("-", "\u2212")
    return ("+" + s if v > 0 else s) if sign else s


def _num(v, dp=2):
    return "—" if v is None else f"{v:,.{dp}f}".replace("-", "\u2212")


def _days(n):
    if n is None:
        return "—"
    if n >= 730:
        return f"{n / 365.25:.1f} yr"
    if n >= 60:
        return f"{n / 30.44:.0f} mo"
    return f"{n} d"


def _cls(v, invert=False):
    if v is None:
        return ""
    good = (v < 0) if invert else (v > 0)
    return "g" if good else ("b" if v else "")


# ------------------------------------------------------------------------ rules

def rules(spec):
    """The strategy in sentences. A reader should not have to decode pct_offset."""
    p = spec.get("params") or {}
    out = []
    what = STRUCTURE_PROSE.get(spec.get("structure", ""))
    if what:
        out.append(("What it trades", what))

    entry = []
    if spec.get("cadence", "weekly") == "weekly":
        dte = p.get("entry_dte"); ndb = p.get("entry_days_before")
        entry.append("Enters once per weekly expiry" +
                     (f", {ndb} trading session{'s' if ndb != 1 else ''} before it"
                      if ndb is not None else
                      (f", {dte} days before it" if dte is not None else "")) +
                     f", at {spec.get('entry_time', '09:15')}.")
        if spec.get("overlay"):
            entry.append(f"Skips the week when the index's 20-day realised volatility is "
                         f"above {spec['overlay'][3:]}% at entry ({spec['overlay']} overlay).")
    else:
        entry.append(f"Enters every trading session at {spec.get('entry_time', '09:15')}, "
                     f"on whichever weekly expiry is nearest.")
        if spec.get("max_dte") is not None:
            entry.append(f"Sessions where that expiry is more than {spec['max_dte']} "
                         f"day(s) away are skipped"
                         + (" — so this is expiry-day only."
                            if spec["max_dte"] == 0 else "."))
    if p.get("pct_offset") is not None:
        entry.append(f"Strikes are placed {p['pct_offset']}% of spot away from the index.")
    if p.get("pct_width") is not None:
        entry.append(f"Protection is bought a further {p['pct_width']}% out.")
    if p.get("direction"):
        entry.append(f"Trades the {p['direction']} side.")
    out.append(("How it enters", " ".join(entry)))

    exits = []
    if spec.get("exit_time"):
        exits.append(f"Squared off the same session at {spec['exit_time']} — it never "
                     f"reaches expiry.")
    if p.get("sl_mult") is not None:
        exits.append(f"Stopped out if the position loses {p['sl_mult']}× the credit "
                     f"collected.")
    if p.get("sl_pct") is not None:
        exits.append(f"Stopped out after losing {p['sl_pct'] * 100:.0f}% of the premium "
                     f"paid.")
    if p.get("tp_pct") is not None:
        exits.append(f"Taken off once {p['tp_pct'] * 100:.0f}% of the credit has been kept.")
    if not spec.get("exit_time"):
        exits.append("Otherwise held to expiry and settled against the mean of the index "
                     "over its final 30 minutes, which is the NSE convention.")
    out.append(("How it exits", " ".join(exits)))

    gate = spec.get("gate", "always")
    if gate and gate != "always":
        out.append(("Filter", GATE_PROSE.get(gate, f"Gate: {gate}.")))
    out.append(("Costs", "Every figure is after brokerage, STT, exchange and stamp "
                         "charges, GST, and slippage modelled from measured bid-ask on "
                         "the actual 1-minute bar. Entry is the traded price, not the "
                         "midpoint."))
    return out


# --------------------------------------------------------------------- position

# Where each structure puts its strikes, as a percentage of spot. Sign is the position:
# -1 sold, +1 bought. This is the same geometry the engine trades, written once so the
# picture on the page cannot drift from the rule that produced the trades.
def _geometry_from_trades(trades):
    """The position's shape read off the trades that actually happened.

    Better than parsing the spec, and the only thing that works at all for a strike named
    by its PREMIUM or its DELTA -- where the spec says "the 20-delta call" and only the
    fill knows which strike that was. Each leg's strike is expressed as a percentage of
    the spot it was opened against, and the median across trades is the typical position.
    Legs opened later by a rule are excluded: they are the adjustment, not the trade.
    """
    slots = {}
    for t in trades:
        spot = t.get("spot_at_entry")
        for i, l in enumerate(t.get("legs") or []):
            if not spot or l.get("opened_at"):
                continue
            slots.setdefault(i, []).append(
                ((l["strike"] / spot - 1.0) * 100.0, l["type"],
                 -1 if l["action"] == "SELL" else 1, l.get("qty", 1)))
    out = []
    for i in sorted(slots):
        vals = slots[i]
        kinds = {(k, s, q) for _, k, s, q in vals}
        if len(kinds) != 1:
            return []                    # the leg changed identity between trades
        kind, sign, qty = kinds.pop()
        pcts = sorted(v for v, _, _, _ in vals)
        out.extend([(kind, sign, pcts[len(pcts) // 2])] * qty)
    return out


def _geometry(spec):
    p = spec.get("params") or {}
    st, off, w = spec.get("structure", ""), p.get("pct_offset"), p.get("pct_width")
    call = (p.get("direction") or "call").lower().startswith("c")
    if st == "iron_condor" and off is not None and w is not None:
        return [("CE", -1, off), ("CE", 1, off + w),
                ("PE", -1, -off), ("PE", 1, -(off + w))]
    if st == "iron_fly" and w is not None:
        return [("CE", -1, 0.0), ("PE", -1, 0.0), ("CE", 1, w), ("PE", 1, -w)]
    if st == "short_strangle" and off is not None:
        return [("CE", -1, off), ("PE", -1, -off)]
    if st == "credit_spread" and off is not None and w is not None:
        return ([("CE", -1, off), ("CE", 1, off + w)] if call
                else [("PE", -1, -off), ("PE", 1, -(off + w))])
    if st == "long_option" and off is not None:
        return [("CE", 1, off)] if call else [("PE", 1, -off)]
    return []


def _bounds(payoff, span):
    """(bounded above, bounded below) from the direction of the two far tails.

    Tested SEPARATELY per direction, because most structures are bounded on one side and
    not the other and a single "is it capped" flag gets both wrong: a short strangle's
    profit is exactly the credit while its loss is open-ended, and requiring both tails to
    be flat reported the capped half as "open-ended" too.
    """
    right = payoff(span * 2) - payoff(span)
    left = payoff(-span * 2) - payoff(-span)
    return (right <= 1e-6 and left <= 1e-6), (right >= -1e-6 and left >= -1e-6)


def _intrinsic(kind, strike, spot):
    return max(spot - strike, 0.0) if kind == "CE" else max(strike - spot, 0.0)


def position(spec, trades, moves):
    """The typical position, drawn to scale: strikes, net credit, and the payoff.

    THE CREDIT IS BACKED OUT, NOT GUESSED. For a trade held to expiry,
    gross = credit - SIGMA sign x intrinsic(exit spot, strike), so the credit is exactly
    recoverable from figures the page already holds -- no option price is read, and the
    curve therefore lands on the same P&L the trade table reports rather than near it.

    Normalised by spot before the median is taken. NIFTY went from 10,600 to 24,000 across
    this window, so a credit in raw points means two different things at the two ends and
    their median means nothing at either.
    """
    # Prefer the spec's own geometry when it has one, and fall back to what the trades
    # actually did -- which is the ONLY thing available for an open leg list, and is
    # strictly more truthful anyway.
    legs = _geometry(spec) or _geometry_from_trades(trades)
    if not legs or not moves:
        return None
    # moves["values"] is positional in EXIT order, the order analytics builds everything
    # in. Pairing it with the trade rows requires re-sorting them the same way; zipping
    # entry-ordered rows against exit-ordered moves silently mismatches on any cadence
    # where positions overlap.
    creds, spots, lots = [], [], []
    idx = 0
    vals = moves.get("values") or []
    for t in sorted(trades, key=lambda x: ((x.get("exit") or ""), x.get("entry") or "")):
        if not t.get("spot_at_entry") or t.get("gross_points") is None:
            continue
        if idx >= len(vals):
            break
        entry, move = t["spot_at_entry"], vals[idx]
        idx += 1
        exit_spot = entry * (1 + move)
        c, clean = t["gross_points"], True
        for kind, sign, k in legs:
            intr = _intrinsic(kind, entry * (1 + k / 100.0), exit_spot)
            c -= sign * intr
            clean = clean and intr == 0.0
        creds.append((c / entry, clean))
        spots.append(entry)
        lots.append(t.get("lot_size") or 0)
    if not creds:
        return None
    spot = statistics.median(spots)
    clean_only = [c for c, ok in creds if ok]
    pool = clean_only if len(clean_only) >= 10 else [c for c, _ in creds]
    credit = statistics.median(pool) * spot

    def payoff(pct):
        """credit PLUS sign x intrinsic: a sold leg pays the credit and gives back the
        intrinsic, a bought one costs its premium and returns the intrinsic. Written with
        a minus -- which it was -- every short spread draws as if it profited from exactly
        the move it exists to fear."""
        s_ = spot * (1 + pct / 100.0)
        return credit + sum(sign * _intrinsic(kind, spot * (1 + k / 100.0), s_)
                            for kind, sign, k in legs)

    # Breakevens by scan: the curve is piecewise linear with at most four kinks, so a fine
    # sweep finds every crossing without solving anything.
    span = max(abs(k) for _, _, k in legs) * 3 + 3
    grid = [(-span + i * (2 * span) / 2000.0) for i in range(2001)]
    be = [round((grid[i] + grid[i + 1]) / 2, 3) for i in range(2000)
          if (payoff(grid[i]) >= 0) != (payoff(grid[i + 1]) >= 0)]
    inside = sum(1 for v in vals if payoff(v * 100) > 0) / len(vals) if vals else None
    above, below = _bounds(payoff, span)
    sampled = [payoff(g) for g in grid]
    return {
        "legs": [{"t": kind, "s": sign, "k": round(k, 3)} for kind, sign, k in legs],
        "credit": round(credit, 2), "spot": round(spot, 1),
        "lot": statistics.median(lots) if lots else 1,
        "offset": max((abs(k) for kind, sign, k in legs if sign < 0), default=0.0),
        "span": max(abs(k) for _, _, k in legs),
        "breakevens": be[:1] + be[-1:] if len(be) > 1 else be,
        "inside": inside,
        # Report a maximum only where there IS one. A short strangle's loss and a long
        # option's gain are open-ended, and printing the value at the edge of the drawing
        # as "the maximum" turns the chart's own crop into a risk limit.
        "max_profit": round(max(sampled), 1) if above else None,
        "max_loss": round(min(sampled), 1) if below else None,
        "capped": above and below,
    }


# --------------------------------------------------------------------- findings

def findings(a, h, s, capital):
    """The four or five sentences a professional would say out loud after reading this.

    WHY THIS IS GENERATED RATHER THAN LEFT TO THE READER. Every fact needed to reach these
    conclusions is somewhere on the page, and a careful reader would find them -- in twenty
    minutes, across nine panels. The report's job is to put the conclusion next to the
    evidence, ranked by how much it should change the decision.

    ONE CLAUSE EACH. An earlier version wrote a paragraph per finding and the section read
    as an essay: by the third card the reader is skimming, which is the opposite of what a
    ranked list is for. The supporting chart is always directly below.
    """
    out = []
    q, d = a["quality"], a["distribution"]
    cost, conc, ep = a["costs"], a["concentration"], a["episodes"]

    surv = cost.get("surviving")
    if surv is None or surv <= 0:
        out.append(("bad", "Costs exceeded the edge, they did not merely reduce it.",
                    f'{_num(cost["gross"], 0)} points gross became '
                    f'{_num(cost["net"], 0)} net — it earned before costs and lost after.'))
    elif surv < 0.4:
        out.append(("warn", f"Only {_fpct(surv, 0)} of the gross edge survives costs.",
                    f'Every trade must clear {_num(cost["breakeven_points"], 1)} points '
                    f'before it earns anything.'))
    else:
        out.append(("ok", f"{_fpct(surv, 0)} of the gross edge survives costs.",
                    f'{_num(cost["breakeven_points"], 1)} points of costs per trade, '
                    f'against {_num(cost["per_trade"]["net"], 1)} points kept.'))

    worst = ep[0] if ep else None
    if worst:
        span = q["max_underwater_days"]
        tone = "bad" if span > 540 else ("warn" if span > 200 else "ok")
        out.append((tone, f'The account spent {_days(span)} below a previous high.',
                    f'{_fpct(worst["depth"], 1)} at its deepest, reached over '
                    f'{_days(worst["to_trough_days"])}'
                    + (', never recovered.' if not worst["recovered"]
                       else f', back in {_days(worst["recover_days"])}.')))

    if d and d.get("worst_over_avg_win"):
        r = d["worst_over_avg_win"]
        tone = "bad" if r > 8 else ("warn" if r > 4 else "ok")
        out.append((tone, f'Its worst trade was {r:.1f}× its average win.',
                    f'{_fpct(d["win_rate"], 0)} won, but the worst one trade in twenty '
                    f'lost {_fpct(d["p05"], 0)} of its margin or more.'))

    oos = h.get("out_of_sample") or {}
    if oos:
        held = bool(oos.get("held_up"))
        ins, outs = oos.get("in_sample") or {}, oos.get("out_of_sample") or {}
        out.append(("ok" if held else "bad",
                    "The held-out final 30% held up." if held else
                    "The held-out final 30% did not hold up.",
                    f'Fitted on {ins.get("n_trades", "?")} trades, tested on '
                    f'{outs.get("n_trades", "?")} it had never seen — split by time, '
                    f'never at random.'))

    wf = h.get("walk_forward") or []
    if wf:
        good = [f for f in wf if f.get("profitable")]
        bad = [f for f in wf if not f.get("profitable")]
        if bad:
            b = min(bad, key=lambda f: f.get("pnl_rupees", 0))
            out.append(("warn" if len(good) > len(bad) else "bad",
                        f'{len(good)} of {len(wf)} walk-forward folds were profitable.',
                        f'Fold {b.get("fold")} lost '
                        f'{_money(abs(b.get("pnl_rupees", 0)))} over '
                        f'{b.get("n_trades")} trades.'))
        else:
            out.append(("ok", f'All {len(wf)} walk-forward folds were profitable.',
                        'Each fold is judged only on data after the one before it.'))

    if conc and conc["n"] >= 20:
        top = conc["top10_of_profit"]
        out.append(("warn" if top > 0.5 else "ok",
                    f'The ten best trades made {_fpct(top, 0)} of all gross profit.',
                    f'Drop the best five and the account ends at '
                    f'{_money(conc["ex_best5"])}, not '
                    f'{_money(a["sized"]["ending_capital"])}.'))

    reg = a.get("regimes")
    if reg and reg["vol"]:
        thin = [b for b in reg["vol"] if b["n"] < max(5, 0.08 * conc["n"])]
        if thin:
            out.append(("warn", f'Almost no evidence in '
                        f'{" or ".join(b["name"] for b in thin)} markets.',
                        'The bands are cut on the index, so a thin one means the strategy '
                        'was rarely on in that state.'))
        else:
            best = max(reg["vol"], key=lambda b: b["mean_rom"])
            out.append(("ok" if best["mean_rom"] > 0 else "warn",
                        f'It did best in {best["name"]} markets.',
                        f'{best["n"]} trades there, {_fpct(best["hit"], 0)} winners, '
                        f'averaging {_fpct(best["mean_rom"], 2, True)} on margin.'))

    order = {"bad": 0, "warn": 1, "ok": 2}
    return sorted(out, key=lambda x: order[x[0]])


# ------------------------------------------------------------------------- style

# Heat ramps for the calendar. Declared as tokens in all three theme states rather than
# inline, so a month cell is never the one colour on the page that ignores the theme.
_RAMP_LIGHT = ("--h1:#EAF2ED;--h2:#D2E5DA;--h3:#B0D5C3;--h4:#86C0A4;--h5:#4E9E7A;"
               "--l1:#FAEBE8;--l2:#F3D4CE;--l3:#E8B4AB;--l4:#D68D80;--l5:#B85B4A;"
               "--grid:#EFF2EE")
_RAMP_DARK = ("--h1:#182620;--h2:#1C3629;--h3:#204734;--h4:#286045;--h5:#347F5B;"
              "--l1:#2A1B19;--l2:#3A211D;--l3:#4C2823;--l4:#63332B;--l5:#844237;"
              "--grid:#222623")

RAMP = (f":root{{{_RAMP_LIGHT}}}\n"
        f"@media(prefers-color-scheme:dark){{:root:not([data-theme=light])"
        f"{{{_RAMP_DARK}}}}}\n"
        f":root[data-theme=dark]{{{_RAMP_DARK}}}\n")

CSS = design.TOKENS + RAMP + design.CHART_CSS + design.MARK_CSS + """
*{box-sizing:border-box}
html{scroll-behavior:smooth;scroll-padding-top:76px}
body{margin:0;background:var(--bg);color:var(--body);
font:400 15px/1.6 FONTSTACK;-webkit-font-smoothing:antialiased;
font-variant-numeric:tabular-nums}
::selection{background:var(--accent-soft);color:var(--accent-ink)}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}

/* ---- shell ------------------------------------------------------------- */
.wrap{max-width:1360px;margin:0 auto;padding:0 26px 90px}
@media(max-width:760px){.wrap{padding:0 15px 60px}}

/* ---- sticky nav -------------------------------------------------------- */
.bar{position:sticky;top:0;z-index:40;background:color-mix(in srgb,var(--bg) 88%,transparent);
backdrop-filter:saturate(160%) blur(12px);border-bottom:1px solid var(--line)}
.barin{max-width:1360px;margin:0 auto;padding:0 26px;display:flex;align-items:center;
gap:18px;height:52px}
@media(max-width:760px){.barin{padding:0 15px;gap:11px}}
.bmark{display:flex;align-items:center;gap:8px;flex:none}
.bmark svg{width:16px;height:16px}
.bmark b{font:600 12.5px/1 FONTSTACK;color:var(--ink);letter-spacing:-.01em}
.bmark span{font:600 9.5px/1 MONOSTACK;letter-spacing:.12em;text-transform:uppercase;
color:var(--faint)}
@media(max-width:900px){.bmark span{display:none}}
.nav{display:flex;gap:2px;overflow-x:auto;scrollbar-width:none;flex:1;min-width:0}
.nav::-webkit-scrollbar{display:none}
.nav a{font:600 10.5px/1 MONOSTACK;letter-spacing:.09em;text-transform:uppercase;
color:var(--muted);padding:8px 9px;border-radius:6px;white-space:nowrap}
.nav a:hover{background:var(--sunk);color:var(--ink);text-decoration:none}
.nav a.on{color:var(--accent);background:var(--accent-soft)}
.chip{flex:none;font:600 10px/1 MONOSTACK;letter-spacing:.1em;text-transform:uppercase;
padding:6px 9px;border-radius:20px;white-space:nowrap}
.chip.ok{background:var(--good-soft);color:var(--good)}
.chip.warn{background:var(--warn-soft);color:var(--warn)}
.chip.bad{background:var(--crit-soft);color:var(--crit)}
@media(max-width:620px){.chip{display:none}}

/* ---- masthead ---------------------------------------------------------- */
.mast{padding:44px 0 26px;border-bottom:1px solid var(--line);margin-bottom:26px;
display:grid;gap:34px;align-items:end;
grid-template-columns:minmax(0,1.05fr) minmax(310px,.82fr)}
@media(max-width:940px){.mast{grid-template-columns:1fr;gap:22px;align-items:start}}
@media(max-width:760px){.mast{padding:24px 0 18px}}
.glance{background:var(--surface);border:1px solid var(--line);border-radius:14px;
padding:15px 17px;box-shadow:var(--shadow)}
.gtop{display:flex;align-items:center;gap:15px;padding-bottom:13px;
border-bottom:1px solid var(--line)}
.gtop .dial{width:76px;height:76px;flex:none}
.gtop .dial b{font-size:21px}
.gname{font:600 15.5px/1.25 FONTSTACK;color:var(--ink);letter-spacing:-.012em}
.gsub{font:400 11px/1.5 MONOSTACK;color:var(--faint);margin-top:4px}
.gfig{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:13px 16px;
padding-top:13px}
.gf .k{font:600 9px/1.3 MONOSTACK;letter-spacing:.1em;text-transform:uppercase;
color:var(--faint)}
.gf .v{font-size:20px;line-height:1.15;font-weight:600;color:var(--ink);
letter-spacing:-.025em;margin-top:4px;white-space:nowrap}
.gf .v.g{color:var(--good)}.gf .v.b{color:var(--crit)}
.kick{font:600 10px/1 MONOSTACK;letter-spacing:.16em;text-transform:uppercase;
color:var(--accent);margin-bottom:13px}
h1{font-size:clamp(27px,4.2vw,44px);line-height:1.04;margin:0 0 12px;color:var(--ink);
letter-spacing:-.022em;font-weight:600;max-width:22ch;text-wrap:balance}
.sub{color:var(--muted);font-size:16px;line-height:1.55;margin:0;max-width:66ch}
.mmeta{display:flex;flex-wrap:wrap;gap:6px 0;margin-top:18px;
font:400 11.5px/1 MONOSTACK;color:var(--faint)}
.mmeta span{padding-right:15px;margin-right:15px;border-right:1px solid var(--line)}
.mmeta span:last-child{border:0}

/* ---- sections ---------------------------------------------------------- */
section{margin:46px 0 0;scroll-margin-top:70px}
@media(max-width:760px){section{margin-top:34px}}
.sh{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin:0 0 4px}
h2{font:600 10.5px/1 MONOSTACK;letter-spacing:.14em;text-transform:uppercase;
color:var(--accent);margin:34px 0 12px}
.sh h2{margin:0}
ul.pts{margin:0;padding:0;list-style:none;display:grid;gap:8px;
grid-template-columns:repeat(auto-fit,minmax(290px,1fr))}
ul.pts li{background:var(--surface);border:1px solid var(--line);border-radius:10px;
padding:12px 14px;font-size:13.5px;line-height:1.55;color:var(--muted);
box-shadow:var(--shadow)}
.sh .sn{font:400 11.5px/1 MONOSTACK;color:var(--faint)}
.lede{font-size:15px;line-height:1.6;color:var(--muted);margin:0 0 16px;max-width:78ch}
.lede b{color:var(--ink);font-weight:600}

/* ---- grid -------------------------------------------------------------- */
.lay{display:grid;gap:13px;align-items:start;
grid-template-columns:repeat(12,minmax(0,1fr))}
.s3{grid-column:span 3}.s4{grid-column:span 4}.s5{grid-column:span 5}
.s6{grid-column:span 6}.s7{grid-column:span 7}.s8{grid-column:span 8}
.s9{grid-column:span 9}.s12{grid-column:span 12}
@media(max-width:1080px){.s3,.s4,.s5{grid-column:span 6}.s7,.s8,.s9{grid-column:span 12}}
@media(max-width:680px){.lay{gap:10px}.s3,.s4,.s5,.s6,.s7,.s8,.s9{grid-column:span 12}}

.card{background:var(--surface);border:1px solid var(--line);border-radius:12px;
padding:15px 17px;box-shadow:var(--shadow);min-width:0}
.card.pad0{padding:0;overflow:hidden}
.ct{font:600 9.5px/1.3 MONOSTACK;letter-spacing:.11em;text-transform:uppercase;
color:var(--faint);display:flex;align-items:baseline;gap:8px;justify-content:space-between}
.ct em{font-style:normal;color:var(--faint);letter-spacing:0;text-transform:none;
font-weight:400;font-size:10.5px}

/* ---- verdict ----------------------------------------------------------- */
.dial{position:relative;width:104px;height:104px}
.dial svg{width:100%;height:100%;display:block}
.dial b{position:absolute;inset:0;display:grid;place-items:center;
font:600 27px/1 FONTSTACK;color:var(--ink);letter-spacing:-.03em}
.vhead{font-size:clamp(19px,2.3vw,25px);line-height:1.3;color:var(--ink);font-weight:600;
margin:0 0 16px;letter-spacing:-.016em;text-wrap:balance;max-width:34ch}
.vhead .g{color:var(--good)}.vhead .b{color:var(--crit)}
.fnd{display:grid;gap:11px;align-items:start;
grid-template-columns:repeat(auto-fit,minmax(352px,1fr))}
@media(max-width:600px){.fnd{grid-template-columns:1fr}}
.fn{display:grid;grid-template-columns:14px minmax(0,1fr);gap:12px;align-items:start;
background:var(--surface);border:1px solid var(--line);border-radius:12px;
padding:14px 16px;box-shadow:var(--shadow)}
.fn .dot{width:9px;height:9px;border-radius:50%;margin-top:6px}
.fn.ok .dot{background:var(--good)}.fn.warn .dot{background:var(--warn)}
.fn.bad .dot{background:var(--crit)}
.fn.bad{border-color:color-mix(in srgb,var(--crit) 26%,var(--line))}
.fn b{display:block;color:var(--ink);font-size:14.5px;font-weight:600;line-height:1.4}
.fn span{display:block;color:var(--muted);font-size:13px;line-height:1.58;margin-top:4px}

/* ---- metric cards ------------------------------------------------------ */
.mgrid{display:grid;gap:11px;grid-template-columns:repeat(auto-fit,minmax(178px,1fr))}
@media(max-width:520px){.mgrid{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}}
.m{background:var(--surface);border:1px solid var(--line);border-radius:11px;
padding:13px 14px;box-shadow:var(--shadow);min-width:0}
.m .k{font:600 9px/1.3 MONOSTACK;letter-spacing:.1em;text-transform:uppercase;
color:var(--faint)}
.m .v{font-size:25px;line-height:1.12;margin-top:6px;color:var(--ink);font-weight:600;
letter-spacing:-.028em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.m .v.g{color:var(--good)}.m .v.b{color:var(--crit)}
.m .s{font-size:11.5px;color:var(--faint);margin-top:5px;line-height:1.45}
@media(max-width:520px){.m .v{font-size:20px}.m .s{font-size:10.5px}}
.mband{font:600 9px/1 MONOSTACK;letter-spacing:.12em;text-transform:uppercase;
color:var(--muted);margin:20px 0 9px;display:flex;align-items:center;gap:10px}
.mband:before{content:"";flex:1;height:1px;background:var(--line);order:2}
.mband:first-child{margin-top:0}

/* ---- charts ------------------------------------------------------------ */
.chartbox{background:var(--surface);border:1px solid var(--line);border-radius:12px;
padding:13px 15px 11px;box-shadow:var(--shadow);min-width:0}
.chead{display:flex;align-items:baseline;justify-content:space-between;gap:12px;
flex-wrap:wrap;margin-bottom:10px}
.ctitle{font:600 13px/1.3 FONTSTACK;color:var(--ink)}
.chint{font:400 11px/1.3 MONOSTACK;color:var(--faint)}
.legend{display:flex;gap:13px;flex-wrap:wrap;margin-top:9px;
font:400 11px/1.5 MONOSTACK;color:var(--muted)}
.legend i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px}
.legend i.rd{border-radius:50%}
.legend i.ln{height:2px;width:14px;border-radius:1px;vertical-align:middle}
#px{height:420px}#eq{height:260px}#uw{height:118px}
#pay{min-height:340px}
@media(max-width:760px){#px{height:300px}#eq{height:210px}#uw{height:92px}}
.tip{margin-top:9px;padding:9px 11px;background:var(--raised);border:1px solid var(--soft);
border-radius:8px;font:400 12px/1.6 MONOSTACK;color:var(--body);min-height:3.4em}
.tip b{color:var(--ink);font-weight:600}
.tip .g{color:var(--good)}.tip .b{color:var(--crit)}.tip .muted{color:var(--faint)}
svg.chart{display:block;width:100%;height:auto;overflow:visible}

/* ---- calendar heatmap -------------------------------------------------- */
.calwrap{overflow-x:auto}
.cal{border-collapse:separate;border-spacing:3px;width:100%;min-width:640px}
.cal th{font:600 9px/1 MONOSTACK;letter-spacing:.09em;text-transform:uppercase;
color:var(--faint);padding:0 0 4px;text-align:center;font-weight:600}
.cal th.yr{text-align:right;padding-right:8px;width:44px}
.cal th:last-child,.cal td.tot{width:62px}
.cal td{padding:0;text-align:center;border-radius:5px;height:34px;
font:600 11.5px/1 MONOSTACK;color:var(--ink);background:var(--sunk);position:relative}
.cal td.yr{background:none;text-align:right;padding-right:8px;color:var(--muted);
font-size:11px}
.cal td.tot{background:var(--raised);border:1px solid var(--line)}
.cal td.nil{background:var(--sunk);color:var(--faint);opacity:.45}
.cal td.p1{background:var(--h1)}.cal td.p2{background:var(--h2)}
.cal td.p3{background:var(--h3)}.cal td.p4{background:var(--h4)}
.cal td.p5{background:var(--h5);color:#fff}
.cal td.n1{background:var(--l1)}.cal td.n2{background:var(--l2)}
.cal td.n3{background:var(--l3)}.cal td.n4{background:var(--l4)}
.cal td.n5{background:var(--l5);color:#fff}
.calkey{display:flex;align-items:center;gap:6px;margin-top:10px;
font:400 10.5px/1 MONOSTACK;color:var(--faint);flex-wrap:wrap}
.calkey i{width:17px;height:11px;border-radius:2px;display:inline-block}

/* ---- figure strip ------------------------------------------------------- */
.strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(126px,1fr));
background:var(--surface);border:1px solid var(--line);border-radius:11px;overflow:hidden;
margin-top:13px}
.strip>div{padding:13px 14px;box-shadow:1px 0 0 var(--line),0 1px 0 var(--line)}
.strip .k{font:600 8.5px/1.3 MONOSTACK;letter-spacing:.11em;text-transform:uppercase;
color:var(--faint)}
.strip .v{font-size:20px;line-height:1.15;font-weight:600;color:var(--ink);
letter-spacing:-.025em;margin-top:6px;white-space:nowrap}
.strip .v.g{color:var(--good)}.strip .v.b{color:var(--crit)}
@media(max-width:520px){.strip .v{font-size:17px}}

/* ---- rubric bars -------------------------------------------------------- */
.scw{display:grid;gap:10px;margin-top:14px}
.sc{display:grid;grid-template-columns:minmax(0,1fr) 74px 46px;gap:10px;align-items:center}
.scn{font:600 11.5px/1.3 FONTSTACK;color:var(--ink);text-transform:capitalize}
.sct{height:7px;border-radius:4px;background:var(--sunk);overflow:hidden}
.sct i{display:block;height:100%;border-radius:4px}
.sct i.pos{background:var(--good)}.sct i.neg{background:var(--crit)}
.scv{font:600 11.5px/1 MONOSTACK;color:var(--ink);text-align:right}
.scv span{color:var(--faint);font-weight:400}

/* ---- rule strip --------------------------------------------------------- */
.rlgrid{display:grid;background:var(--surface);border:1px solid var(--line);
border-radius:11px;overflow:hidden;margin-top:13px;
grid-template-columns:repeat(auto-fit,minmax(210px,1fr))}
.rl{padding:13px 15px;box-shadow:1px 0 0 var(--line),0 1px 0 var(--line)}
.rl .rk{font:600 8.5px/1.3 MONOSTACK;letter-spacing:.11em;text-transform:uppercase;
color:var(--accent)}
.rl .rv{font-size:13.5px;line-height:1.45;color:var(--body);margin-top:5px}

/* ---- percentile ladder -------------------------------------------------- */
.pctl{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));
background:var(--surface);border:1px solid var(--line);border-radius:9px;overflow:hidden;
margin-top:12px}
.pctl div{padding:9px 6px;text-align:center;box-shadow:1px 0 0 var(--line)}
.pctl .k{font:600 8.5px/1.3 MONOSTACK;letter-spacing:.09em;text-transform:uppercase;
color:var(--faint)}
.pctl .v{font:600 14px/1.2 MONOSTACK;margin-top:4px;color:var(--ink)}
.pctl .v.g{color:var(--good)}.pctl .v.b{color:var(--crit)}

/* ---- tables ------------------------------------------------------------ */
.scroll{overflow-x:auto;background:var(--surface);border:1px solid var(--line);
border-radius:12px;box-shadow:var(--shadow)}
table.t{width:100%;border-collapse:collapse;font-size:13px}
table.t th,table.t td{text-align:left;padding:9px 14px;
border-bottom:1px solid var(--soft);white-space:nowrap}
table.t tbody tr:last-child td{border-bottom:0}
table.t th{font:600 9px/1.3 MONOSTACK;letter-spacing:.08em;text-transform:uppercase;
color:var(--muted);background:var(--raised);position:sticky;top:0}
table.t td.n,table.t th.n{text-align:right}
table.t td.w{white-space:normal;min-width:13rem;line-height:1.5;color:var(--muted);
font-size:12.5px}
table.t.fx{table-layout:fixed}
table.t.fx td:first-child{width:36%}
table.t.fx td.n{width:22%}
table.t tbody tr:hover{background:var(--raised)}
table.t td.name{font-weight:600;color:var(--ink)}
table.t tr.hi td{background:var(--accent-soft)}
table.t tr.hi td:first-child{box-shadow:inset 3px 0 0 var(--accent)}
.g{color:var(--good)}.b{color:var(--crit)}.mut{color:var(--faint)}
.pill{display:inline-block;font:600 9px/1 MONOSTACK;letter-spacing:.09em;
text-transform:uppercase;padding:4px 6px;border-radius:3px}
.p-ok{background:var(--good-soft);color:var(--good)}
.p-no{background:var(--crit-soft);color:var(--crit)}
.p-mid{background:var(--warn-soft);color:var(--warn)}
.p-mut{background:var(--sunk);color:var(--muted)}

/* ---- bar rows ---------------------------------------------------------- */
.bars{display:grid;gap:9px}
.br{display:grid;grid-template-columns:minmax(112px,auto) minmax(0,1fr) minmax(70px,auto);
gap:11px;align-items:center;font-size:12.5px}
.br .bl{color:var(--ink);font-weight:600;font-size:12.5px}
.br .bt{position:relative;height:20px;background:var(--sunk);border-radius:4px;
overflow:hidden}
.br .bt i{position:absolute;top:0;bottom:0;border-radius:3px}
.br .bt i.pos{background:var(--good);opacity:.75}
.br .bt i.neg{background:var(--crit);opacity:.75}
.br .bt .zl{position:absolute;top:0;bottom:0;width:1px;background:var(--faint);opacity:.5}
.br .bv{text-align:right;font:600 12px/1 MONOSTACK}
.br .bn{font:400 10.5px/1.3 MONOSTACK;color:var(--faint)}

/* ---- scatter ----------------------------------------------------------- */
.sctwrap{position:relative}
#sct{height:340px}
@media(max-width:760px){#sct{height:260px}}
.filters{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:11px}
.fbtn{font:600 10px/1 MONOSTACK;letter-spacing:.08em;text-transform:uppercase;
padding:7px 10px;border-radius:16px;border:1px solid var(--line);background:var(--raised);
color:var(--muted);cursor:pointer}
.fbtn:hover{border-color:var(--accent);color:var(--ink)}
.fbtn.on{background:var(--accent);border-color:var(--accent);color:var(--surface)}
.pop{position:absolute;pointer-events:none;z-index:9;background:var(--surface);
border:1px solid var(--line);border-radius:9px;padding:9px 11px;box-shadow:var(--shadow);
font:400 11.5px/1.65 MONOSTACK;color:var(--muted);min-width:184px;opacity:0;
transition:opacity .1s}
.pop b{color:var(--ink);font-weight:600}
.pop .hd{font:600 9px/1 MONOSTACK;letter-spacing:.1em;text-transform:uppercase;
color:var(--faint);margin-bottom:5px}
@media(prefers-reduced-motion:reduce){.pop{transition:none}}

/* ---- evidence cards ---------------------------------------------------- */
.ev{display:grid;gap:12px;align-items:start;
grid-template-columns:repeat(12,minmax(0,1fr))}
.ev .evc{grid-column:span 4}
.ev .evc.wide{grid-column:span 6}
@media(max-width:1080px){.ev .evc,.ev .evc.wide{grid-column:span 6}}
@media(max-width:680px){.ev .evc,.ev .evc.wide{grid-column:span 12}}
.evc{background:var(--surface);border:1px solid var(--line);border-radius:12px;
padding:15px 16px;box-shadow:var(--shadow);display:flex;flex-direction:column;gap:9px}
.evc .eh{display:flex;align-items:center;justify-content:space-between;gap:9px}
.evc .en{font:600 12.5px/1.3 FONTSTACK;color:var(--ink)}
.evc .ed{font-size:12.5px;line-height:1.6;color:var(--muted);margin:0}
.evc svg{width:100%;height:auto;display:block}

/* ---- rules ------------------------------------------------------------- */
.rules{display:grid;gap:1px;background:var(--line);border:1px solid var(--line);
border-radius:12px;overflow:hidden}
.rule{background:var(--surface);padding:13px 17px;display:grid;
grid-template-columns:142px minmax(0,1fr);gap:16px;align-items:baseline}
@media(max-width:620px){.rule{grid-template-columns:1fr;gap:4px}}
.rule .rk{font:600 9px/1.4 MONOSTACK;letter-spacing:.1em;text-transform:uppercase;
color:var(--accent)}
.rule .rv{font-size:14px;line-height:1.55}

/* ---- cta / note / footer ----------------------------------------------- */
.cta{display:flex;align-items:center;gap:14px;background:var(--surface);
border:1px solid var(--line);border-radius:12px;padding:15px 17px;box-shadow:var(--shadow)}
.cta:hover{border-color:var(--accent);text-decoration:none}
.cta .ctai{flex:none;width:34px;height:34px;border-radius:50%;background:var(--accent);
color:var(--surface);display:grid;place-items:center;font-size:12px}
.cta .ctat{flex:1;min-width:0}
.cta .ctat b{display:block;color:var(--ink);font-size:14.5px;font-weight:600}
.cta .ctat em{display:block;font-style:normal;color:var(--faint);font-size:12.5px;
line-height:1.5;margin-top:2px}
.cta .ctag{color:var(--accent);font-size:17px}
.note{font-size:12.5px;line-height:1.65;color:var(--faint);
border-left:2px solid var(--line);padding-left:13px;margin:13px 0 0;max-width:80ch}
.note b{color:var(--muted);font-weight:600}
.foot{margin-top:52px;border-top:1px solid var(--line);padding-top:18px}
.foot p{font-size:12.5px;line-height:1.65;color:var(--faint);max-width:80ch;
margin:0 0 9px}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}
*{animation:none!important;transition:none!important}}
""".replace("FONTSTACK", design.FONT).replace("MONOSTACK", design.MONO)


# -------------------------------------------------------------------- behaviour

JS = design.CHART_JS + r"""
(function(){
var D = window.__STRATIFY__;
var css = getComputedStyle(document.documentElement);
function tok(n){ return css.getPropertyValue(n).trim() }
var UP=tok('--good'), DN=tok('--crit'), ACC=tok('--accent'), INK=tok('--ink'),
    MUT=tok('--muted'), FAINT=tok('--faint'), LINE=tok('--line'), GRID=tok('--grid'),
    SURF=tok('--surface'), WARN=tok('--warn');
function $(id){ return document.getElementById(id) }
function pc(v,dp){ return v==null?'—':nf((v*100).toFixed(dp==null?1:dp))+'%' }
function sgn(v,dp){ return v==null?'—':(v>0?'+':'')+
  nf((v*100).toFixed(dp==null?1:dp))+'%' }
function cl(v){ return v>0?'g':(v<0?'b':'muted') }
// U+2212. A hyphen next to a rupee figure that uses the real minus reads as two
// different kinds of negative on the same page.
function nf(x){ return String(x).replace(/-/g,'\u2212') }

/* =================================================================== LWC panes */
var base = {
  layout:{ background:{color:'transparent'}, textColor:FAINT,
           attributionLogo:true,               // the Apache-2.0 licence requires this
           fontFamily:getComputedStyle(document.body).fontFamily,
           fontSize:11 },
  grid:{ vertLines:{color:GRID}, horzLines:{color:GRID} },
  rightPriceScale:{ borderColor:LINE, scaleMargins:{top:.14,bottom:.08} },
  // 1,857 daily candles at the 0.5px default floor need 928px of pane; on a
  // 390px phone fitContent() therefore silently kept the last two years only.
  timeScale:{ borderColor:LINE, rightOffset:3, minBarSpacing:0.02 },
  crosshair:{ mode:0,
    vertLine:{color:MUT,width:1,style:3,labelBackgroundColor:ACC},
    horzLine:{color:MUT,width:1,style:3,labelBackgroundColor:ACC} },
  handleScale:{ axisPressedMouseMove:{time:true,price:false} }
};
function rupees(v){ return v>=1e7?'₹'+(v/1e7).toFixed(2)+'Cr'
  : v>=1e5?'₹'+(v/1e5).toFixed(1)+'L' : '₹'+Math.round(v/1000)+'k' }

/* ---- the account ------------------------------------------------------- */
var eqEl=$('eq'), uwEl=$('uw'), eq=null, uw=null;
if(eqEl){
  eq = LightweightCharts.createChart(eqEl, Object.assign({}, base, {
    height:eqEl.clientHeight, localization:{ priceFormatter:rupees } }));
  var eqA = eq.addSeries(LightweightCharts.AreaSeries, { lineColor:ACC,
    topColor:ACC+'2E', bottomColor:ACC+'03', lineWidth:2, priceLineVisible:false });
  eqA.setData(D.equity);
  if(D.bench && D.bench.length){
    var bl = eq.addSeries(LightweightCharts.LineSeries, { color:FAINT, lineWidth:1,
      lineStyle:0, priceLineVisible:false, lastValueVisible:false,
      crosshairMarkerVisible:false });
    bl.setData(D.bench);
  }
  var cap = eq.addSeries(LightweightCharts.LineSeries, { color:MUT, lineWidth:1,
    lineStyle:2, priceLineVisible:false, lastValueVisible:false,
    crosshairMarkerVisible:false });
  cap.setData(D.equity.map(function(p){ return {time:p.time, value:D.capital} }));
}
if(uwEl){
  uw = LightweightCharts.createChart(uwEl, Object.assign({}, base, {
    height:uwEl.clientHeight,
    rightPriceScale:{ borderColor:LINE, scaleMargins:{top:.06,bottom:.06} },
    localization:{ priceFormatter:function(v){ return nf((v*100).toFixed(0))+'%' } } }));
  // A BASELINE series, not an area. The series is entirely negative, and an area fills to
  // the pane floor -- which draws the deepest drawdown as the SMALLEST block of colour,
  // exactly inverting the thing the panel exists to show.
  var uwS = uw.addSeries(LightweightCharts.BaselineSeries,
    { baseValue:{type:'price',price:0},
      topLineColor:DN, topFillColor1:DN+'00', topFillColor2:DN+'00',
      bottomLineColor:DN, bottomFillColor1:DN+'12', bottomFillColor2:DN+'40',
      lineWidth:1, priceLineVisible:false, lastValueVisible:false });
  uwS.setData(D.uw);
  // The deepest point, labelled on its own axis. A reader should not have to hunt for the
  // trough of a line that spends its whole life below zero.
  if(D.maxdd!=null) uwS.createPriceLine({price:D.maxdd, color:DN, lineWidth:1,
    lineStyle:2, axisLabelVisible:true, title:D.maxddlabel||''});
}
// Pan one, pan the other. Two charts of one timeline that scroll independently are two
// charts the reader has to reconcile by hand. Synced by TIME, not logical index: the
// series do not share a point count.
var syncing=false;
function sync(a,b){ if(!a||!b) return;
  a.timeScale().subscribeVisibleTimeRangeChange(function(r){
    if(syncing||!r) return; syncing=true;
    try{ b.timeScale().setVisibleRange(r) }catch(e){}
    syncing=false; }) }
sync(eq,uw); sync(uw,eq);

/* ---- the account read-out ---------------------------------------------- */
var etip=$('etip');
if(eq && etip){
  var eqBy={}; D.equity.forEach(function(p){ eqBy[p.time]=p });
  var bBy={}; (D.bench||[]).forEach(function(p){ bBy[p.time]=p });
  var uwBy={}; D.uw.forEach(function(p){ uwBy[p.time]=p });
  var eqKeys=D.equity.map(function(p){ return p.time });
  function atOrBefore(map,keys,t){
    if(map[t]) return map[t];
    var lo=0,hi=keys.length-1,best=null;
    while(lo<=hi){ var m=(lo+hi)>>1;
      if(keys[m]<=t){ best=keys[m]; lo=m+1 } else hi=m-1 }
    return best?map[best]:null;
  }
  function eshow(t){
    if(!t){ etip.innerHTML=D.ehint; return }
    var p=atOrBefore(eqBy,eqKeys,t); if(!p){ etip.innerHTML=D.ehint; return }
    var u=uwBy[p.time], b=bBy[t];
    var r=(p.value/D.capital-1);
    etip.innerHTML='<b>'+t+'</b> &nbsp;·&nbsp; account <b>'+rupees(p.value)+'</b>'+
      ' <span class="'+cl(r)+'">'+sgn(r)+'</span>'+
      (u?' &nbsp;·&nbsp; <span class="'+(u.value<-0.0001?'b':'muted')+'">'+
         (u.value<-0.0001?'down '+pc(-u.value)+' from its peak':'at a new high')+
         '</span>':'')+
      (b?' &nbsp;·&nbsp; <span class="muted">index buy-and-hold '+rupees(b.value)+
         '</span>':'');
  }
  eq.subscribeCrosshairMove(function(p){ eshow(p&&p.time) });
  if(uw) uw.subscribeCrosshairMove(function(p){ eshow(p&&p.time) });
  eshow(null);
}

/* ---- the market, with every trade on it -------------------------------- */
var pxEl=$('px'), px=null;
if(pxEl){
  px = LightweightCharts.createChart(pxEl, Object.assign({}, base,
    // "30000.00" on an index axis is two characters of noise on every label; NIFTY is
    // quoted in whole points at this zoom.
    {height:pxEl.clientHeight,
     localization:{ priceFormatter:function(v){ return Math.round(v).toLocaleString() } }}));
  var cnd = px.addSeries(LightweightCharts.CandlestickSeries, {
    upColor:UP, downColor:DN, borderUpColor:UP, borderDownColor:DN,
    wickUpColor:UP, wickDownColor:DN, priceLineVisible:false });
  cnd.setData(D.candles);
  // Losers get an arrow, winners get a dot. An arrow at every entry AND a dot at every
  // exit was 760 marks on 1,857 bars -- a wall of arrows with candles somewhere behind
  // it. Losers are the minority and the thing worth finding, so they carry the weight.
  var mk=[];
  D.marks.forEach(function(t){
    mk.push(t.pnl>=0
      ? {time:t.d, position:'belowBar', color:UP, shape:'circle', size:0.55}
      : {time:t.d, position:'aboveBar', color:DN, shape:'arrowDown',
         text: D.marks.length<=60 ? M(t.pnl) : ''});
  });
  LightweightCharts.createSeriesMarkers(cnd, mk);

  var ptip=$('ptip'), byDate={};
  D.marks.forEach(function(t){ (byDate[t.d]=byDate[t.d]||[]).push(t) });
  var keys=Object.keys(byDate).sort(), stamps=keys.map(function(k){ return Date.parse(k) });
  // The crosshair time arrives as a string, a {year,month,day} object or a UNIX second
  // count depending on series type. Assuming the string made every comparison NaN and the
  // read-out silently never resolved a trade.
  function toMs(t){ if(t==null) return NaN;
    if(typeof t==='number') return t*1000;
    if(typeof t==='string') return Date.parse(t);
    if(typeof t==='object'&&t.year) return Date.UTC(t.year,(t.month||1)-1,t.day||1);
    return NaN }
  function nearest(t){
    var ms=toMs(t); if(isNaN(ms)||!keys.length) return null;
    var lo=0,hi=keys.length-1;
    while(lo<hi){ var m=(lo+hi)>>1; if(stamps[m]<ms) lo=m+1; else hi=m }
    var b=lo; if(lo>0 && Math.abs(stamps[lo-1]-ms)<Math.abs(stamps[lo]-ms)) b=lo-1;
    return Math.abs(stamps[b]-ms)<=6*864e5 ? byDate[keys[b]] : null }
  function pshow(list){
    if(!list){ ptip.innerHTML=D.phint; return }
    ptip.innerHTML=list.map(function(t){
      return '<b>'+t.d+'</b> → '+t.x+' &nbsp;·&nbsp; <span class="'+cl(t.pnl)+'">'+
        M(t.pnl)+'</span> &nbsp;·&nbsp; '+t.l+' lot'+(t.l===1?'':'s')+
        ' &nbsp;·&nbsp; margin '+M(t.m)+' &nbsp;·&nbsp; <span class="muted">'+
        t.r.toLowerCase()+', spot '+Math.round(t.s)+'</span>' }).join('<br>');
  }
  px.subscribeCrosshairMove(function(p){ pshow(p&&p.time?nearest(p.time):null) });
  pshow(null);
}

/* ================================================================ SVG panels */
function axisY(g,x0,x1,y,vals,fmt){
  vals.forEach(function(v){ var yy=y(v);
    E('line',{x1:x0,x2:x1,y1:yy,y2:yy,stroke:GRID,'stroke-width':1},g);
    T(fmt(v),x0-6,yy+3.5,'tk',g,'end'); }); }

/* ---- rolling twelve-month return --------------------------------------- */
(function(){
  var h=$('roll'); if(!h||!D.rolling||D.rolling.length<3) return;
  function draw(){
    var W=h.clientWidth||640, H=190, L=52,R=10,T0=12,B=24;
    var s=S(h,W,H), vals=D.rolling.map(function(p){ return p.ret });
    var lo=Math.min.apply(null,vals), hi=Math.max.apply(null,vals);
    var pad=(hi-lo||.1)*.12; lo-=pad; hi+=pad; if(lo>0)lo=0; if(hi<0)hi=0;
    var x=sc(0,vals.length-1,L,W-R), y=sc(lo,hi,H-B,T0);
    var ticks=[lo,(lo+hi)/2,hi]; if(lo<0&&hi>0) ticks=[lo,0,hi];
    axisY(s,L,W-R,y,ticks,function(v){ return nf((v*100).toFixed(0))+'%' });
    var z=y(0);
    var d=vals.map(function(v,i){
      return (i?'L':'M')+x(i).toFixed(1)+' '+y(v).toFixed(1) }).join('');
    var defs=E('defs',{},s);
    var cu=E('clipPath',{id:'clipUp'},defs); E('rect',{x:0,y:0,width:W,height:z},cu);
    var cd=E('clipPath',{id:'clipDn'},defs);
    E('rect',{x:0,y:z,width:W,height:Math.max(0,H-z)},cd);
    // Fill above and below zero separately: a rolling return is a two-sided quantity and
    // one accent-coloured area under it reads as "always this much".
    var closed=d+'L'+x(vals.length-1).toFixed(1)+' '+z+'L'+x(0).toFixed(1)+' '+z+'Z';
    P(closed,{fill:UP,opacity:.13,'clip-path':'url(#clipUp)'},s);
    P(closed,{fill:DN,opacity:.13,'clip-path':'url(#clipDn)'},s);
    E('line',{x1:L,x2:W-R,y1:z,y2:z,stroke:FAINT,'stroke-width':1,opacity:.6},s);
    P(d,{stroke:ACC,'stroke-width':1.8,'stroke-linejoin':'round'},s);
    var n=D.rolling.length;
    [0,Math.floor(n/2),n-1].forEach(function(i){
      T(D.rolling[i].date.slice(0,7),x(i),H-6,'tk',s,
        i===0?'start':(i===n-1?'end':'middle')); });
  }
  draw(); addEventListener('resize',draw);
})();

/* ---- concentration ------------------------------------------------------ */
(function(){
  var h=$('conc'); if(!h||!D.conc||D.conc.length<3) return;
  function draw(){
    var W=h.clientWidth||520, H=190, L=44,R=10,T0=12,B=24;
    var s=S(h,W,H), v=D.conc, n=v.length;
    var x=sc(0,n-1,L,W-R), y=sc(0,1,H-B,T0);
    axisY(s,L,W-R,y,[0,.25,.5,.75,1],function(t){ return (t*100).toFixed(0)+'%' });
    // The 45-degree line is what a perfectly even strategy would draw. The gap between it
    // and the real curve IS the concentration, and without it the curve looks much the
    // same for every strategy ever run.
    P('M'+L+' '+y(0)+'L'+(W-R)+' '+y(1),{stroke:FAINT,'stroke-width':1,
      'stroke-dasharray':'3 3',opacity:.7},s);
    P(line(v,x,y),{stroke:ACC,'stroke-width':2},s);
    var k=Math.min(10,n)-1;
    E('line',{x1:x(k),x2:x(k),y1:y(0),y2:y(v[k]),stroke:WARN,'stroke-width':1,
      'stroke-dasharray':'2 2'},s);
    E('circle',{cx:x(k),cy:y(v[k]),r:3.4,fill:WARN},s);
    T((v[k]*100).toFixed(0)+'% from 10 trades',x(k)+9,y(v[k])-9,'lm',s);
    T('trades, best first',L,H-6,'tk',s);
    T('all '+n,W-R,H-6,'tk',s,'end');
  }
  draw(); addEventListener('resize',draw);
})();

/* ---- return distribution ------------------------------------------------ */
(function(){
  var h=$('hist'); if(!h||!D.hist) return;
  function draw(){
    var W=h.clientWidth||520, H=250, L=36,R=12,T0=22,B=30;
    var s=S(h,W,H), b=D.hist.bins;
    var mx=Math.max.apply(null,b.map(function(z){ return z.n }))||1;
    T0=34;
    var x=sc(0,b.length,L,W-R), y=sc(0,mx,H-B,T0);
    var bw=(x(1)-x(0));
    axisY(s,L,W-R,y,[0,Math.round(mx/2),mx],function(v){ return v });
    b.forEach(function(z,i){
      if(!z.n) return;
      var neg = z.hi<=1e-7;
      E('rect',{x:x(i)+1,y:y(z.n),width:Math.max(1,bw-2),height:(H-B)-y(z.n),
        fill:neg?DN:UP,opacity:neg?.62:.5,rx:1.5},s); });
    // Labels are stacked on two rows and skipped when they would still overlap: a
    // median close to break-even printed the two captions on top of each other.
    var used=[[],[]];
    function mark(v,lab,col,dash){
      var span=b[b.length-1].hi-b[0].lo;
      var i=(v-b[0].lo)/span*b.length;
      var xx=x(Math.max(0,Math.min(b.length,i)));
      E('line',{x1:xx,x2:xx,y1:T0-4,y2:H-B,stroke:col,'stroke-width':1.3,
        'stroke-dasharray':dash||null},s);
      var w=lab.length*5.4;
      for(var r=0;r<2;r++){
        var clash=used[r].some(function(u){ return Math.abs(u[0]-xx)<(u[1]+w)/2+6 });
        if(!clash){ used[r].push([xx,w]); T(lab,xx,r?T0-8:T0-20,'lm',s,'middle'); return } }
    }
    mark(0,'break-even',INK);
    mark(D.hist.median,'median',ACC,'3 2');
    mark(D.hist.p05,'5th pct',DN,'3 2');
    T(nf((b[0].lo*100).toFixed(0))+'%',L,H-8,'tk',s);
    T('return on margin, per trade',(L+W-R)/2,H-8,'tk',s,'middle');
    T('+'+(b[b.length-1].hi*100).toFixed(0)+'%',W-R,H-8,'tk',s,'end');
  }
  draw(); addEventListener('resize',draw);
})();

/* ---- cost waterfall ----------------------------------------------------- */
(function(){
  var h=$('cost'); if(!h||!D.cost) return;
  function draw(){
    var W=h.clientWidth||420, H=186, L=10,R=10,T0=32,B=28;
    var s=S(h,W,H), c=D.cost;
    var steps=[['gross',c.gross,ACC],['slippage',-c.slippage,DN],
               ['charges',-c.charges,DN],['net',c.net,c.net>=0?UP:DN]];
    var hiV=Math.max(c.gross,0,c.net);
    var loV=Math.min(0,c.net,c.gross-c.slippage-c.charges);
    var span=(hiV-loV)||1;
    var y=sc(loV-span*.1,hiV+span*.1,H-B,T0);
    var bw=(W-L-R)/steps.length, run=0, z=y(0);
    E('line',{x1:L,x2:W-R,y1:z,y2:z,stroke:FAINT,'stroke-width':1,opacity:.6},s);
    var prevX=null, prevY=null;
    steps.forEach(function(st,i){
      var xx=L+i*bw+bw*.16, w=bw*.68, a;
      if(st[0]==='gross'||st[0]==='net'){ a=0; run=st[1] }
      else { a=run; run=run+st[1] }
      var y1=y(a), y2=y(run);
      // Without the connector a waterfall is four bars at unexplained heights: the whole
      // point is that each one starts where the last one stopped.
      if(prevX!=null) E('line',{x1:prevX,x2:xx,y1:prevY,y2:prevY,stroke:FAINT,
        'stroke-width':1,'stroke-dasharray':'2 2',opacity:.7},s);
      prevX=xx+w; prevY=(st[0]==='net')?y(0):y2;
      E('rect',{x:xx,y:Math.min(y1,y2),width:w,height:Math.max(1.5,Math.abs(y2-y1)),
        fill:st[2],opacity:.72,rx:2},s);
      T(st[0],xx+w/2,H-14,'tk',s,'middle');
      T((st[1]>0?'+':'')+nf(st[1].toFixed(0)),xx+w/2,Math.min(y1,y2)-6,
        st[1]>=0?'la':'lb',s,'middle'); });
    T('points, whole backtest',W-R,13,'tk',s,'end');
  }
  draw(); addEventListener('resize',draw);
})();

/* ---- evidence minis ----------------------------------------------------- */
(function(){
  var wf=$('evwf');
  if(wf && D.ev.wf && D.ev.wf.length){ (function(){
    function draw(){
      var W=wf.clientWidth||260,H=64,s=S(wf,W,H),f=D.ev.wf;
      var mx=Math.max.apply(null,f.map(function(z){ return Math.abs(z.pnl) }))||1;
      var bw=W/f.length, z=H/2;
      E('line',{x1:0,x2:W,y1:z,y2:z,stroke:FAINT,'stroke-width':1,opacity:.5},s);
      f.forEach(function(o,i){
        var hgt=Math.abs(o.pnl)/mx*(H/2-13);
        E('rect',{x:i*bw+bw*.22,y:o.pnl>=0?z-hgt:z,width:bw*.56,
          height:Math.max(2,hgt),fill:o.pnl>=0?UP:DN,opacity:.72,rx:2},s);
        T('fold '+o.f,i*bw+bw/2,o.pnl>=0?z+14:z-6,'tk',s,'middle'); });
    } draw(); addEventListener('resize',draw); })(); }

  var ci=$('evci');
  if(ci && D.ev.ci){ (function(){
    function draw(){
      var W=ci.clientWidth||260,H=58,s=S(ci,W,H),c=D.ev.ci;
      var lo=Math.min(c[0],0), hi=Math.max(c[1],0), pad=(hi-lo||.01)*.2;
      var x=sc(lo-pad,hi+pad,14,W-14), y=30;
      E('line',{x1:14,x2:W-14,y1:y,y2:y,stroke:GRID,'stroke-width':1},s);
      E('rect',{x:x(c[0]),y:y-7,width:Math.max(2,x(c[1])-x(c[0])),height:14,
        fill:ACC,opacity:.25,rx:3},s);
      E('line',{x1:x(0),x2:x(0),y1:y-14,y2:y+14,stroke:INK,'stroke-width':1.4},s);
      T('0',x(0),y+26,'tk',s,'middle');
      T(nf((c[0]*100).toFixed(1))+'%',x(c[0]),y-12,'lm',s,'middle');
      T(nf((c[1]*100).toFixed(1))+'%',x(c[1]),y-12,'lm',s,'middle');
    } draw(); addEventListener('resize',draw); })(); }

  var os=$('evos');
  if(os && D.ev.oos){ (function(){
    function draw(){
      var W=os.clientWidth||260,H=64,s=S(os,W,H),o=D.ev.oos;
      var mx=Math.max(Math.abs(o.ins),Math.abs(o.outs),1), z=H/2, bw=W/2;
      E('line',{x1:0,x2:W,y1:z,y2:z,stroke:FAINT,'stroke-width':1,opacity:.5},s);
      [['fitted 70%',o.ins,.38],['held-out 30%',o.outs,.8]].forEach(function(p,i){
        var hgt=Math.abs(p[1])/mx*(H/2-13);
        E('rect',{x:i*bw+bw*.2,y:p[1]>=0?z-hgt:z,width:bw*.6,height:Math.max(2,hgt),
          fill:p[1]>=0?UP:DN,opacity:p[2],rx:2},s);
        T(p[0],i*bw+bw/2,p[1]>=0?z+14:z-6,'tk',s,'middle'); });
    } draw(); addEventListener('resize',draw); })(); }
})();

/* ---- every trade, as a scatter ------------------------------------------ */
(function(){
  var h=$('sct'); if(!h||!D.scatter||!D.scatter.length) return;
  var pop=$('spop'), pts=D.scatter, mode='all', hot=-1, geo=null;
  var t0=Date.parse(pts[0].date), t1=Date.parse(pts[pts.length-1].date)||t0+1;
  function pass(p){
    if(mode==='all') return true;
    if(mode==='win') return p.pnl>0;
    if(mode==='loss') return p.pnl<=0;
    return p.reason===mode; }
  function draw(){
    var W=h.clientWidth||760, H=h.clientHeight||340, L=52,R=14,T0=30,B=26;
    var s=S(h,W,H);
    s.setAttribute('style','width:100%;height:'+H+'px');
    var roms=pts.map(function(p){ return p.rom });
    var lo=Math.min.apply(null,roms), hi=Math.max.apply(null,roms);
    var pad=(hi-lo||.1)*.1; lo-=pad; hi+=pad;
    var x=sc(t0,t1,L,W-R), y=sc(lo,hi,H-B,T0);
    var mm=Math.max.apply(null,pts.map(function(p){ return p.margin }))||1;
    var ticks=[lo,lo+(hi-lo)/2,hi]; if(lo<0&&hi>0) ticks=[lo,0,hi];
    axisY(s,L,W-R,y,ticks,function(v){ return nf((v*100).toFixed(0))+'%' });
    E('line',{x1:L,x2:W-R,y1:y(0),y2:y(0),stroke:FAINT,'stroke-width':1,opacity:.65},s);
    geo={x:x,y:y,mm:mm};
    var g=E('g',{},s);
    pts.forEach(function(p,i){
      var on=pass(p), r=3+Math.sqrt(p.margin/mm)*5.5;
      E('circle',{cx:x(Date.parse(p.date)),cy:y(p.rom),r:r,
        fill:p.pnl>=0?UP:DN, opacity:on?(i===hot?.95:.42):.06,
        stroke:i===hot?INK:'none','stroke-width':i===hot?1.4:0},g); });
    var yrs={};
    pts.forEach(function(p){ if(!(p.date.slice(0,4) in yrs))
      yrs[p.date.slice(0,4)]=Date.parse(p.date) });
    Object.keys(yrs).sort().forEach(function(k){
      T(k,x(yrs[k]),H-7,'tk',s,'middle'); });
    // Above the plot, not beside the top tick: anchored 'end' at the axis it ran
    // straight through the first gridline label.
    T('return on margin, per trade',2,13,'tk',s,'start');
  }
  function hit(mx,my){
    if(!geo) return -1;
    var best=-1,bd=1e9;
    pts.forEach(function(p,i){
      if(!pass(p)) return;
      var dx=geo.x(Date.parse(p.date))-mx, dy=geo.y(p.rom)-my, d=dx*dx+dy*dy;
      if(d<bd){ bd=d; best=i } });
    return bd<=26*26?best:-1; }
  h.addEventListener('mousemove',function(e){
    var b=h.getBoundingClientRect(), i=hit(e.clientX-b.left, e.clientY-b.top);
    if(i!==hot){ hot=i; draw() }
    if(i<0){ pop.style.opacity=0; return }
    var p=pts[i];
    pop.innerHTML='<div class="hd">trade #'+p.n+' &middot; '+
      (p.reason||'').toLowerCase()+'</div><b>'+(p.entry||p.date).slice(0,16)+
      '</b> → '+p.date+'<br><span class="'+cl(p.pnl)+'">'+M(p.pnl)+'</span> on '+
      p.lots+' lot'+(p.lots===1?'':'s')+' &nbsp;<b>'+sgn(p.rom)+
      '</b> on margin<br>margin '+M(p.margin)+' &middot; spot '+Math.round(p.spot)+
      (p.dte!=null?' &middot; '+p.dte+' DTE':'')+
      (p.vol!=null?'<br>index vol '+pc(p.vol,0)+' at entry':'');
    pop.style.opacity=1;
    var lx=e.clientX-b.left+16, ly=e.clientY-b.top+14;
    if(lx>b.width-210) lx=e.clientX-b.left-206;
    if(ly>b.height-118) ly=e.clientY-b.top-116;
    pop.style.left=lx+'px'; pop.style.top=ly+'px';
  });
  h.addEventListener('mouseleave',function(){ hot=-1; pop.style.opacity=0; draw() });
  Array.prototype.forEach.call(document.querySelectorAll('[data-f]'),function(b){
    b.onclick=function(){
      Array.prototype.forEach.call(document.querySelectorAll('[data-f]'),function(o){
        o.classList.remove('on') });
      b.classList.add('on'); mode=b.getAttribute('data-f'); hot=-1; pop.style.opacity=0;
      draw();
      var sel=pts.filter(pass);
      var sum=sel.reduce(function(a,p){ return a+p.pnl },0);
      $('sctn').innerHTML=sel.length+' of '+pts.length+' trades &middot; <span class="'+
        cl(sum)+'">'+M(sum)+'</span>'; }; });
  draw(); addEventListener('resize',draw);
})();

/* ---- price-scale mode: rupees, log, or per cent -------------------------- */
// A seven-year index that triples while the strategy halves flattens the strategy into
// the bottom fifth of a shared rupee axis. That IS the finding, so the linear view stays
// the default -- but a log axis gives equal space to equal RATIOS, which is what a reader
// comparing two growth curves is actually trying to see.
Array.prototype.forEach.call(document.querySelectorAll('[data-scale]'),function(b){
  b.onclick=function(){
    if(!eq) return;
    Array.prototype.forEach.call(document.querySelectorAll('[data-scale]'),function(o){
      o.classList.remove('on') });
    b.classList.add('on');
    eq.priceScale('right').applyOptions({mode:+b.getAttribute('data-scale')}); }; });


/* ---- what the position IS, and where the index actually went ------------- */
// THE CHART THE REPORT WAS MISSING. Every other panel describes the RESULT. This one
// describes the bet: the payoff of the position drawn against the distribution of moves
// the index actually made underneath it. A win rate cannot tell "finished just inside"
// from "never came close"; this can, in one look.
(function(){
  var h=$('pay'); if(!h||!D.pos) return;
  var POS=D.pos, MV=D.moves;
  function payoff(pct){
    var S=POS.spot*(1+pct/100), v=POS.credit;
    for(var i=0;i<POS.legs.length;i++){
      var l=POS.legs[i], K=POS.spot*(1+l.k/100);
      var intr = l.t==='CE' ? Math.max(S-K,0) : Math.max(K-S,0);
      v += l.s*intr;                       // s = -1 sold, +1 bought
    }
    return v; }
  function draw(){
    // TWO LANES, not one canvas. Drawn on top of each other, the distribution columns sat
    // inside the payoff's red loss shading -- so trades that finished safely INSIDE the
    // strikes appeared to be standing in the loss zone. A hairline and separate scales
    // keep "what the position pays" and "where the index went" as two readings of one
    // x-axis rather than one confused picture.
    var W=h.clientWidth||900, H=356, L=58,R=58,T0=38,B=52;
    var PH=Math.round((H-B-T0)*0.66)+T0;      // payoff lane floor
    var DH=H-B;                               // distribution lane floor
    var s=S(h,W,H);
    // Wide enough to show both wings and the bulk of what the index actually did.
    // Scaled to the extreme tail instead, the whole payoff shape collapsed into the
    // middle fifth of the panel to make room for three outliers.
    var span=Math.max(POS.span*2.4, (MV?MV.p95:0.03)*100*1.25, 1.5);
    var lo=-span, hi=span;
    var pts=[], step=(hi-lo)/240;
    for(var v=lo; v<=hi+1e-9; v+=step) pts.push([v,payoff(v)]);
    var ys=pts.map(function(p){ return p[1] });
    var ymin=Math.min.apply(null,ys), ymax=Math.max.apply(null,ys);
    var pad=(ymax-ymin)*0.18 || 10;
    var x=sc(lo,hi,L,W-R), y=sc(ymin-pad,ymax+pad*0.7,PH,T0);
    var z=y(0);

    var d=pts.map(function(p,i){ return (i?'L':'M')+x(p[0]).toFixed(1)+' '+y(p[1]).toFixed(1) }).join('');
    var defs=E('defs',{},s);
    var cu=E('clipPath',{id:'payUp'},defs);
    E('rect',{x:0,y:T0-6,width:W,height:Math.max(0,z-T0+6)},cu);
    var cd=E('clipPath',{id:'payDn'},defs);
    E('rect',{x:0,y:z,width:W,height:Math.max(0,PH-z)},cd);
    var closed=d+'L'+x(hi)+' '+z+'L'+x(lo)+' '+z+'Z';
    P(closed,{fill:UP,opacity:.18,'clip-path':'url(#payUp)'},s);
    P(closed,{fill:DN,opacity:.15,'clip-path':'url(#payDn)'},s);
    E('line',{x1:L,x2:W-R,y1:z,y2:z,stroke:FAINT,'stroke-width':1,opacity:.7},s);
    P(d,{stroke:INK,'stroke-width':2.2,'stroke-linejoin':'round'},s);

    // strikes, drawn through both lanes so a column can be read against a strike.
    // Labels are staggered on two rows and shortened on a narrow pane: at 390px the four
    // strikes of a condor are ~30px apart and the full captions printed on top of one
    // another.
    var narrow=W<620, used=[[],[]];
    POS.legs.forEach(function(l){
      var xx=x(l.k); if(xx<L-1||xx>W-R+1) return;
      E('line',{x1:xx,x2:xx,y1:T0-6,y2:DH,stroke:l.s<0?ACC:FAINT,'stroke-width':1,
        'stroke-dasharray':l.s<0?null:'3 3',opacity:l.s<0?.6:.5},s);
      var lab = narrow ? ((l.s<0?'sell ':'buy ')+(l.k>0?'+':'')+nf(l.k.toFixed(1)))
                       : ((l.s<0?'sell ':'buy ')+l.t+' '+(l.k>0?'+':'')+nf(l.k.toFixed(1))+'%');
      var w=lab.length*(narrow?5.2:5.8);
      for(var r=0;r<2;r++){
        if(used[r].some(function(u){ return Math.abs(u[0]-xx)<(u[1]+w)/2+5 })) continue;
        used[r].push([xx,w]);
        T(lab,xx,r?T0-11:T0-23,l.s<0?'la':'lm',s,'middle');
        break; } });

    [ymin,0,ymax].forEach(function(v){
      var yy=y(v);
      T(nf(Math.round(v))+(v===ymax?' pts':''),L-8,yy+3.5,'tk',s,'end');
      T(M(v*POS.lot),W-R+8,yy+3.5,'tk',s,'start'); });

    // ---- lane two: where the index actually finished ----
    E('line',{x1:L,x2:W-R,y1:DH,y2:DH,stroke:GRID,'stroke-width':1},s);
    if(MV && MV.bins){
      var mx=Math.max.apply(null,MV.bins.map(function(b){ return b.n }))||1;
      var cap=DH-PH-16;
      MV.bins.forEach(function(b){
        if(!b.n) return;
        var x0=Math.max(L,x(b.lo*100)), x1=Math.min(W-R,x(b.hi*100));
        if(x1<=x0) return;
        var mid=(b.lo+b.hi)/2*100, ht=b.n/mx*cap;
        E('rect',{x:x0,y:DH-ht,width:Math.max(1,x1-x0)-1,height:ht,
          fill:payoff(mid)>=0?UP:DN,opacity:.5,rx:1},s); });
      T('where the index finished',L,PH+13,'tk',s,'start');
      T(MV.clip?'tail beyond the frame not drawn':'',W-R,PH+13,'tk',s,'end');
    }
    T(narrow?'% from where the trade opened'
            :'index at expiry, % from where the trade opened',
      (L+W-R)/2,H-8,'tk',s,'middle');
    if(!narrow){                      // on a phone these ran straight into the caption
      T(nf(lo.toFixed(1))+'%',L,H-8,'tk',s,'start');
      T('+'+hi.toFixed(1)+'%',W-R,H-8,'tk',s,'end'); }
  }
  draw(); addEventListener('resize',draw);
})();

/* ---- best and worst trades, as one diverging bar chart ------------------- */
(function(){
  var h=$('top'); if(!h||!D.top||!D.top.length) return;
  function draw(){
    var W=h.clientWidth||520, rows=D.top.length, rh=22, H=rows*rh+26;
    var s=S(h,W,H);
    var mx=Math.max.apply(null,D.top.map(function(t){ return Math.abs(t.pnl) }))||1;
    var mid=W*0.5, half=W*0.36;
    E('line',{x1:mid,x2:mid,y1:6,y2:H-20,stroke:GRID,'stroke-width':1},s);
    D.top.forEach(function(t,i){
      var yy=8+i*rh, w=Math.abs(t.pnl)/mx*half;
      E('rect',{x:t.pnl>=0?mid:mid-w,y:yy,width:Math.max(1.5,w),height:rh-8,
        fill:t.pnl>=0?UP:DN,opacity:.75,rx:2},s);
      T(t.d,mid+(t.pnl>=0?-8:8),yy+rh-13,'tk',s,t.pnl>=0?'end':'start');
      T(M(t.pnl),t.pnl>=0?mid+w+7:mid-w-7,yy+rh-13,t.pnl>=0?'lg':'lb',s,
        t.pnl>=0?'start':'end'); });
    T('five worst',mid-half,H-5,'tk',s,'start');
    T('five best',mid+half,H-5,'tk',s,'end');
  }
  draw(); addEventListener('resize',draw);
})();

/* ---- leverage: the same trades at six position sizes -------------------- */
(function(){
  var h=$('lev'); if(!h||!D.lev||D.lev.length<2) return;
  function draw(){
    var W=h.clientWidth||520, H=228, L=52,R=52,T0=18,B=32;
    var s=S(h,W,H), v=D.lev;
    var vals=v.map(function(r){ return r.ret }).concat(v.map(function(r){ return r.dd }));
    var lo=Math.min.apply(null,vals), hi=Math.max.apply(null,vals);
    var pad=(hi-lo||.1)*.1; lo-=pad; hi+=pad; if(hi<0) hi=0;
    var x=sc(0,v.length-1,L,W-R), y=sc(lo,hi,H-B,T0);
    axisY(s,L,W-R,y,[lo,0,hi],function(t){ return nf((t*100).toFixed(0))+'%' });
    E('line',{x1:L,x2:W-R,y1:y(0),y2:y(0),stroke:FAINT,'stroke-width':1,opacity:.6},s);
    P(line(v.map(function(r){ return r.dd }),x,y),{stroke:DN,'stroke-width':1.8},s);
    P(line(v.map(function(r){ return r.ret }),x,y),{stroke:ACC,'stroke-width':2.2},s);
    v.forEach(function(r,i){
      var on=r.on;
      E('circle',{cx:x(i),cy:y(r.ret),r:on?5:3.2,fill:ACC,
        stroke:on?SURF:'none','stroke-width':2},s);
      E('circle',{cx:x(i),cy:y(r.dd),r:on?4.5:2.8,fill:DN,
        stroke:on?SURF:'none','stroke-width':2},s);
      T((r.deploy*100).toFixed(0)+'%',x(i),H-11,on?'la':'tk',s,'middle'); });
    T('capital committed per trade',(L+W-R)/2,H-1,'tk',s,'middle');
  }
  draw(); addEventListener('resize',draw);
})();

/* ---- does it survive volatility ----------------------------------------- */
(function(){
  var h=$('volsc'); if(!h||!D.vol||D.vol.length<8) return;
  function draw(){
    var W=h.clientWidth||520, H=268, L=50,R=16,T0=20,B=34;
    var s=S(h,W,H), v=D.vol;
    var xs=v.map(function(p){ return p.v }).slice().sort(function(a,b){return a-b});
    var ys=v.map(function(p){ return p.r });
    // Realised volatility is right-skewed: a handful of crisis readings at 80% squeezed
    // every ordinary trade into the left tenth of the panel and stacked all three band
    // markers on top of each other. Clip at the 96th percentile and pin the rest to the
    // edge, which says "off the scale" without lying about how many there are.
    var xlo=xs[0], xhi=xs[Math.floor(xs.length*0.96)], clipped=0;
    var ylo=Math.min.apply(null,ys), yhi=Math.max.apply(null,ys);
    var yp=(yhi-ylo||.1)*.08; ylo-=yp; yhi+=yp;
    var x=sc(xlo,xhi,L,W-R), y=sc(ylo,yhi,H-B,T0);
    axisY(s,L,W-R,y,[ylo,0,yhi],function(t){ return nf((t*100).toFixed(0))+'%' });
    // the tercile cuts, so a reader can see which band a dot belongs to
    (D.volcuts||[]).forEach(function(c){
      var xx=x(c); if(xx<L||xx>W-R) return;
      E('line',{x1:xx,x2:xx,y1:T0,y2:H-B,stroke:GRID,'stroke-width':1,
        'stroke-dasharray':'3 3'},s); });
    E('line',{x1:L,x2:W-R,y1:y(0),y2:y(0),stroke:FAINT,'stroke-width':1,opacity:.65},s);
    v.forEach(function(p){
      var over=p.v>xhi; if(over) clipped++;
      E('circle',{cx:x(Math.min(p.v,xhi)),cy:y(p.r),r:over?2.6:3.4,
        fill:p.r>=0?UP:DN,opacity:over?.3:.42},s); });
    // the mean of each band, joined -- the trend the cloud is hiding
    if(D.volband && D.volband.length){
      var mp=D.volband.filter(function(b){ return b.n });
      P(mp.map(function(b,i){ return (i?'L':'M')+x(b.x).toFixed(1)+' '+y(b.y).toFixed(1) }).join(''),
        {stroke:INK,'stroke-width':2,'stroke-dasharray':'5 3'},s);
      // Band names under their own marker, not in a row at the top: the markers are not
      // evenly spaced, so a header row labelled the wrong columns.
      mp.forEach(function(b){
        E('circle',{cx:x(b.x),cy:y(b.y),r:5,fill:SURF,stroke:INK,'stroke-width':2},s);
        T(b.name,x(b.x),y(b.y)+(b.y>=0?17:-11),'lm',s,'middle');
        T(sgn(b.y),x(b.x),y(b.y)+(b.y>=0?28:-22),'tk',s,'middle'); }); }
    T('index volatility when the trade opened',(L+W-R)/2,H-6,'tk',s,'middle');
    T(nf((xlo*100).toFixed(0))+'%',L,H-19,'tk',s,'start');
    T(nf((xhi*100).toFixed(0))+'%'+(clipped?'+':''),W-R,H-19,'tk',s,'end');
  }
  draw(); addEventListener('resize',draw);
})();

/* ---- fit the LWC panes, and keep them fitted ---------------------------- */
function fit(){
  if(eq) eq.applyOptions({width:eqEl.clientWidth});
  if(uw) uw.applyOptions({width:uwEl.clientWidth});
  if(px) px.applyOptions({width:pxEl.clientWidth});
}
fit();
if(eq) eq.timeScale().fitContent();
if(px) px.timeScale().fitContent();
setTimeout(function(){ if(eq) eq.timeScale().fitContent();
                       if(px) px.timeScale().fitContent() },0);
addEventListener('resize',fit);

/* ---- section nav -------------------------------------------------------- */
(function(){
  var links=Array.prototype.slice.call(document.querySelectorAll('.nav a'));
  var secs=links.map(function(a){ return document.querySelector(a.getAttribute('href')) })
                .filter(Boolean);
  if(!('IntersectionObserver' in window)||!secs.length) return;
  var io=new IntersectionObserver(function(es){
    es.forEach(function(en){
      if(!en.isIntersecting) return;
      links.forEach(function(a){
        a.classList.toggle('on', a.getAttribute('href')==='#'+en.target.id) }); });
  },{rootMargin:'-64px 0px -70% 0px',threshold:0});
  secs.forEach(function(s){ io.observe(s) });
})();

// Canvas cannot inherit CSS tokens, so the charts have to be told when the theme moves.
if(window.matchMedia) window.matchMedia('(prefers-color-scheme: dark)')
  .addEventListener('change', function(){ setTimeout(function(){ location.reload() },50) });
})();
"""


# ------------------------------------------------------------------- fragments

def _m(k, v, sub="", cls=""):
    tail = f'<div class="s">{_e(sub)}</div>' if sub else ""
    return (f'<div class="m"><div class="k">{_e(k)}</div>'
            f'<div class="v {cls}">{_e(v)}</div>{tail}</div>')


def _band(label):
    return f'<div class="mband">{_e(label)}</div>'


def _dial(score, tone):
    """The health score as a ring. A bare '52/100' reads as a grade; a ring that is half
    empty reads as half the checks failing, which is what it actually means."""
    score = 0 if score is None else max(0, min(100, score))
    r, c = 44.0, 2 * 3.14159265 * 44.0
    col = {"ok": "var(--good)", "warn": "var(--warn)", "bad": "var(--crit)"}[tone]
    return (f'<div class="dial"><svg viewBox="0 0 104 104" aria-hidden="true">'
            f'<circle cx="52" cy="52" r="{r}" fill="none" stroke="var(--sunk)" '
            f'stroke-width="9"/>'
            f'<circle cx="52" cy="52" r="{r}" fill="none" stroke="{col}" stroke-width="9" '
            f'stroke-linecap="round" stroke-dasharray="{c * score / 100:.1f} {c:.1f}" '
            f'transform="rotate(-90 52 52)"/></svg><b>{score}</b></div>')


def _brow(label, note, value, frac, neg=False):
    """One horizontal bar, drawn from a signed fraction of the row's widest value."""
    w = max(1.5, min(100.0, abs(frac) * 50.0))
    left = 50.0 - w if neg else 50.0
    return (f'<div class="br"><div><div class="bl">{_e(label)}</div>'
            f'<div class="bn">{_e(note)}</div></div>'
            f'<div class="bt"><span class="zl" style="left:50%"></span>'
            f'<i class="{"neg" if neg else "pos"}" '
            f'style="left:{left:.2f}%;width:{w:.2f}%"></i></div>'
            f'<div class="bv {"b" if neg else "g"}">{_e(value)}</div></div>')


def _brow_flat(label, note, value, frac, bad=False):
    """A bar measured from the left, for rows where every value has the same sign. The
    centred variant draws a one-sided series as a set of half-empty tracks, which reads as
    a missing value rather than as a small one."""
    w = max(1.5, min(100.0, abs(frac) * 100.0))
    return (f'<div class="br"><div><div class="bl">{_e(label)}</div>'
            f'<div class="bn">{_e(note)}</div></div>'
            f'<div class="bt"><i class="{"neg" if bad else "pos"}" '
            f'style="left:0;width:{w:.2f}%"></i></div>'
            f'<div class="bv {"b" if bad else "g"}">{_e(value)}</div></div>')


def _plural(n, word):
    return f'{n} {word}' + ('' if n == 1 else 's')


def _bars(items):
    """items: [(label, note, value_text, signed_number)] -> a bar block on a shared scale."""
    items = [i for i in items if i]
    if not items:
        return '<div class="note">nothing to show here.</div>'
    mx = max(abs(i[3]) for i in items) or 1.0
    return ('<div class="bars">' +
            "".join(_brow(l, n, v, x / mx, x < 0) for l, n, v, x in items) +
            "</div>")


HEAT = 5


def _heat(v, scale):
    if v is None or abs(v) < 1e-9 or not scale:
        return "nil"
    step = min(HEAT, max(1, int(abs(v) / scale * HEAT) + (abs(v) / scale * HEAT % 1 > 0)))
    return f'{"p" if v > 0 else "n"}{step}'


def _calendar(monthly):
    """Year rows, month columns, shaded by the month's return.

    The single most-scanned object in any fund tearsheet, and the one that answers "would I
    have sat through this" faster than any curve: a row of red across one year is a year of
    explaining yourself, and it is invisible in a rising equity line.
    """
    cells = monthly["cells"]
    if not cells:
        return ""
    by = {}
    for c in cells:
        by.setdefault(c["year"], {})[c["m"]] = c
    scale = max((abs(c["ret"]) for c in cells), default=0.0) or 1.0
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    head = ('<tr><th class="yr"></th>' +
            "".join(f"<th>{m}</th>" for m in months) + '<th>Year</th></tr>')
    rows = []
    for y in sorted(by):
        tds = []
        for m in range(1, 13):
            c = by[y].get(m)
            if c is None:
                tds.append('<td class="nil">·</td>')
            else:
                # A month that lost four hundredths of a per cent prints "-0.0", which
                # reads as a rendering fault rather than as a flat month.
                v = c["ret"] * 100
                txt = "0.0" if abs(v) < 0.05 else f"{v:+.1f}".replace("-", "\u2212")
                tds.append(f'<td class="{_heat(c["ret"], scale)}" '
                           f'title="{y}-{m:02d} · {_rs(c["rs"])}">{txt}</td>')
        ms = [c for c in by[y].values()]
        yr = (ms[-1]["equity"] / (ms[0]["equity"] - ms[0]["rs"]) - 1) if ms else 0.0
        ytxt = f"{yr * 100:+.1f}".replace("-", "\u2212")
        rows.append(f'<tr><td class="yr">{y}</td>{"".join(tds)}'
                    f'<td class="tot {"g" if yr >= 0 else "b"}">{ytxt}</td></tr>')
    key = ('<div class="calkey"><span>monthly return, %</span>'
           '<i style="background:var(--l5)"></i><i style="background:var(--l3)"></i>'
           '<i style="background:var(--l1)"></i><i style="background:var(--sunk)"></i>'
           '<i style="background:var(--h1)"></i><i style="background:var(--h3)"></i>'
           '<i style="background:var(--h5)"></i>'
           f'<span>strongest month on this scale is '
           f'{scale * 100:.1f}%</span></div>')
    return (f'<div class="calwrap"><table class="cal"><thead>{head}</thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>{key}')


def _evidence(h, a):
    """Five checks, each with a picture. The same facts as a table of prose, except that a
    reader can see which one failed without reading any of them."""
    oos = h.get("out_of_sample") or {}
    wf = h.get("walk_forward") or []
    mc = h.get("multiple_comparisons") or {}
    ci = h.get("bootstrap_ci_95_return_on_margin") or []
    cost = a["costs"]
    cards = []

    def card(name, ok, verdict, vis, text, wide=False):
        pill = "p-ok" if ok is True else ("p-no" if ok is False else "p-mid")
        cards.append(f'<div class="evc{" wide" if wide else ""}">'
                     f'<div class="eh"><span class="en">{_e(name)}</span>'
                     f'<span class="pill {pill}">{_e(verdict)}</span></div>{vis}'
                     f'<p class="ed">{text}</p></div>')

    ins, outs = oos.get("in_sample") or {}, oos.get("out_of_sample") or {}
    card("Out of sample", bool(oos.get("held_up")),
         "held up" if oos.get("held_up") else "did not hold",
         '<div id="evos"></div>',
         f'The last 30% of the window was held back and never fitted on. Fitting period '
         f'{_money(ins.get("pnl_rupees"))} over {ins.get("n_trades", "?")} trades; '
         f'held out {_money(outs.get("pnl_rupees"))} over {outs.get("n_trades", "?")}. '
         f'Split by time, never at random — a random split leaks the future into the past '
         f'across overlapping option cycles.')

    prof = sum(1 for f in wf if f.get("profitable"))
    worst = min((f.get("pnl_rupees", 0) for f in wf), default=None)
    card("Walk-forward", (bool(wf) and prof == len(wf)) if wf else None,
         f"{prof} of {len(wf)}", '<div id="evwf"></div>',
         f'Each fold is judged only on data after the one before it. Worst fold '
         f'{_money(worst)}. The worst fold matters more than the total: everything earned '
         f'in one fold is a different proposition from earning it steadily.')

    if len(ci) == 2:
        spans = ci[0] < 0 < ci[1]
        card("Bootstrap interval", not spans,
             "spans zero" if spans else "excludes zero", '<div id="evci"></div>',
             f'Resampling the trades 2,000 times puts 95% of outcomes between '
             f'{ci[0] * 100:.2f}% and {ci[1] * 100:.2f}% return on margin. An interval '
             f'that spans zero means even the SIGN of the edge is not established by this '
             f'sample.')

    dsp = mc.get("deflated_sharpe_probability")
    card("Multiple comparisons", None if dsp is None else dsp >= 0.5,
         "—" if dsp is None else f"{dsp * 100:.0f}%",
         f'<div class="br" style="margin:2px 0 0"><div><div class="bl">deflated</div>'
         f'<div class="bn">{mc.get("variants_tested_last_24h", 0)} variants in 24h</div>'
         f'</div><div class="bt"><i class="pos" style="left:0;'
         f'width:{(dsp or 0) * 100:.0f}%"></i></div>'
         f'<div class="bv">{"—" if dsp is None else f"{dsp * 100:.0f}%"}</div></div>',
         f'{_e((mc.get("reading") or "").rstrip("."))}. The more variants you try, the '
         f'better the best of them looks by luck alone; this is the correction for having '
         f'searched.',
         wide=True)

    surv = cost.get("surviving")
    card("Costs", None if surv is None else surv > 0.4,
         "exceed the edge" if (surv is None or surv <= 0) else f"{surv * 100:.0f}% survives",
         '<div id="cost"></div>',
         f'{_num(cost["gross"], 0)} points of gross edge, less '
         f'{_num(cost["slippage"], 0)} slippage and {_num(cost["charges"], 0)} of charges, '
         f'leaves {_num(cost["net"], 0)}. Each trade must clear '
         f'{_num(cost["breakeven_points"], 1)} points before it earns anything. Most '
         f'published options backtests skip this step entirely.', wide=True)
    # Costs before multiple comparisons: it is the check that most often decides the
    # answer, and it carries the only chart in this row.
    return "".join(cards[:3] + cards[4:] + cards[3:4])


def _series(points, key="equity"):
    """LWC needs strictly ascending, unique times. Several trades can close on one date,
    and setData throws on the duplicate rather than merging it -- so the last value of any
    day wins, which is what the account was actually worth at the close."""
    out = {}
    for p in points:
        out[p["date"][:10]] = round(p[key], 2)
    return [{"time": t, "value": v} for t, v in sorted(out.items())]


# ----------------------------------------------------------------------- render

NAV = (("verdict", "Verdict"), ("position", "How it works"), ("account", "Account"),
       ("calendar", "Calendar"), ("risk", "Risk"), ("market", "Market"),
       ("trades", "Trades"), ("evidence", "Evidence"))


def _as_strategy(spec):
    """Parse an open leg-list spec so the report can describe what was actually written.
    Returns None for a preset spec, and for anything that no longer parses -- a stored
    result must still render even if the protocol has moved on since."""
    if not isinstance(spec, dict) or "legs" not in spec:
        return None
    try:
        from engine import strategy as strategy_mod
        return strategy_mod.parse(dict(spec, period={}),
                                  window=(dt.date(2000, 1, 1), dt.date(2100, 1, 1)))
    except Exception:
        return None


def _open_rules(st):
    """The strategy as written: its legs, then its rules, in the author's own order."""
    from engine import strategy as strategy_mod
    legs = "; ".join(
        f'{l["side"]} {"" if l["qty"] == 1 else str(l["qty"]) + "x "}{l["type"]} at '
        f'{strategy_mod._strike_words(l["strike"])}'
        + ("" if l["expiry"] == "near" else f' ({l["expiry"]} expiry)')
        for l in st.legs)
    out = [("Position", legs),
           ("Enters", ("every session" if st.cadence == "daily"
                       else f"every {st.cadence} expiry, {st.entry_dte}d out")
            + f", at {strategy_mod.fmt_minute(st.entry_minute)}"
            + (f" — only if {strategy_mod._cond_words(st.entry_when)}"
               if st.entry_when else ""))]
    if st.rules:
        out.append(("Manages", " · ".join(
            f'{strategy_mod._cond_words(r["when"])} → '
            f'{strategy_mod._action_words(r["then"])}' for r in st.rules)))
    tail = []
    if st.exit_minute is not None:
        tail.append(f"squared off at {strategy_mod.fmt_minute(st.exit_minute)}")
    tail.append("otherwise held to expiry")
    out.append(("Exits", ", ".join(tail)))
    if st.portfolio:
        out.append(("Book rules", ", ".join(
            f"{k.replace('_', ' ')} {v}" if v is not True else k.replace("_", " ")
            for k, v in st.portfolio.items())))
    return out


def rules_short(spec):
    """The rule as four short phrases, not four paragraphs.

    The long form is still in the footer for anyone who wants it. Up here the payoff
    diagram is carrying the explanation, and a wall of prose beside a picture that already
    says the same thing is the reader's third-favourite way to be told something.
    """
    p = spec.get("params") or {}
    out = []
    if spec.get("cadence", "weekly") == "weekly":
        dte = p.get("entry_dte"); ndb = p.get("entry_days_before")
        out.append(("Enters", f"every weekly expiry"
                    + (f", T-{ndb}" if ndb is not None else
                       (f", {dte}d out" if dte is not None else ""))
                    + f", {spec.get('entry_time', '09:15')}"
                    + (f", {spec['overlay']} filter" if spec.get("overlay") else "")))
    else:
        limit = spec.get("max_dte")
        out.append(("Enters", f"every session, {spec.get('entry_time', '09:15')}"
                    + (", expiry day only" if limit == 0
                       else f", within {limit}d of expiry" if limit is not None else "")))
    strikes = []
    if p.get("pct_offset") is not None:
        strikes.append(f"{p['pct_offset']}% from spot")
    if p.get("pct_width") is not None:
        strikes.append(f"wings {p['pct_width']}% further")
    out.append(("Strikes", ", ".join(strikes) or "at the money"))
    ex = []
    if spec.get("exit_time"):
        ex.append(f"squared off {spec['exit_time']}")
    if p.get("sl_mult") is not None:
        ex.append(f"stop at {p['sl_mult']}× credit")
    if p.get("sl_pct") is not None:
        ex.append(f"stop at −{p['sl_pct'] * 100:.0f}%")
    if p.get("tp_pct") is not None:
        ex.append(f"target {p['tp_pct'] * 100:.0f}% of credit")
    if not spec.get("exit_time"):
        ex.append("otherwise held to expiry" if ex else "held to expiry")
    out.append(("Exits", ", ".join(ex)))
    gate = spec.get("gate", "always")
    out.append(("Filter", "none — every cycle is taken" if gate in (None, "always")
                else GATE_PROSE.get(gate, gate)))
    return out


def _zone(pos):
    """The profitable range of the index, in words that match the shape.

    "±0.69%" on a long call is wrong in both directions at once: the payoff is one-sided,
    and below the strike there is no profit at any distance.
    """
    be = pos.get("breakevens") or []
    if not be:
        return "—"
    if len(be) == 1:
        # One crossing: profitable on whichever side of it the far tail pays.
        return (f'above +{be[0]:.2f}%' if pos.get("max_profit") is None or be[0] > 0
                else f'below {be[0]:.2f}%'.replace("-", "\u2212"))
    lo, hi = min(be), max(be)
    if abs(abs(lo) - hi) < 0.02:
        return f'±{hi:.2f}%'
    return f'{lo:.2f}% to +{hi:.2f}%'.replace("-", "\u2212", 1)


def _strip(items):
    """A row of figures with room to breathe. Used where four numbers ARE the answer --
    the payoff's own limits, say -- and a card each would be four times the furniture."""
    cells = "".join(f'<div><div class="k">{_e(k)}</div>'
                    f'<div class="v {c}">{_e(v)}</div></div>' for k, v, c in items)
    return f'<div class="strip">{cells}</div>'


def _score(h):
    """The health score taken apart, as bars. The number in the dial is an average of
    these five, and an average is the one form in which a total failure and a near miss
    look identical."""
    bd = h.get("score_breakdown") or {}
    rub = h.get("rubric") or []
    if not bd or not rub:
        return ""
    rows = []
    for r in rub:
        name = r["component"].replace("_", " ")
        got, mx = bd.get(r["component"], 0), r["max_points"]
        pct = got / mx if mx else 0
        tone = "pos" if pct >= 0.6 else "neg"
        rows.append(
            f'<div class="sc"><div class="scn">{_e(name)}</div>'
            f'<div class="sct"><i class="{tone}" style="width:{pct * 100:.0f}%"></i></div>'
            f'<div class="scv">{got}<span>/{mx}</span></div></div>')
    return f'<div class="scw">{"".join(rows)}</div>'


def render(payload, chart_rows, candles, backtest_id, capital=sizing.DEFAULT_CAPITAL,
           deploy=sizing.DEFAULT_DEPLOY, report_url=None, replay_url=None):
    """The whole page. A PURE FUNCTION of the backtest payload, the price-free trade rows,
    the index candles and the sizing choice -- which is what lets the design iterate in a
    fifth of a second against a frozen fixture instead of re-running the strategy."""
    s = payload.get("summary") or {}
    h = payload.get("honesty") or {}
    spec = payload.get("spec") or {}
    interp = payload.get("interpretation") or {}
    ratios = s.get("ratios") or {}

    a = analytics.build(chart_rows, candles, capital=capital, deploy=deploy)
    if a.get("empty"):
        return _empty(spec, backtest_id)

    sized, q, dist = a["sized"], a["quality"], a["distribution"]
    cost, conc, mg, eps = a["costs"], a["concentration"], a["margin"], a["episodes"]
    bench, mon, moves = a["benchmark"], a["monthly"], a["moves"]
    pos = position(spec, chart_rows, moves)
    n = len(a["scatter"])

    verdict = h.get("verdict", "")
    tone = VERDICT_TONE.get(verdict, "warn")
    health = h.get("health_score")
    # An open leg list has no `structure` key. Name it from its own shape and use the
    # author's own name when they gave one -- calling a jade lizard "Strategy" is the
    # report failing to read the thing it is describing.
    written = _as_strategy(spec)
    name = (spec.get("structure") or (written.structure if written else "strategy")) \
        .replace("_", " ").title()
    if spec.get("name"):
        name = str(spec["name"])
    ret = sized["return_pct"]

    # ---- masthead ----------------------------------------------------------
    glance = f"""<aside class="glance">
  <div class="gtop">{_dial(health, tone)}
    <div><div class="gname">{_e(verdict.replace("_", " ").capitalize() or "Unrated")}</div>
      <div class="gsub">{_e(str(health))}/100 on a published rubric</div></div></div>
  <div class="gfig">
    <div class="gf"><div class="k">Ending capital</div>
      <div class="v {"g" if (ret or 0) >= 0 else "b"}">{_money(sized["ending_capital"])}</div></div>
    <div class="gf"><div class="k">CAGR</div>
      <div class="v {"g" if (sized["cagr_pct"] or 0) >= 0 else "b"}">{_pct(sized["cagr_pct"], 1)}</div></div>
    <div class="gf"><div class="k">Worst drawdown</div>
      <div class="v b">{_fpct(q["max_dd"], 1)}</div></div>
    <div class="gf"><div class="k">Under water</div>
      <div class="v">{_days(q["max_underwater_days"])}</div></div>
  </div>
</aside>"""

    fnd = "".join(
        f'<div class="fn {t}"><span class="dot"></span><div><b>{_e(head)}</b>'
        f'<span>{_e(det)}</span></div></div>'
        for t, head, det in findings(a, h, s, capital)[:4])

    # ---- the position ------------------------------------------------------
    if pos:
        pos_strip = _strip([
            ("Profit zone", _zone(pos), ""),
            # A bought position pays a debit; calling it a credit and printing it negative
            # asks the reader to do the sign in their head to learn they spent money.
            ("Credit collected" if pos["credit"] >= 0 else "Premium paid",
             f'{abs(pos["credit"]):.0f} pts', ""),
            ("Most it can make", (f'{pos["max_profit"]:.0f} pts'.replace("-", "\u2212")
                                  if pos["max_profit"] is not None else "open-ended"), "g"),
            ("Most it can lose", (f'{pos["max_loss"]:.0f} pts'.replace("-", "\u2212")
                                  if pos["max_loss"] is not None else "unlimited"), "b"),
            ("Finished in profit", _fpct(pos["inside"], 0), ""),
        ])
    else:
        pos_strip = ""

    rules_strip = "".join(
        f'<div class="rl"><div class="rk">{_e(k)}</div>'
        f'<div class="rv">{_e(v)}</div></div>'
        for k, v in (_open_rules(written) if written else rules_short(spec)))

    # ---- payloads for the SVG panels --------------------------------------
    ranked = sorted(a["scatter"], key=lambda p: p["pnl"])
    top = ([{"d": p["date"][:10], "pnl": round(p["pnl"])} for p in ranked[:5]] +
           [{"d": p["date"][:10], "pnl": round(p["pnl"])} for p in ranked[-5:]])

    sens = sizing.sensitivity(chart_rows, capital=capital)
    lev = [{"deploy": r["deploy"], "ret": (r["return_pct"] or 0) / 100.0,
            "dd": (r["max_drawdown_pct"] or 0) / 100.0,
            "on": abs(r["deploy"] - deploy) < 1e-9} for r in sens]

    reg = a["regimes"]
    volpts = [{"v": round(p["vol"], 4), "r": round(p["rom"], 4)}
              for p in a["scatter"] if p["vol"] is not None]
    volband = []
    if reg and volpts:
        cuts = reg["cuts"]
        for nm, lo, hi in (("calm", -1, cuts[0]), ("normal", cuts[0], cuts[1]),
                           ("stressed", cuts[1], 9)):
            grp = [p for p in volpts if lo <= p["v"] < hi]
            if grp:
                volband.append({"name": nm, "n": len(grp),
                                "x": round(sum(p["v"] for p in grp) / len(grp), 4),
                                "y": round(sum(p["r"] for p in grp) / len(grp), 4)})

    equity = _series(a["equity"])
    if len(a["equity"]) > 1 and a["equity"][0]["date"][:10] == a["equity"][1]["date"][:10]:
        # The seed point shares a date with the first close, and setData would drop one of
        # them -- so the account would appear to start at its first outcome rather than at
        # the capital it was given.
        seed = (dt.date(*map(int, a["equity"][0]["date"][:10].split("-")))
                - dt.timedelta(days=1)).isoformat()
        equity = [{"time": seed, "value": capital}] + equity

    data = {
        "capital": capital,
        "equity": equity,
        "uw": _series(a["underwater"], "dd"),
        "maxdd": round(q["max_dd"], 4),
        "maxddlabel": (f'{_fpct(q["max_dd"], 1)} · {eps[0]["trough"]}' if eps else ""),
        "bench": ([{"time": p["date"], "value": round(p["equity"])}
                   for p in bench["series"]] if bench else []),
        "candles": candles,
        "marks": [{"d": (p["entry"] or p["date"])[:10], "x": p["date"], "pnl": p["pnl"],
                   "l": p["lots"], "m": p["margin"], "r": p["reason"] or "",
                   "s": p["spot"] or 0} for p in a["scatter"]],
        "scatter": [{"n": p["n"], "date": p["date"], "entry": p["entry"],
                     "rom": round(p["rom"], 5), "pnl": round(p["pnl"]),
                     "lots": p["lots"], "margin": round(p["margin"]),
                     "dte": p["dte"], "reason": p["reason"] or "",
                     "spot": p["spot"], "vol": round(p["vol"], 4) if p["vol"] else None}
                    for p in a["scatter"]],
        "rolling": [{"date": p["date"], "ret": round(p["ret"], 5)} for p in a["rolling"]],
        "conc": [round(x, 4) for x in conc["share_curve"]],
        "hist": ({"bins": [{"lo": round(b["lo"], 4), "hi": round(b["hi"], 4), "n": b["n"]}
                           for b in dist["bins"]],
                  "median": round(dist["median"], 4), "p05": round(dist["p05"], 4)}
                 if dist else None),
        "cost": {"gross": round(cost["gross"], 1), "slippage": round(cost["slippage"], 1),
                 "charges": round(cost["charges"], 1), "net": round(cost["net"], 1)},
        "pos": pos,
        "moves": ({"bins": [{"lo": round(b["lo"], 5), "hi": round(b["hi"], 5),
                             "n": b["n"]} for b in moves["bins"]],
                   "lo": round(moves["lo"], 5), "hi": round(moves["hi"], 5),
                   "p95": round(moves["p95_abs"], 5)} if moves else None),
        "top": top, "lev": lev, "vol": volpts,
        "volcuts": [round(c, 4) for c in (reg["cuts"] if reg else [])],
        "volband": volband,
        "ev": {"wf": [{"f": f.get("fold"), "pnl": f.get("pnl_rupees", 0)}
                      for f in (h.get("walk_forward") or [])],
               "ci": h.get("bootstrap_ci_95_return_on_margin"),
               "oos": ({"ins": ((h.get("out_of_sample") or {}).get("in_sample") or {})
                        .get("pnl_rupees", 0),
                        "outs": ((h.get("out_of_sample") or {}).get("out_of_sample") or {})
                        .get("pnl_rupees", 0)} if h.get("out_of_sample") else None)},
        "ehint": "Hover for the account on any date. Drag to pan, scroll to zoom.",
        "phint": "Hover a candle for the trade nearest it.",
    }

    replay_cta = f"""<a class="cta" href="{_e(replay_url)}">
  <span class="ctai" aria-hidden="true">&#x25B6;</span>
  <span class="ctat"><b>Watch it trade</b>
  <em>play the window back minute by minute &mdash; index, premium, payoff and account,
  moving together</em></span>
  <span class="ctag" aria-hidden="true">&rarr;</span></a>""" if replay_url else ""

    nav = "".join(f'<a href="#{i}">{_e(t)}</a>' for i, t in NAV)
    period = s.get("period") or {}
    rule_rows = "".join(
        f'<div class="rule"><div class="rk">{_e(k)}</div><div class="rv">{v}</div></div>'
        for k, v in rules(spec))
    dont = "".join(f"<li>{_e(x)}</li>" for x in (interp.get("do_not_conclude") or []))

    payoff_box = f"""<div class="chartbox">
    <div class="chead">
      <div class="ctitle">One trade at expiry, drawn against every move the index
        actually made</div>
      <div class="chint">below: where the index actually finished, coloured by
        whether that paid</div></div>
    <div id="pay"></div>
  </div>
  {pos_strip}""" if pos else ""

    ladder = f"""<div class="pctl">
        <div><div class="k">5th</div>
          <div class="v {_cls(dist["p05"])}">{_fpct(dist["p05"], 0, True)}</div></div>
        <div><div class="k">25th</div>
          <div class="v {_cls(dist["p25"])}">{_fpct(dist["p25"], 0, True)}</div></div>
        <div><div class="k">median</div>
          <div class="v {_cls(dist["median"])}">{_fpct(dist["median"], 0, True)}</div></div>
        <div><div class="k">75th</div>
          <div class="v {_cls(dist["p75"])}">{_fpct(dist["p75"], 0, True)}</div></div>
        <div><div class="k">95th</div>
          <div class="v {_cls(dist["p95"])}">{_fpct(dist["p95"], 0, True)}</div></div>
      </div>""" if dist else ""

    risk_left = f"""<div class="chartbox s7">
      <div class="chead"><div class="ctitle">Every trade's return on its own margin</div>
        <div class="chint">size-independent, so this is the strategy not the bet size</div>
      </div>
      <div id="hist"></div>
      {ladder}
    </div>""" if dist else ""

    rolling_box = ("""<div class="chartbox" style="margin-top:13px">
    <div class="chead"><div class="ctitle">Trailing twelve-month return</div>
      <div class="chint">an equity curve rising for years can hide an edge that stopped
        working in year five</div></div>
    <div id="roll"></div>
  </div>""" if len(a["rolling"]) >= 3 else
        '<p class="note" style="margin-top:14px">This window is too short to have a full '
        'year behind any of its closes, so no trailing twelve-month return is drawn.</p>')

    dte_bars = _bars([(str(b["key"]), f'{_plural(b["n"], "trade")} · '
                       f'{_fpct(b["hit"], 0)} won', _money(b["pnl"]), b["pnl"])
                      for b in a["buckets"]["dte"]]) if a["buckets"]["dte"] else ""

    reasons = sorted({p["reason"] for p in a["scatter"] if p.get("reason")})
    filters = ('<button class="fbtn on" data-f="all">all</button>'
               '<button class="fbtn" data-f="win">winners</button>'
               '<button class="fbtn" data-f="loss">losers</button>' +
               ("".join(f'<button class="fbtn" data-f="{_e(r)}">{_e(r.lower())}</button>'
                        for r in reasons) if len(reasons) > 1 else ""))

    body = f"""<div class="bar"><div class="barin">
  <span class="bmark">
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <rect x="1" y="12" width="4.6" height="7" rx="1.2" fill="var(--accent)" opacity=".45"/>
      <rect x="7.7" y="7" width="4.6" height="12" rx="1.2" fill="var(--accent)" opacity=".72"/>
      <rect x="14.4" y="1" width="4.6" height="18" rx="1.2" fill="var(--accent)"/>
    </svg><b>{_e(name)}</b><span>Stratify</span></span>
  <nav class="nav">{nav}</nav>
  <span class="chip {tone}">{_e(verdict.replace("_", " ") or "unrated")}</span>
</div></div>

<div class="wrap">
<header class="mast">
  <div>
    <div class="kick">Stratify · strategy report</div>
    <h1>{_e(name)} on NIFTY weekly options</h1>
    <p class="sub">{_e(STRUCTURE_PROSE.get(spec.get("structure", "")) or
      (f"{written.n_legs} legs, "
       f"{len(written.rules)} management rule{'' if len(written.rules) == 1 else 's'}, "
       f"shaped like a {written.structure.replace('_', ' ')}." if written else ""))}</p>
    <div class="mmeta">
      <span>{_e(period.get("from", "?"))} → {_e(period.get("to", "?"))}</span>
      <span>{n:,} trades</span>
      <span>{sized["years"]} years</span>
      <span>net of all costs</span>
    </div>
  </div>
  {glance}
</header>

<section id="verdict">
  <p class="vhead">{_money(sized["net_pnl"])} on {_money(capital)} —
    <span class="{"g" if (ret or 0) >= 0 else "b"}">{_pct(ret)}</span>
    over {sized["years"]} years, across {n:,} trades.</p>
  <div class="lay">
    <div class="card s4"><div class="ct">Where the score comes from</div>
      {_score(h)}
      <p class="note" style="margin-top:12px">Each bar is one check, drawn below.</p>
    </div>
    <div class="s8"><div class="fnd">{fnd}</div></div>
  </div>
</section>

<section id="position">
  <div class="sh"><h2>How it works</h2>
    <span class="sn">the bet, drawn to scale</span></div>
  {payoff_box}
  <div class="rlgrid">{rules_strip}</div>
</section>

<section id="account">
  <div class="sh"><h2>The account</h2>
    <span class="sn">equity above, distance from its own high below</span></div>
  <div class="chartbox">
    <div class="chead"><div class="ctitle">{_money(capital)} through {sized["years"]} years</div>
      <div class="filters" style="margin:0">
        <button class="fbtn on" data-scale="0">₹</button>
        <button class="fbtn" data-scale="1">log</button>
        <button class="fbtn" data-scale="2">%</button>
      </div></div>
    <div id="eq"></div>
    <div id="uw"></div>
    <div class="legend">
      <span><i class="ln" style="background:var(--accent)"></i>this strategy</span>
      <span><i class="ln" style="background:var(--faint)"></i>NIFTY bought and held</span>
      <span><i style="background:var(--crit);opacity:.5"></i>below a previous high</span>
    </div>
    <div class="tip" id="etip"></div>
  </div>
  <div class="lay" style="margin-top:13px">
    <div class="s5">{_strip([
        ("Sharpe", _num(ratios.get("sharpe"), 2), ""),
        ("Profit factor", _num(q["profit_factor"], 2),
         "g" if (q["profit_factor"] or 0) > 1 else "b"),
        ("Per trade", _rs(q["expectancy_rs"]), _cls(q["expectancy_rs"])),
        ("Peak margin", _money(mg["peak_rs"]), ""),
    ])}
      <p class="note" style="margin-top:13px">Position size is a <b>view</b>, not a second
      backtest: the engine trades one lot and the capital model decides how many.
      {sized["lots_median"]} lots was typical here, at {sized["deploy_pct"]:.0f}% of running
      capital per trade.</p>
    </div>
    <div class="chartbox s7">
      <div class="chead"><div class="ctitle">The same trades at six position sizes</div>
        <div class="chint">return in teal, worst drawdown in red</div></div>
      <div id="lev"></div>
      <p class="note">A strategy has a return per unit of leverage, not a return. Too small
      and costs dominate; too large and a drawdown you would have ridden out compounds
      against you.</p>
    </div>
  </div>
</section>

<section id="calendar">
  <div class="sh"><h2>Month by month</h2>
    <span class="sn">{mon["positive"]} of {mon["total"]} months positive</span></div>
  <div class="card">{_calendar(mon)}</div>
  {rolling_box}
</section>

<section id="risk">
  <div class="sh"><h2>Risk</h2>
    <span class="sn">what a win rate cannot tell you</span></div>
  <div class="lay">
    {risk_left}
    <div class="chartbox s5">
      <div class="chead"><div class="ctitle">Against index volatility</div>
        <div class="chint">bands cut on the market, not the trades</div></div>
      <div id="volsc"></div>
      <p class="note">Each dot is a trade. The dashed line joins the average of each
      volatility band — a strategy that only works in calm markets slopes down it.</p>
    </div>
  </div>
  <div class="lay" style="margin-top:13px">
    <div class="chartbox s5">
      <div class="chead"><div class="ctitle">Share of all profit, best trades first</div>
        <div class="chint">dashed = perfectly even</div></div>
      <div id="conc"></div>
      <p class="note">Ten trades made {_fpct(conc["top10_of_profit"], 0)} of the gross
      profit. Drop the best five and re-run the capital model and the account ends at
      {_money(conc["ex_best5"])} instead of {_money(sized["ending_capital"])}.</p>
    </div>
    <div class="chartbox s7">
      <div class="chead"><div class="ctitle">The five that made it, the five that cost it</div>
        <div class="chint">at the position size above</div></div>
      <div id="top"></div>
    </div>
  </div>
</section>

<section id="market">
  <div class="sh"><h2>The market it traded</h2>
    <span class="sn">every trade on the index</span></div>
  <div class="chartbox">
    <div class="chead"><div class="ctitle">NIFTY daily</div>
      <div class="chint">circles win, arrows lose · scroll to zoom, drag to pan</div></div>
    <div id="px"></div>
    <div class="tip" id="ptip"></div>
  </div>
  {replay_cta}
</section>

<section id="trades">
  <div class="sh"><h2>Every trade</h2>
    <span class="sn" id="sctn">{n:,} trades · {_money(conc["net"])}</span></div>
  <p class="lede">One point each: <b>when</b> it closed, <b>what it returned on its own
    margin</b>, and how large it was. Hover any point for its row.</p>
  <div class="chartbox">
    <div class="filters">{filters}</div>
    <div class="sctwrap"><div id="sct"></div><div class="pop" id="spop"></div></div>
    <div class="legend">
      <span><i class="rd" style="background:var(--good)"></i>winner</span>
      <span><i class="rd" style="background:var(--crit)"></i>loser</span>
      <span style="color:var(--faint)">point size is the margin committed</span>
    </div>
  </div>
  {f'''<div class="lay" style="margin-top:13px"><div class="card s6">
    <div class="ct">By days to expiry at entry<em>rupees at the size above</em></div>
    <div style="margin-top:13px">{dte_bars}</div></div></div>''' if dte_bars else ""}
</section>

<section id="evidence">
  <div class="sh"><h2>Does the evidence hold</h2>
    <span class="sn">five checks, run on this backtest</span></div>
  <div class="ev">{_evidence(h, a)}</div>
</section>

<footer class="foot">
  <div class="sh" style="margin-bottom:12px"><h2>The rule, in full</h2></div>
  <div class="rules">{rule_rows}</div>
  {f'<div class="sh" style="margin:26px 0 12px"><h2>What this does not show</h2></div>'
   f'<ul class="pts">{dont}</ul>' if dont else ""}
  {design.MARK_HTML}
  <p><strong>Not investment advice.</strong> Backtested results are hypothetical, computed
  under the stated assumptions, and carry no guarantee that any strategy will achieve them.
  Position sizing here is a model, not a broker statement: real margin is set by the
  exchange and your broker and moves intraday.</p>
  <p>Entry and exit at the traded price on the 1-minute bar, not the midpoint. Stops and
  targets evaluated on 1-minute bars. Expiry settles on the mean of the index over its
  final 30 minutes. Lot size is the one in force on each trade date. {_e(sized["basis"])}
  Every panel on this page is computed from price-free trade rows; per-leg option prices
  are released only in the tool response, under a per-account budget.
  {f"Source of record: {_e(report_url)}" if report_url else ""}
  <br>{_e(backtest_id)}</p>
</footer>
</div>"""

    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="color-scheme" content="light dark">'
            f'<title>Stratify · {_e(name)} · {_pct(ret)}</title>'
            f'<style>{CSS}</style></head><body>{body}'
            f'<script>{LWC}</script>'
            f'<script>window.__STRATIFY__='
            f'{json.dumps(data, separators=(",", ":"), default=str)};</script>'
            f'<script>{JS}</script></body></html>')


def _empty(spec, backtest_id):
    name = spec.get("structure", "strategy").replace("_", " ").title()
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Stratify · {_e(name)}</title><style>{CSS}</style></head>'
            f'<body><div class="wrap"><header class="mast">'
            f'<div class="kick">Stratify · strategy report</div>'
            f'<h1>{_e(name)} produced no trades</h1>'
            f'<p class="sub">Nothing was entered in this window, so there is nothing to '
            f'measure. Widen the period, relax the filter, or check that the cadence and '
            f'the days-to-expiry limit can both be satisfied.</p>'
            f'<div class="mmeta"><span>{_e(backtest_id)}</span></div></header>'
            f'<footer class="foot">{design.MARK_HTML}</footer></div></body></html>')
