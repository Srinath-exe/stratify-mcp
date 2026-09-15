# stratify-mcp

Python client for [Stratify](https://stratify.aeon-labs.site) — real 1-minute NIFTY options data,
honest backtests (out-of-sample, walk-forward, deflated Sharpe, all reported alongside the
number, not instead of it).

```bash
pip install stratify-mcp
```

## Quickstart

```python
from stratify_mcp import StratifyClient

# One-time: create an account and get a key. The key is shown once -- save it.
signup = StratifyClient.signup("you@example.com")
client = StratifyClient(api_key=signup["api_key"])

result = client.run_backtest({
    "legs": [
        {"side": "sell", "type": "CE", "strike": {"delta_near": 0.2}},
        {"side": "sell", "type": "PE", "strike": {"delta_near": 0.2}},
    ],
    # The reason for the trade, not just the trade.
    "entry": {"cadence": "weekly", "dte": 3, "time": "09:30",
              "when": {"vix": {"gte": 15}}},
    # Managed while it is open: take profit, and roll the tested side if it doubles.
    "rules": [
        {"when": {"pnl_pct_of_credit": {"gte": 0.6}}, "then": "close"},
        {"when": {"leg_mark_mult": {"gte": 2.0, "leg": 0}},
         "then": {"roll": {"legs": [0], "to": {"delta_near": 0.2}}}, "max_times": 2},
    ],
    "portfolio": {"stop_after_losses": 3, "resume_after_days": 30},
})

print(result.summary["total_pnl_rupees"], result.summary["max_drawdown_rupees"])
print(result.summary["ratios"])       # sharpe, profit_factor, calmar (only above 30 trades)
print(result.honesty)          # out-of-sample split, walk-forward folds, deflated Sharpe
result.trades                  # pandas.DataFrame, one row per trade
result.equity_curve            # pandas.DataFrame
print(result.report_url)       # shareable page with the full chart and every trade
```

Already have a key? Skip `signup()`:

```python
client = StratifyClient(api_key="sk_live_...")
```

## Why a Python client at all, when it's just JSON-RPC

There's no separate REST endpoint for `run_backtest` — every tool is reached through one
`POST /mcp` speaking MCP JSON-RPC 2.0. This package exists so you don't hand-roll that
envelope: `client.run_backtest(...)` is a real function call, errors come back as Python
exceptions you can `except`, and results come back as `pandas.DataFrame`s instead of raw
JSON, because that's what you're actually going to do with a table of trades.

## Errors

```python
from stratify_mcp import AuthenticationError, QuotaExceededError, ToolRefusalError

try:
    result = client.run_backtest(spec)
except AuthenticationError:
    ...  # bad or revoked key
except QuotaExceededError as e:
    ...  # e.limit, e.retry_after_seconds
except ToolRefusalError as e:
    ...  # the server read your spec and refused it -- str(e) says why
```

## Methods

| Method | Returns |
|---|---|
| `run_backtest(spec, lots=1, detail="standard")` | `BacktestResult` |
| `get_backtest(backtest_id, detail=None)` | `BacktestResult` |
| `describe_coverage()` | `dict` — symbols, date range, structures, gates, biases, cost model |
| `explain_methodology(topic=None)` | `dict` |
| `list_strategies(order="consistency", limit=None)` | `dict` — `{"strategies": [...], "bar": {...}, ...}`, this account's strategies that held up out-of-sample |
| `search(query)` / `fetch(id)` | `dict` |
| `StratifyClient.signup(email)` (staticmethod) | `dict` — includes `api_key`, shown once |

`detail` on `run_backtest`/`get_backtest`: `"summary"` (aggregates only, cheapest),
`"standard"` (default — equity curve, breakdowns, first 25 trades), `"full"` (every
stored trade).

## `BacktestResult`

| Property | Type |
|---|---|
| `.summary` | `dict` |
| `.honesty` | `dict` |
| `.interpretation` | `str` |
| `.trades` | `pandas.DataFrame` |
| `.equity_curve` | `pandas.DataFrame` |
| `.qualified` / `.why_not_qualified` | `bool` / `str \| None` |
| `.backtest_id` / `.report_url` | `str` |
| `.warnings` | `list[str]` |
| `.to_dict()` | the complete raw payload |

## Development

```bash
pip install -e ".[dev]"
pytest
```

Tests run against a mocked transport and need no live server or API key.

## License

MIT.

<!-- mcp-name: site.aeon-labs/stratify -->
