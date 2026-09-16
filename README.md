<p align="center">
  <a href="https://stratify.aeon-labs.site"><img src="https://stratify.aeon-labs.site/static/icon-192.png" width="96" alt="Stratify"></a>
</p>

<h1 align="center">Stratify</h1>

<p align="center">
  <strong>Describe an options strategy in plain words. Get it backtested on real 1-minute NIFTY data — with an honest answer about how much to trust the result.</strong>
</p>

<p align="center">
  <a href="https://stratify.aeon-labs.site">Website</a> ·
  <a href="https://stratify.aeon-labs.site/docs">Docs</a> ·
  <a href="https://stratify.aeon-labs.site/explore">What it can do</a> ·
  <a href="https://registry.modelcontextprotocol.io/v0.1/servers?search=site.aeon-labs/stratify">MCP Registry</a> ·
  <a href="https://stratify.aeon-labs.site/contact">Contact</a>
</p>

<p align="center">
  <a href="https://www.npmjs.com/package/stratify-mcp"><img src="https://img.shields.io/npm/v/stratify-mcp?label=npm&color=8DDD8D" alt="npm"></a>
  <a href="https://pypi.org/project/stratify-mcp/"><img src="https://img.shields.io/pypi/v/stratify-mcp?label=PyPI&color=8DDD8D" alt="PyPI"></a>
  <a href="https://github.com/Srinath-exe/stratify-mcp/actions/workflows/ci.yml"><img src="https://github.com/Srinath-exe/stratify-mcp/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-8DDD8D" alt="MIT"></a>
  <img src="https://img.shields.io/badge/MCP-streamable--http%20%2B%20OAuth%202.1-6066EE" alt="MCP">
</p>

---

Stratify is an [MCP](https://modelcontextprotocol.io) server. You connect it to the chat
assistant you already use — ChatGPT, Claude, Gemini, Claude Code, Codex, Gemini CLI,
OpenCode — and say things like:

> *Sell a 20-delta NIFTY strangle every Thursday at 09:30, stop out at 2× the credit, take
> profit at 60%. Did it hold up out of sample?*

The assistant turns that into a strategy, Stratify runs it against every trading day in the
window on real option prints, and you get back P&L after real charges, return on margin, a
shareable report page — and an **honesty panel** that says whether the result survived the
data it was not fitted on.

This repository is the server behind the hosted service at
**[stratify.aeon-labs.site](https://stratify.aeon-labs.site)**, published so you can read
exactly how every number you are shown was produced. The market data is not here and never
leaves the service: you send a strategy, you get results.

## Connect in one minute

**No key needed** — these clients sign you in with Google when you add the URL:

| Client | Where | Paste |
|---|---|---|
| **ChatGPT** | Settings → Plugins → *Create* (Developer mode on) | `https://stratify-mcp.aeon-labs.site/mcp` |
| **Claude** (claude.ai / Desktop) | Settings → Connectors → *Add custom connector* | `https://stratify-mcp.aeon-labs.site/mcp` |
| **Gemini** (web / mobile) | Settings → Connected apps → *custom app* | `https://stratify-mcp.aeon-labs.site/mcp` |

**With an API key** — sign in at [stratify.aeon-labs.site](https://stratify.aeon-labs.site),
make a key (`sk_live_…`), then:

```bash
# Claude Code
claude mcp add --transport http stratify https://stratify-mcp.aeon-labs.site/mcp \
  --header "Authorization: Bearer sk_live_..."
```

```toml
# Codex  (~/.codex/config.toml)
[mcp_servers.stratify]
url = "https://stratify-mcp.aeon-labs.site/mcp"
http_headers = { Authorization = "Bearer sk_live_..." }
```

```json
// Gemini CLI  (~/.gemini/settings.json)      — or: gemini extensions install https://github.com/Srinath-exe/stratify-gemini-extension
{ "mcpServers": { "stratify": {
    "httpUrl": "https://stratify-mcp.aeon-labs.site/mcp",
    "headers": { "Authorization": "Bearer sk_live_..." }, "timeout": 120000 } } }
```

```json
// OpenCode  (opencode.json)
{ "mcp": { "stratify": { "type": "remote", "url": "https://stratify-mcp.aeon-labs.site/mcp",
    "enabled": true, "headers": { "Authorization": "Bearer {env:STRATIFY_API_KEY}" } } } }
```

```bash
# Any client that only speaks stdio
STRATIFY_API_KEY=sk_live_... npx stratify-mcp
```

```python
# Python, from a notebook
pip install stratify-mcp
from stratify_mcp import StratifyClient
result = StratifyClient(api_key="sk_live_...").run_backtest({...})   # returns pandas DataFrames
```

**Claude Desktop extension:** download `stratify-<version>.mcpb` from the
[latest release](https://github.com/Srinath-exe/stratify-mcp/releases/latest), double-click,
paste your key once.

Free tier: NIFTY, one year of 1-minute data, 100 backtests an hour, 1,000 other calls an hour.

## What comes back

Every backtest returns, and every report page shows:

- **P&L after real costs** — brokerage per leg, STT, exchange and SEBI fees, GST, stamp
  duty — and modelled slippage that is *stated as an assumption*, not hidden.
- **Return on margin** from a SPAN calibration measured against a live broker.
- **The honesty panel:** an out-of-sample split (the final 30% is never seen while the
  strategy is being shaped), walk-forward folds, a bootstrap interval on the return, and a
  deflated Sharpe that penalises the number of variants you already tried.
- **No ratio below 30 trades.** A Sharpe on twelve trades is noise; the service says
  `insufficient_sample` instead of a number that looks like evidence.
- **Every trade**, with why it entered and why it exited.
- A **shareable report** — a self-contained page with the equity curve, the rules card in
  plain English, and a browser-side capital view.

The service never calls a strategy good. It shows you what happened and how fragile that is.

## The strategy protocol

A strategy is **legs + rules**, not a preset. Any number of legs on any expiry; strikes by
delta, premium, points or percent; conditions on the position, on the legs, or on the
index; actions that close, roll or open. This exact spec parses and runs:

```json
{
  "legs": [
    {"side": "sell", "type": "CE", "strike": {"delta_near": 0.20}},
    {"side": "sell", "type": "PE", "strike": {"delta_near": 0.20}}
  ],
  "entry": {
    "cadence": "weekly", "dte": 3, "time": "09:30",
    "when": {"all": [{"vix_prev_close": {"gte": 13}}, {"rsi_14": {"lt": 70}}]}
  },
  "rules": [
    {"when": {"pnl_pct_of_credit": {"gte": 0.6}}, "then": "close"},
    {"when": {"leg_mark_mult": {"gte": 2.0, "leg": 0}},
     "then": {"roll": {"legs": [0], "to": {"delta_near": 0.20}}}, "max_times": 1}
  ],
  "portfolio": {"stop_after_losses": 3, "resume_after_days": 30}
}
```

| Vocabulary | |
|---|---|
| Strike selection | `delta_near`, `premium_near`, `pct_offset`, `points_offset`, `atm`, `strike`, `from_leg` |
| Entry gates (market) | `vix`, `vix_prev_close`, `vix_change_pct`, `prev_day_move_pct`, `gap_pct`, `realised_vol_20d`, `day_of_week` |
| Entry gates (index indicators) | `rsi_N`, `close_vs_sma_N_pct`, `close_vs_ema_N_pct`, `ema_F_vs_S_pct`, `sma_F_vs_S_pct` — N from 2 to 250, all on the **previous** close |
| Position conditions | `pnl_pct_of_credit`, `leg_mark_mult`, `combined_premium`, days/minutes to expiry, clock time |
| Actions | `close`, `close_legs`, `open`, `roll`, `close_and_open` |
| Portfolio | `stop_after_losses`, `stop_after_drawdown_pct`, `skip_after_loss`, `max_trades`, `stop_after_profit_pct`, `resume_after_days` |

Every gate reads data from *before* the entry moment — the SQL window frames and the
indicator series are built so that lookahead is structurally impossible, not merely
intended. `engine/tests/test_indicators.py` changes a day's close and asserts that day's RSI
does not move.

You never write this JSON by hand: the assistant does, from your sentence. The
`explain_methodology` and `describe_coverage` tools teach it the vocabulary and the limits.

## Tools

| Tool | Does | Writes? |
|---|---|---|
| `run_backtest` | Strategy in; trades, metrics, honesty panel, report URL out | stores the result under your account |
| `build_report` | Turns a stored backtest into a finished, shareable report page | creates a page |
| `describe_coverage` | Symbols, date window, resolution, structures, gates this deployment accepts | no |
| `explain_methodology` | Entry pricing, settlement, costs, margin, slippage, liquidity, overfitting, changelog | no |
| `get_backtest` | Retrieve a stored result by id | no |
| `list_strategies` | Your kept shortlist, ranked by worst walk-forward fold | no |
| `search` · `fetch` | Retrieval in the single-string form ChatGPT deep research requires | no |
| `submit_feedback` · `my_feedback` | Report a wrong answer with repro context attached automatically; see its status | files a report |

All tools carry `title`, `readOnlyHint`, `destructiveHint` and `openWorldHint`. Nothing is
destructive and nothing reaches outside the service — no broker, no third-party API, no
order placement of any kind.

## For agents and crawlers

```
name            site.aeon-labs/stratify            (official MCP Registry)
endpoint        https://stratify-mcp.aeon-labs.site/mcp
transport       streamable-http (JSON-RPC 2.0 over POST)
auth            OAuth 2.1 (PKCE S256, dynamic client registration, CIMD)
                or  Authorization: Bearer sk_live_...
discovery       https://stratify-mcp.aeon-labs.site/.well-known/oauth-protected-resource/mcp
issuer          https://stratify.aeon-labs.site
open without auth   initialize, tools/list, describe_coverage, explain_methodology, search, fetch
needs auth          run_backtest, build_report, get_backtest, list_strategies, submit_feedback, my_feedback
unauthenticated tools/call → HTTP 401 + WWW-Authenticate (never a 200 wrapping an error)
stdio bridge    npx stratify-mcp        python client   pip install stratify-mcp
market          NIFTY index options, NSE (India); 1-minute; free tier = one year
limits (free)   100 backtests/hour, 1,000 other calls/hour, per account
data egress     none — results only, never rows
```

## Why the code is open and the data is not

A backtest is a claim about the past, and the only thing separating an honest one from a
flattering one is methodology you cannot see from the outside. Every service in this space
asks you to take its fills, its costs and its margin on faith. So the methodology is here —
the parts that most often hide a lie:

- **[`engine/config/`](engine/config/)** — brokerage, STT, stamp duty, slippage, margin.
  Every constant is either measured against a live broker and says so, or an explicit
  assumption with a stated basis and says that too. There are no bare numbers.
- **[`engine/marks.py`](engine/marks.py)** — how a contract is priced at a point in time,
  including the window frames that make lookahead structurally impossible.
- **[`engine/honesty.py`](engine/honesty.py)** — the floors that force a result to admit a
  thin sample instead of reporting a confident number over eleven trades.
- **[`engine/strategy.py`](engine/strategy.py) · [`engine/simulate.py`](engine/simulate.py)**
  — the open strategy protocol: a closed vocabulary, no `eval` anywhere.
- **[`engine/methodology.py`](engine/methodology.py)** — every result is stamped with the
  methodology version that produced it, so a number you recorded last month is
  attributable when the model improves.

The data is a licensed, cleaned, multi-year tick archive. It is the one thing this
repository does not contain and cannot fetch; there is no route that returns rows, and a
test asserts there never will be.

## Layout

```
engine/      Spec in, trades and evidence out. Pure Python, ClickHouse for prices.
server/      MCP service, OAuth 2.1 authorization server, website, dashboard, reports, quotas.
data/        ClickHouse schema and per-tier settings profiles (schema only, no data).
packages/    npm client + stdio bridge, Python client, Claude Desktop bundle, Gemini CLI
             extension, MCP Registry entry. Released from tags; see packages/stratify-npm/PUBLISHING.md.
tests/       Adversarial suites: 100-strategy uniqueness sweep, exotic specs, market gates, persona evals.
docs/        Design record, frozen at the dates in each header.
vendor/      Runtime inputs the engine loads but does not ship. Read vendor/README.md.
```

## Running it yourself

You need a ClickHouse instance holding options data in the schema under `data/schema/`.
This repository contains no data and cannot fetch any; with no database, the suite skips
the tests that need one and says so.

```bash
pip install -r requirements-dev.txt
python3 -m pytest engine/tests server/tests -q -rs   # 667 tests; 212 skip without the database
cp .env.example .env                                  # then fill it in
./server/run.sh
```

`run.sh` refuses to start without `STRATIFY_KEY_PEPPER`, deliberately: the default is a
development value, and starting with it would hash every API key against a constant printed
in this repository. [`docker-compose.example.yml`](docker-compose.example.yml) is the
container deployment; [`SECURITY.md`](SECURITY.md) describes the trust boundaries it depends
on and how to report a vulnerability privately.

## Tests worth reading

`tests/torture_100.py` builds 100 strategies that are pairwise distinct on four axes —
entry, position, management, exit — and refuses to run if any two collide. It found bugs
unit tests did not: two chart resolutions that had never once executed, a guard that
discarded legitimate ratio spreads, and margin that ignored quantity.

`tests/persona_eval.py` drives a real model through the live MCP as several personas. It
caught a guardrail failure no unit test could have.

`server/tests/test_mcpauth.py` names, per test, the OAuth attack it prevents — code replay,
refresh-token reuse, redirect-URI substitution, PKCE downgrade — rather than asserting that
the happy path works.

## Contributing

The most useful contribution is checking the arithmetic — a wrong number that looks
plausible is the failure that matters here. [CONTRIBUTING.md](CONTRIBUTING.md) covers
setup, house style, and the one rule that is not optional: if a change makes an unchanged
spec return different numbers, the methodology version gets bumped.

## Licence

MIT — see [LICENSE](LICENSE). `server/vendor/lightweight-charts.js` is TradingView
Lightweight Charts™, Apache 2.0, redistributed with its licence header intact.

Backtests are historical simulation, not advice. Nothing here is a recommendation to trade.
Built and run by [Srinath H](https://github.com/Srinath-exe).
