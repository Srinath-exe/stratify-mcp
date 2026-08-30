# stratify (npm)

JS/TS client, and `npx stratify-mcp` stdio bridge, for [Stratify](https://stratify.aeon-labs.site)
— real 1-minute NIFTY options data, honest backtests.

```bash
npm install stratify-mcp
```

## Quickstart

```ts
import { StratifyClient } from "stratify-mcp";

// One-time: create an account and get a key. The key is shown once -- save it.
const signup = await StratifyClient.signup("you@example.com");
const client = new StratifyClient({ apiKey: signup.api_key });

const result = await client.runBacktest({
  legs: [
    { side: "sell", type: "CE", strike: { delta_near: 0.2 } },
    { side: "sell", type: "PE", strike: { delta_near: 0.2 } },
  ],
  // The reason for the trade, not just the trade.
  entry: { cadence: "weekly", dte: 3, time: "09:30", when: { vix: { gte: 15 } } },
  // Managed while it is open.
  rules: [
    { when: { pnl_pct_of_credit: { gte: 0.6 } }, then: "close" },
    { when: { leg_mark_mult: { gte: 2.0, leg: 0 } },
      then: { roll: { legs: [0], to: { delta_near: 0.2 } } }, max_times: 2 },
  ],
  portfolio: { stop_after_losses: 3, resume_after_days: 30 },
});

console.log(result.summary.total_pnl_rupees, result.summary.max_drawdown_rupees);
console.log(result.summary.ratios);  // sharpe, profit_factor, calmar (only above 30 trades)
console.log(result.honesty);        // out-of-sample split, walk-forward folds, deflated Sharpe
result.trades;                      // Array<Record<string, unknown>>, one entry per trade
result.equity_curve?.rows;          // Array<unknown[]>, columns named in .equity_curve.columns
console.log(result.report_url);     // shareable page with the full chart and every trade
```

Already have a key?

```ts
const client = new StratifyClient({ apiKey: "sk_live_..." });
```

Requires Node >=18 (uses the platform `fetch`; no HTTP dependency).

## Why a client at all, when it's just JSON-RPC

There's no separate REST endpoint for `run_backtest` — every tool is reached through one
`POST /mcp` speaking MCP JSON-RPC 2.0. This package exists so you don't hand-roll that
envelope: `client.runBacktest(...)` is a typed async function, and errors come back as
JS `Error` subclasses you can `instanceof`-check instead of a JSON-RPC error code you have
to remember.

## Errors

```ts
import { AuthenticationError, QuotaExceededError, ToolRefusalError } from "stratify-mcp";

try {
  const result = await client.runBacktest(spec);
} catch (err) {
  if (err instanceof AuthenticationError) { /* bad or revoked key */ }
  else if (err instanceof QuotaExceededError) { /* err.limit, err.retryAfterSeconds */ }
  else if (err instanceof ToolRefusalError) { /* err.message says why the server refused */ }
  else throw err;
}
```

## Methods

| Method | Returns |
|---|---|
| `runBacktest(spec, {lots, detail})` | `Promise<BacktestResult>` |
| `getBacktest(backtestId, detail?)` | `Promise<BacktestResult>` |
| `describeCoverage()` | `Promise<Record<string, unknown>>` |
| `explainMethodology(topic?)` | `Promise<Record<string, unknown>>` |
| `listStrategies({order, limit})` | `Promise<Record<string, unknown>>` — `{strategies: [...], bar: {...}, ...}` |
| `search(query)` / `fetchDocument(id)` | `Promise<Record<string, unknown>>` |
| `StratifyClient.signup(email)` (static) | `Promise<SignupResponse>` — includes `api_key`, shown once |

`detail`: `"summary"` (aggregates only), `"standard"` (default — equity curve,
breakdowns, first 25 trades), `"full"` (every stored trade).

`fetch` is named `fetchDocument` on the client so it doesn't collide with the global
`fetch` this library uses internally.

## `npx stratify-mcp` — for stdio-only clients

Most current MCP clients (claude.ai, Claude Desktop, Claude Code) speak Streamable HTTP
directly — point them at `https://stratify-mcp.aeon-labs.site/mcp` with your key as a bearer header
and skip this section entirely. This proxy exists only for a client that can *only* run a
local stdio server:

```json
{
  "mcpServers": {
    "stratify": {
      "command": "npx",
      "args": ["stratify-mcp-proxy"],
      "env": { "STRATIFY_API_KEY": "sk_live_..." }
    }
  }
}
```

It is a dumb pipe: one JSON-RPC message per line on stdin, forwarded verbatim to
`POST /mcp`, the response written back as one line on stdout. It does not add features or
change behavior versus calling the HTTP endpoint directly.

## Development

```bash
npm install
npm test     # builds, then runs against a mocked fetch -- no live server or key needed
```

## License

MIT.
