"""The report iteration loop: capture the data once, re-render the page in milliseconds.

THE PROBLEM THIS SOLVES. A full report is built from four things -- the backtest payload,
every trade in a price-free form, the index candles, and the sizing choice. Three of those
cost real time: the backtest re-runs the strategy over seven and a half years of 1-minute
option bars, and the candles are a ClickHouse aggregate over the same window. Together they
are a few seconds and a live database. The rendering is 50 milliseconds of string
formatting.

Iterating on the LAYOUT should therefore never pay for the DATA. So the two are split:

    capture   run the strategy once, freeze its four inputs into a JSON fixture   ~3 s, DB
    build     load the fixture, call fullreport.render, write the HTML          ~0.05 s, no DB
    watch     rebuild on every save, and the open page reloads itself             continuous

`fullreport.render` is a pure function of those four inputs, which is what makes this work
at all -- and is a good reason to keep it that way.

WHAT THE FIXTURE IS NOT. It is a development artefact, not a cache the product reads. The
live report path in tools.py always re-runs the spec, because a stored fixture would
silently serve yesterday's data under today's date.
"""
import argparse
import json
import os
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
FIXTURES = HERE / "state" / "fixtures"
DEFAULT_OUT = pathlib.Path("/var/www/ops/report-demo")

# Named strategies to iterate against. The first is the demo: a real, ordinary,
# not-especially-good strategy, which is the honest case to design the page around. A
# report that only looks right on a winner is a brochure.
DEMO_SPECS = {
    "demo": {
        "structure": "iron_condor",
        "params": {"pct_offset": 1.0, "pct_width": 1.0, "entry_dte": 2},
        "entry_time": "09:30",
        "cadence": "weekly",
        "gate": "always",
        "period": {"from": "2019-01-01", "to": "2026-06-30"},
    },
    "strangle": {
        "structure": "short_strangle",
        "params": {"pct_offset": 1.5, "entry_dte": 2, "sl_mult": 2.0},
        "entry_time": "09:30",
        "cadence": "weekly",
        "period": {"from": "2019-01-01", "to": "2026-06-30"},
    },
    # The open protocol, exercised end to end: a delta-selected strangle that rolls the
    # tested side, hedges the other, trails a stop, and stands down after three losers.
    # If the report can draw this it can draw anything the protocol can say.
    "adaptive": {
        "name": "Adaptive delta strangle",
        "legs": [{"side": "sell", "type": "CE", "strike": {"delta_near": 0.18}},
                 {"side": "sell", "type": "PE", "strike": {"delta_near": 0.18}}],
        "entry": {"cadence": "weekly", "dte": 3, "time": "09:20",
                  "when": {"combined_premium": {"gte": 40}}},
        "rules": [
            {"label": "roll the tested call out",
             "when": {"spot_beyond_strike": {"leg": 0, "gte": -30}},
             "then": {"roll": {"legs": [0], "to": {"pct_offset": 1.2, "ref": "now"}}},
             "max_times": 2},
            {"label": "roll the tested put out",
             "when": {"spot_beyond_strike": {"leg": 1, "gte": -30}},
             "then": {"roll": {"legs": [1], "to": {"pct_offset": -1.2, "ref": "now"}}},
             "max_times": 2},
            {"label": "trail once well in profit",
             "when": {"all": [{"pnl_pct_of_credit": {"gte": 0.6}},
                              {"drawdown_from_peak": {"gte": 12}}]},
             "then": "close"},
            {"label": "hard stop at twice the credit",
             "when": {"pnl_pct_of_credit": {"lte": -2.0}}, "then": "close"}],
        "max_adjustments": 4,
        "portfolio": {"stop_after_losses": 3, "resume_after_days": 30},
        "period": {"from": "2019-01-01", "to": "2026-06-30"},
    },
    "expiry": {
        "structure": "iron_fly",
        "params": {"pct_width": 1.0},
        "entry_time": "09:30",
        "exit_time": "15:00",
        "cadence": "daily",
        "max_dte": 0,
        "period": {"from": "2019-01-01", "to": "2026-06-30"},
    },
}


def fixture_path(name):
    return FIXTURES / f"{name}.json"


# ---------------------------------------------------------------- capture (slow, needs DB)

def capture(name, spec=None, tier=None):
    """Run the strategy once and freeze everything the report needs onto disk."""
    sys.path.insert(0, str(ROOT))
    from engine import (backtest, db, detail, honesty, index_series, metrics,
                        replay, spec as spec_mod)
    from server import knowledge

    raw = spec if spec is not None else DEMO_SPECS.get(name)
    if raw is None:
        raise SystemExit(f"no spec for {name!r}; pass --spec or use one of "
                         f"{', '.join(DEMO_SPECS)}")

    # The tier sets TWO things and forgetting the second is silent: spec.parse uses it for
    # the date window, and ClickHouse enforces the same window again as a row policy on a
    # per-tier database user. Asking for 2019 with the free DB user returns 2025 onward and
    # no error -- which is how a "seven-year" fixture ends up holding one year.
    db.use_tier(tier)
    t0 = time.time()
    # Same routing as the live tool: a spec carrying "legs" is the open protocol.
    general = "legs" in raw
    if general:
        from engine import simulate, strategy as strategy_mod
        parsed = strategy_mod.parse(raw, window=spec_mod.window_for(tier))
        result = simulate.run(parsed, lots=1)
    else:
        parsed = spec_mod.parse(raw, tier=tier)
        result = backtest.run(parsed, lots=1)
    summary = metrics.summarise(result)
    # log_variant=False: a preview is not a search. Logging it would inflate the
    # multiple-comparisons count on every reload and quietly degrade the honesty panel
    # of the very report being designed.
    panel = honesty.panel(result, summary, api_key_id="preview", log_variant=False)

    payload = {
        "spec": raw,
        "summary": summary,
        "honesty": panel,
        "interpretation": knowledge.interpretation(summary, panel, parsed),
    }
    payload.update(detail.build(result, max_trades=detail.MAX_TRADES_RETURNED))

    chart_rows = detail.rows_for_chart(result)
    if not chart_rows:
        raise SystemExit("that spec produced no trades; nothing to draw")
    period = (summary.get("period") or {})
    candles = index_series.daily(period.get("from") or str(parsed.date_from),
                                 period.get("to") or str(parsed.date_to))

    # The replay tracks: per-trade spot bars and the position's combined premium. Audited
    # on the way out, because a leak here would arrive dressed as a UI feature. A
    # single-leg strategy has no replay at all -- the fixture is still useful for the
    # report, so this is a missing section rather than a failed capture.
    try:
        tracks = replay.build(result)
        audit = replay.release_audit(tracks)
    except (replay.NotReleasable, Exception) as exc:
        tracks, audit = None, {"price_points_released": 0, "refused": str(exc)}
        print(f"  no replay: {exc}")

    FIXTURES.mkdir(parents=True, exist_ok=True)
    doc = {"name": name, "spec": raw, "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "backtest_id": f"preview_{name}", "payload": payload,
           "chart_rows": chart_rows, "candles": candles,
           "replay": tracks, "replay_release": audit}
    fixture_path(name).write_text(json.dumps(doc, default=str))
    kb = fixture_path(name).stat().st_size / 1024
    bars = (tracks or {}).get("spot", {}).get("n", 0)
    ntr = len((tracks or {}).get("trades", []))
    print(f"captured {name}: {len(chart_rows)} trades, {len(candles)} candles, "
          f"{bars:,} index bars over {ntr} replayable trades, {kb:.0f} KB, "
          f"{time.time() - t0:.1f}s -> {fixture_path(name)}")
    return doc


# ------------------------------------------------------------------ build (fast, no DB)

# Injected only by --dev. It polls a stamp file written on every rebuild and reloads when
# it changes, so iterating means saving the Python and looking at the browser.
RELOAD_JS = """<script data-preview-reload>
(function(){var last=null;setInterval(function(){
 fetch('STAMP_URL',{cache:'no-store'}).then(function(r){return r.text()}).then(function(t){
  if(last===null){last=t}else if(t!==last){location.reload()}}).catch(function(){});
 },1200);})();
</script>"""


def build(name, out=DEFAULT_OUT, capital=None, deploy=None, dev=False):
    """Render the fixture. This is the loop -- it must stay fast and DB-free."""
    sys.path.insert(0, str(ROOT))
    # Imported here, and re-imported fresh on every watch tick, so edits take effect.
    from server import fullreport, replay_view, sizing

    path = fixture_path(name)
    if not path.exists():
        raise SystemExit(f"no fixture for {name!r}. Run: python -m server.preview "
                         f"capture --name {name}")
    doc = json.loads(path.read_text())

    cap = capital or sizing.DEFAULT_CAPITAL
    dep = (deploy / 100.0) if deploy else sizing.DEFAULT_DEPLOY

    t0 = time.time()
    html_doc = fullreport.render(
        doc["payload"], doc["chart_rows"], doc["candles"], doc["backtest_id"],
        capital=cap, deploy=dep, report_url=None,
        replay_url=(f"{name}-replay.html"
                    if (doc.get("replay") or {}).get("trades") else None))

    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = f"{name}.stamp"
    if dev:
        banner = (f'<div style="position:fixed;left:0;right:0;bottom:0;z-index:99;'
                  f'font:11px/1.9 ui-monospace,monospace;text-align:center;'
                  f'background:#1c1917;color:#a8a29e;letter-spacing:.06em">'
                  f'PREVIEW &middot; {name} &middot; fixture {doc["captured_at"]} '
                  f'&middot; rebuilt {time.strftime("%H:%M:%S")} &middot; auto-reloads'
                  f'</div>')
        html_doc = html_doc.replace(
            "</body>", banner + RELOAD_JS.replace("STAMP_URL", stamp) + "</body>")
        (out / stamp).write_text(str(time.time()))

    target = out / f"{name}.html"
    target.write_text(html_doc)
    msg = f"built {target}  {len(html_doc) / 1024:.0f} KB"

    if (doc.get("replay") or {}).get("trades"):
        rep = replay_view.render(doc["payload"], doc["replay"], capital=cap,
                                 deploy=dep, report_url=f"{name}.html")
        if dev:
            rep = rep.replace("</body>", RELOAD_JS.replace("STAMP_URL", stamp) + "</body>")
        rtarget = out / f"{name}-replay.html"
        rtarget.write_text(rep)
        msg += f"  +  {rtarget.name}  {len(rep) / 1024:.0f} KB"

    print(f"{msg}  {(time.time() - t0) * 1000:.0f} ms")
    return target


# ------------------------------------------------------------------------------ watch

WATCHED = ("fullreport.py", "analytics.py", "replay_view.py", "design.py",
           "sizing.py", "preview.py")


def watch(name, out=DEFAULT_OUT, capital=None, deploy=None, interval=0.4):
    """Rebuild whenever a file the report is made of changes."""
    files = [HERE / f for f in WATCHED]
    seen = {}
    print(f"watching {', '.join(WATCHED)} -> {out}/{name}.html   (ctrl-c to stop)")
    while True:
        now = {f: f.stat().st_mtime for f in files if f.exists()}
        if now != seen:
            seen = now
            for mod in [m for m in list(sys.modules)
                        if m.startswith("server.") and m.split(".")[-1] + ".py" in WATCHED]:
                del sys.modules[mod]
            try:
                build(name, out=out, capital=capital, deploy=deploy, dev=True)
            except Exception as exc:      # a syntax error must not kill the loop
                print(f"  !! {type(exc).__name__}: {exc}")
        time.sleep(interval)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="server.preview", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("capture", help="run the strategy once and freeze it (slow, needs DB)")
    c.add_argument("--name", default="demo")
    c.add_argument("--spec", help="JSON spec; defaults to the named demo spec")
    c.add_argument("--tier", default="pro", help="date window; pro = full history")

    for cmd, helptext in (("build", "render the fixture once"),
                          ("watch", "render on every save")):
        p = sub.add_parser(cmd, help=helptext)
        p.add_argument("--name", default="demo")
        p.add_argument("--out", default=str(DEFAULT_OUT))
        p.add_argument("--capital", type=int, default=None)
        p.add_argument("--deploy", type=float, default=None, help="percent, e.g. 10")
        if cmd == "build":
            p.add_argument("--dev", action="store_true", help="add the auto-reload banner")

    sub.add_parser("list", help="show captured fixtures")
    a = ap.parse_args(argv)

    if a.cmd == "capture":
        capture(a.name, json.loads(a.spec) if a.spec else None, tier=a.tier)
    elif a.cmd == "build":
        build(a.name, out=a.out, capital=a.capital, deploy=a.deploy, dev=a.dev)
    elif a.cmd == "watch":
        watch(a.name, out=a.out, capital=a.capital, deploy=a.deploy)
    else:
        FIXTURES.mkdir(parents=True, exist_ok=True)
        for f in sorted(FIXTURES.glob("*.json")):
            d = json.loads(f.read_text())
            print(f'{d["name"]:12} {d["captured_at"]}  {len(d["chart_rows"]):>5} trades  '
                  f'{f.stat().st_size / 1024:>6.0f} KB  {json.dumps(d["spec"])[:80]}')


if __name__ == "__main__":
    main()
