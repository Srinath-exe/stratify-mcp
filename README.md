# Stratify MCP

An MCP server that backtests Indian index-options strategies against real 1-minute data,
and tells you where the answer is weak.

The hosted service is at **[stratify.aeon-labs.site](https://stratify.aeon-labs.site)** —
sign in, generate a key, point your client at it. This repository is the server behind it,
published so you can read exactly how a number you are shown was produced.

```
Endpoint   https://stratify-mcp.aeon-labs.site/mcp   (Streamable HTTP, bearer token)
Clients    npm  stratify-mcp        pip  stratify-mcp
```

## Why this is open

A backtest is a claim about the past, and the only thing separating an honest one from a
flattering one is methodology you cannot see from the outside. Every service in this space
asks you to take its fills, its costs and its margin on faith.

So the methodology is here. The parts that most often hide a lie:

- **[`engine/config/`](engine/config/)** — brokerage, STT, stamp duty, slippage, margin.
  Every constant is either measured against a live broker and says so, or an explicit
  assumption with a stated basis and says that too. There are no bare numbers.
- **[`engine/marks.py`](engine/marks.py)** — how a contract is priced at a point in time,
  including the window frames that make lookahead structurally impossible rather than
  merely intended.
- **[`engine/honesty.py`](engine/honesty.py)** — the floors that force a result to admit a
  thin sample instead of reporting a confident number over eleven trades.
- **[`engine/simulate.py`](engine/simulate.py)** — the open strategy protocol: legs, rules,
  and a closed vocabulary. No `eval`, anywhere.

What is **not** here is the data. Market data never leaves the service — you send a
strategy, you get results. There is no route that returns rows, and a test asserts there
never will be.

## What it does

Ten tools over MCP:

| Tool | |
|---|---|
| `run_backtest` | A strategy spec in; trades, metrics and an honesty panel out |
| `build_report` | A hosted tearsheet for a completed backtest |
| `describe_coverage` | What data exists, and what is withheld from your tier |
| `explain_methodology` | Entry pricing, settlement, costs, margin, slippage, liquidity, overfitting |
| `list_strategies` | The kept shortlist, ranked by worst walk-forward fold |
| `get_backtest` · `search` · `fetch` | Retrieval, including the single-string aliases ChatGPT deep research requires |
| `submit_feedback` · `my_feedback` | Report a wrong answer, with repro context attached automatically |

A strategy is legs plus rules, not a preset:

```json
{
  "legs": [{"action": "SELL", "option_type": "CE", "strike": {"atm_offset": 2}},
           {"action": "SELL", "option_type": "PE", "strike": {"atm_offset": -2}}],
  "entry": {"time": "09:30", "dte": 4, "when": {"vix_prev_close": {"gt": 15}}},
  "exit":  {"stop_loss_pct": 40, "target_pct": 60, "time": "15:15"}
}
```

Entry can gate on India VIX, the previous day's move, realised 20-day volatility, gap
percent, day of week and monthly-expiry weeks — all sourced from a market-state table whose
SQL window frame ends the day *before* entry.

## Layout

```
engine/      Spec in, trades and evidence out.
server/      MCP service, dashboard, Google sign-in, quotas, reports.
data/        ClickHouse schema and per-tier settings profiles.
packages/    Published clients, released FROM this repo — npm and PyPI both
             point back here. stratify-npm is TS, stratify-py is Python.
tests/       Adversarial suites: 100-strategy uniqueness sweep, exotic specs, market gates.
docs/        Internal design record, frozen at the dates in each header.
vendor/      Runtime inputs the engine loads but does not ship. Read vendor/README.md.
```

## Contributing

The most useful contribution is checking the arithmetic — a wrong number that looks
plausible is the failure that matters here. [CONTRIBUTING.md](CONTRIBUTING.md) covers
setup, house style, and the one rule that is not optional: if a change makes an unchanged
spec return different numbers, the methodology version gets bumped.

## Running it

You need a ClickHouse instance holding options data in the schema under `data/schema/`.
This repository contains no data and cannot fetch any.

```bash
pip install -r requirements.txt
python3 -m pytest engine/tests server/tests -q     # 527 tests
cp .env.example .env                                # then fill it in
./server/run.sh
```

`run.sh` refuses to start without `STRATIFY_KEY_PEPPER`, deliberately: the default is a
development value, and starting with it would hash every API key against a constant printed
in this repository.

See [`docker-compose.example.yml`](docker-compose.example.yml) for a container deployment
and [`SECURITY.md`](SECURITY.md) for the trust boundaries it depends on.

## Tests worth reading

`tests/torture_100.py` builds 100 strategies that are pairwise distinct on four axes —
entry, position, management, exit — and refuses to run if any two collide. It found bugs
unit tests did not: two chart resolutions that had never once executed, a guard that
discarded legitimate ratio spreads, and margin that ignored quantity, so a hundred short
calls blocked the same capital as one.

`tests/persona_eval.py` drives a real model through the live MCP as several personas. It
caught a guardrail failure no unit test could have.

## Licence

MIT — see [LICENSE](LICENSE). `server/vendor/lightweight-charts.js` is TradingView
Lightweight Charts™, Apache 2.0, redistributed with its licence header intact.

Backtests are historical simulation, not advice. Nothing here is a recommendation to trade.
