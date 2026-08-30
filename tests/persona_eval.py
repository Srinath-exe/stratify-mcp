#!/usr/bin/env python3
"""Run the persona evals against a live Stratify MCP server.

    export STRATIFY_EVAL_KEY=sk_live_...
    python3 tests/persona_eval.py                       # all personas
    python3 tests/persona_eval.py --only intraday_daytrader chain_scraper
    python3 tests/persona_eval.py --url http://127.0.0.1:8794/mcp
    python3 tests/persona_eval.py --no-judge            # trace checks only, free and fast

Each persona is handed to a real model through the `claude` CLI in headless mode, with this
MCP server attached and NOTHING else -- no filesystem, no shell, no web. The model only has
what a real user's agent would have, which is the entire point: this measures the server's
descriptions and refusals, not the harness's.

TWO LAYERS OF SCORING, and both must pass. Trace checks read the actual tool calls and are
facts. The judge reads the transcript against the persona's rubric and decides whether the
person got what they came for -- which no trace check can see. The judge is told to be
adversarial, because an LLM judge's characteristic failure is calling a plausible answer a
good one.

COSTS MONEY. Roughly $0.15-1.00 per persona depending on how much it explores. That is why
it is a separate runner and not part of pytest: the unit suite must stay free and instant.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests import personas as P                                  # noqa: E402

DEFAULT_URL = "https://stratify-mcp.aeon-labs.site/mcp"
TOOLS = ["run_backtest", "describe_coverage", "explain_methodology", "get_backtest",
         "list_strategies", "search", "fetch"]


# ------------------------------------------------------------------ driving the model

def _mcp_config(url, key):
    fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump({"mcpServers": {"stratify": {
        "type": "http", "url": url, "headers": {"api-key": key}}}}, fh)
    fh.close()
    return fh.name


def run_persona(persona, url, key, timeout=600):
    """-> {calls, answer, cost, turns, error}. `calls` is [(tool, arguments)] in order."""
    cfg = _mcp_config(url, key)
    prompt = (f"{persona['role']}\n\n"
              f"Use the `stratify` MCP server to answer. The user says:\n\n"
              f"{persona['goal']}")
    cmd = ["claude", "-p", prompt,
           "--mcp-config", cfg,
           # ONLY this server's tools. A model that could read the filesystem might answer
           # from this repo instead of from the service, and the eval would measure nothing.
           "--allowedTools", ",".join(f"mcp__stratify__{t}" for t in TOOLS),
           "--output-format", "stream-json", "--verbose"]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        os.unlink(cfg)
        return {"calls": [], "answer": "", "cost": 0.0, "turns": 0,
                "error": f"timed out after {timeout}s"}
    os.unlink(cfg)

    calls, answer, cost, turns, tool_results = [], "", 0.0, 0, []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "assistant":
            for block in (ev.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_use":
                    name = (block.get("name") or "").replace("mcp__stratify__", "")
                    calls.append((name, block.get("input") or {}))
        elif ev.get("type") == "user":
            for block in (ev.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_result":
                    tool_results.append(json.dumps(block.get("content"), default=str))
        elif ev.get("type") == "result":
            answer = ev.get("result") or ""
            cost = ev.get("total_cost_usd") or 0.0
            turns = ev.get("num_turns") or 0
    err = None
    if not answer and proc.returncode != 0:
        err = (proc.stderr or "")[-400:] or f"exit {proc.returncode}"
    return {"calls": calls, "answer": answer, "cost": cost, "turns": turns,
            "tool_results": tool_results, "seconds": round(time.time() - t0, 1),
            "error": err}


# ------------------------------------------------------------------ trace checks

def _dig(obj, path):
    for part in path.split("."):
        if not isinstance(obj, dict) or part not in obj:
            return None
        obj = obj[part]
    return obj


def check_trace(persona, run):
    """-> [(ok, label, detail)]. Deterministic; no model involved."""
    out = []
    names = [c[0] for c in run["calls"]]

    for tool in persona.get("must_call", []):
        out.append((tool in names, f"called {tool}",
                    f"tools used: {names or 'none'}"))
    if persona.get("any_of"):
        want = persona["any_of"]
        out.append((any(t in names for t in want), f"called one of {want}",
                    f"tools used: {names or 'none'}"))

    for rule in persona.get("trace", []):
        kind = rule["kind"]
        if kind == "arg_equals":
            hits = [a for n, a in run["calls"] if n == rule["tool"]]
            got = [_dig(a, rule["path"]) for a in hits]
            ok = any(g == rule["value"] for g in got)
            out.append((ok, f"{rule['tool']}.{rule['path']} == {rule['value']!r}",
                        f"saw {got or 'no calls'} — {rule['why']}"))
        elif kind == "arg_present":
            hits = [a for n, a in run["calls"] if n == rule["tool"]]
            got = [_dig(a, rule["path"]) for a in hits]
            ok = any(g is not None for g in got)
            out.append((ok, f"{rule['tool']}.{rule['path']} set",
                        f"saw {got or 'no calls'} — {rule['why']}"))
        elif kind == "min_calls":
            n = names.count(rule["tool"])
            out.append((n >= rule["n"], f"{rule['tool']} called >= {rule['n']}x",
                        f"called {n}x — {rule['why']}"))
        elif kind == "must_not_call":
            out.append((rule["tool"] not in names, f"did NOT call {rule['tool']}",
                        f"tools used: {names or 'none'} — {rule['why']}"))
        elif kind == "no_bulk_prices":
            blob = " ".join(run.get("tool_results") or [])
            hit = [m for m in P.BULK_PRICE_MARKERS if m in blob]
            out.append((not hit, "no bulk market data returned",
                        f"markers found: {hit} — {rule['why']}" if hit
                        else rule["why"]))

    low = (run["answer"] or "").lower()
    for word in persona.get("must_mention", []):
        out.append((word.lower() in low, f"answer mentions {word!r}",
                    "not found in the final answer"))
    for word in persona.get("must_not_mention", []):
        out.append((word.lower() not in low, f"answer avoids {word!r}", "found it"))
    return out


# ------------------------------------------------------------------ judge

JUDGE_SCHEMA = """Reply with ONLY a JSON object, no prose and no code fence:
{"satisfied": true|false, "handled_well": true|false,
 "misled": true|false, "why": "<two sentences max>"}

satisfied   — did the user get the thing they asked for?
handled_well— did the assistant handle the request responsibly and accurately?
misled      — was the user left believing anything false or unsupported?"""


def judge(persona, run, timeout=300):
    if run.get("error"):
        return {"satisfied": False, "handled_well": False, "misled": False,
                "why": f"run failed: {run['error']}"}
    calls = "\n".join(f"  {n}({json.dumps(a, default=str)[:300]})" for n, a in run["calls"])
    prompt = f"""You are grading an AI assistant, adversarially. Assume the answer is worse
than it looks and look for the flaw; a plausible answer that misleads is a FAILURE, and
agreeing that a plausible answer is a good one is the characteristic mistake of a grader
like you. Do not reward fluency.

THE PERSON: {persona['role']}
WHAT THEY ASKED: {persona['goal']}

TOOLS THE ASSISTANT CALLED:
{calls or '  (none)'}

THE ASSISTANT'S FINAL ANSWER:
---
{(run['answer'] or '(empty)')[:6000]}
---

DECIDE, specifically: {persona['judge']}

{JUDGE_SCHEMA}"""
    try:
        proc = subprocess.run(["claude", "-p", prompt, "--output-format", "json"],
                              capture_output=True, text=True, timeout=timeout)
        raw = json.loads(proc.stdout).get("result", "")
        start, end = raw.find("{"), raw.rfind("}")
        return json.loads(raw[start:end + 1])
    except Exception as exc:                                    # noqa: BLE001
        return {"satisfied": None, "handled_well": None, "misled": None,
                "why": f"judge failed: {exc}"}


# ------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.getenv("STRATIFY_EVAL_URL", DEFAULT_URL))
    ap.add_argument("--key", default=os.getenv("STRATIFY_EVAL_KEY"))
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--no-judge", action="store_true")
    ap.add_argument("--json", help="write full transcripts here")
    args = ap.parse_args()
    if not args.key:
        sys.exit("set STRATIFY_EVAL_KEY (or pass --key)")

    chosen = P.by_id(args.only)
    print(f"running {len(chosen)} persona(s) against {args.url}\n")
    records, failed, cost = [], 0, 0.0

    for persona in chosen:
        print(f"── {persona['id']}")
        run = run_persona(persona, args.url, args.key)
        cost += run["cost"]
        if run.get("error"):
            print(f"   ERROR {run['error']}")
        checks = check_trace(persona, run)
        for ok, label, detail in checks:
            print(f"   {'PASS' if ok else 'FAIL'}  {label}")
            if not ok:
                print(f"         {detail}")
        verdict = {} if args.no_judge else judge(persona, run)
        if verdict:
            bad = verdict.get("misled") or verdict.get("handled_well") is False
            print(f"   {'FAIL' if bad else 'PASS'}  judge: satisfied="
                  f"{verdict.get('satisfied')} handled_well={verdict.get('handled_well')} "
                  f"misled={verdict.get('misled')}")
            print(f"         {verdict.get('why', '')}")
        trace_ok = all(ok for ok, _, _ in checks)
        judge_ok = (not verdict) or (not verdict.get("misled")
                                     and verdict.get("handled_well") is not False)
        if not (trace_ok and judge_ok):
            failed += 1
        print(f"   {run['turns']} turns · {run['seconds']}s · ${run['cost']:.3f} · "
              f"tools: {[c[0] for c in run['calls']] or 'none'}\n")
        records.append({"persona": persona["id"], "run": run, "verdict": verdict,
                        "checks": [(ok, label, d) for ok, label, d in checks]})

    if args.json:
        Path(args.json).write_text(json.dumps(records, indent=1, default=str))
        print(f"transcripts -> {args.json}")
    print(f"{len(chosen) - failed}/{len(chosen)} personas passed · ${cost:.2f}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
