#!/usr/bin/env python3
"""Run the invariants across a wide grid of valid specs.

    python3 tests/sweep.py                 # ~180 specs, a few minutes
    python3 tests/sweep.py --quick         # ~30 specs, for a pre-push hook
    python3 tests/sweep.py --full          # every combination, for nightly
    python3 tests/sweep.py --seed 7 --random 400   # random specs, for soak

WHY A GRID AND NOT EXAMPLES. The bugs this exists for were reachable from DEFAULT
parameters and still survived 231 hand-written tests, because a spec only meets the thin
minute that triggers them at particular offsets on particular days. Breadth finds that;
care choosing examples does not.

WHY IT IS NOT ONLY pytest. The full grid takes minutes and hits ClickHouse, which is the
wrong shape for a suite that has to stay fast on every push. A subset runs in pytest
(test_sweep.py); the full thing is a job. The invariants themselves live in one module so
both paths check the same rules.

Exit code is the point: 0 clean, 1 if any invariant was violated, so a scheduler can gate.
"""
import argparse
import itertools
import json
import random
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import backtest, detail, metrics, spec as spec_mod   # noqa: E402
from tests import invariants                                     # noqa: E402

ENTRY_TIMES = ["09:30", "11:00", "13:00", "15:00"]

# Deliberately includes values at and beyond the edges of what is sensible -- 0.0 offset
# (at the money), 20.0 (the schema maximum, far past any listed strike), width 0.0 -- so
# the grid probes the boundaries the validator claims to allow rather than a comfortable
# middle. A spec that is accepted must produce a coherent result or refuse cleanly.
GRIDS = {
    "short_strangle": {"pct_offset": [0.0, 0.5, 1.5, 3.0, 8.0],
                       "sl_mult": [None, 1.0, 2.5]},
    "credit_spread": {"pct_offset": [0.0, 0.5, 1.5, 3.0],
                      "pct_width": [0.25, 1.0, 3.0],
                      "sl_mult": [None, 1.5], "tp_pct": [None, 0.5],
                      "direction": ["CE", "PE"]},
    "iron_condor": {"pct_offset": [0.0, 1.0, 2.5, 5.0],
                    "pct_width": [0.25, 1.0, 2.0]},
    "iron_fly": {"pct_width": [0.25, 1.0, 2.5, 5.0]},
    "long_option": {"pct_offset": [0.0, 0.5, 2.0, 5.0],
                    "sl_pct": [None, 0.5], "tp_pct": [None, 1.0],
                    "direction": ["CE", "PE"]},
}
DTES = [0, 1, 4, 7]
CADENCES = [("weekly", None), ("daily", "14:00"), ("daily", "EOD"), ("daily", None)]


def _param_sets(structure, full):
    grid = GRIDS[structure]
    keys = sorted(grid)
    combos = [dict(zip(keys, values)) for values in itertools.product(*(grid[k] for k in keys))]
    combos = [{k: v for k, v in c.items() if v is not None} for c in combos]
    if not full:
        # Every VALUE of every parameter still appears; the cross product does not.
        seen, trimmed = set(), []
        for c in combos:
            sig = tuple(sorted(c.items()))
            novel = any((k, v) not in seen for k, v in c.items())
            if novel or len(trimmed) < 2:
                trimmed.append(c)
                seen.update(c.items())
        combos = trimmed
    return combos


def build_specs(quick=False, full=False):
    specs = []
    for structure in sorted(GRIDS):
        for params in _param_sets(structure, full):
            for cadence, exit_time in (CADENCES[:2] if quick else CADENCES):
                for dte in ([4] if quick else DTES):
                    p = dict(params)
                    raw = {"structure": structure, "params": p, "entry_time": "11:00"}
                    if cadence == "daily":
                        raw["cadence"] = "daily"
                        if dte <= 1:
                            raw["max_dte"] = dte
                    else:
                        p["entry_dte"] = dte
                    if exit_time:
                        raw["exit_time"] = exit_time
                    specs.append(raw)
        if not quick:
            for t in ENTRY_TIMES:
                base = dict(_param_sets(structure, False)[0])
                base["entry_dte"] = 4
                specs.append({"structure": structure, "params": base, "entry_time": t})
    # Deduplicate: the trimming above can produce the same spec by two routes.
    uniq, out = set(), []
    for s in specs:
        k = json.dumps(s, sort_keys=True)
        if k not in uniq:
            uniq.add(k)
            out.append(s)
    return out


def random_specs(n, seed):
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        structure = rng.choice(sorted(GRIDS))
        params = {k: rng.choice(v) for k, v in GRIDS[structure].items()}
        params = {k: v for k, v in params.items() if v is not None}
        raw = {"structure": structure, "params": params,
               "entry_time": rng.choice(spec_mod.ENTRY_TIMES)}
        if rng.random() < 0.5:
            raw["cadence"] = "daily"
            if rng.random() < 0.4:
                raw["max_dte"] = rng.choice([0, 1, 2, 5])
        else:
            params["entry_dte"] = rng.choice(DTES)
        if rng.random() < 0.6:
            later = [t for t in spec_mod.EXIT_TIMES
                     if spec_mod.minute_of(t) > spec_mod.minute_of(raw["entry_time"])]
            if later:
                raw["exit_time"] = rng.choice(later)
        out.append(raw)
    return out


def run(specs, verbose=False):
    violations, refused, empty, ok = [], [], [], 0
    t0 = time.time()
    for i, raw in enumerate(specs, 1):
        label = json.dumps(raw, sort_keys=True)
        try:
            spec = spec_mod.parse(raw)
        except spec_mod.SpecError as exc:
            # A spec the validator refuses is a PASS: refusing is a documented outcome.
            refused.append((label, str(exc)))
            continue
        try:
            result = backtest.run(spec)
        except spec_mod.SpecError as exc:
            refused.append((label, str(exc)))
            continue
        except Exception as exc:                      # noqa: BLE001
            violations.append((label, f"CRASH {type(exc).__name__}: {exc}",
                               traceback.format_exc()))
            continue
        if not result.trades:
            empty.append(label)
            continue
        summary = metrics.summarise(result)
        rich = detail.build(result)
        try:
            invariants.check_all(spec, result, summary, rich)
            ok += 1
        except invariants.Violation as exc:
            violations.append((label, str(exc), None))
        if verbose and i % 25 == 0:
            print(f"  ... {i}/{len(specs)}  {time.time() - t0:.0f}s", flush=True)
    return {"ok": ok, "violations": violations, "refused": refused, "empty": empty,
            "seconds": round(time.time() - t0, 1), "n": len(specs)}


def report(res):
    print(f"\n  specs        {res['n']}")
    print(f"  clean        {res['ok']}")
    print(f"  refused      {len(res['refused'])}   (a refusal is a pass)")
    print(f"  no trades    {len(res['empty'])}")
    print(f"  VIOLATIONS   {len(res['violations'])}")
    print(f"  wall         {res['seconds']}s")
    if res["refused"]:
        kinds = {}
        for _, why in res["refused"]:
            kinds[why.split(";")[0][:70]] = kinds.get(why.split(";")[0][:70], 0) + 1
        print("\n  refusal reasons:")
        for why, n in sorted(kinds.items(), key=lambda kv: -kv[1])[:8]:
            print(f"    {n:>4}  {why}")
    if res["violations"]:
        print("\n  ---- VIOLATIONS ----")
        grouped = {}
        for label, msg, tb in res["violations"]:
            grouped.setdefault(msg.split(":")[0], []).append((label, msg, tb))
        for rule, items in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
            print(f"\n  [{len(items)}] {rule}")
            for label, msg, tb in items[:3]:
                print(f"      spec {label}")
                print(f"      {msg}")
                if tb:
                    print("      " + tb.strip().splitlines()[-1])
    return 1 if res["violations"] else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--random", type=int, default=0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    specs = (random_specs(args.random, args.seed) if args.random
             else build_specs(quick=args.quick, full=args.full))
    print(f"sweeping {len(specs)} specs", flush=True)
    sys.exit(report(run(specs, verbose=args.verbose)))


if __name__ == "__main__":
    main()
