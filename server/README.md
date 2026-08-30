# `server` — the MCP service

```
python3 -m pytest server/tests -q      # 25 tests
STRATIFY_KEY_PEPPER=... ./server/run.sh
```

| Route | Auth | Purpose |
|---|---|---|
| `POST /mcp` | Bearer | JSON-RPC 2.0 — `initialize`, `tools/list`, `tools/call`, `ping` |
| `POST /v1/signup` | — | Self-serve account and first key |
| `POST /v1/keys` | — | Another key for an existing account |
| `GET /v1/coverage` | — | Public metadata: what exists, and what is withheld |
| `GET /r/{token}` | signed token | Rendered report |
| `GET /healthz` | — | Liveness |

**Not present, deliberately:** any route that returns market data. Decision D3 — zero rows
out. A test asserts no such route exists, and another asserts a result carries no
per-contract prices.

## Tools

`run_backtest` · `describe_coverage` · `explain_methodology` · `get_backtest` ·
`search` · `fetch`

`search` and `fetch` are single-string aliases, which is the shape ChatGPT's deep research
requires. They cost almost nothing and open a channel we would otherwise be excluded from.

`explain_methodology` is the knowledge base: seven topics covering entry pricing,
settlement, costs, margin, slippage, liquidity, validation and overfitting — including the
practical rules (decide the exit rule before looking at results; a lone parameter peak is
noise; an edge that dies when you double slippage was a cost artefact).

## Why the protocol is hand-written

v1 authenticates with a bearer key and enforces quotas on three dimensions, and those wrap
every call. Owning ~150 lines of JSON-RPC dispatch keeps auth, quotas and refusals in one
readable place and removes a dependency from the deploy. OAuth plugs in here in phase 2.

## Refusals are results, not errors

A bad spec comes back as a tool result with `isError: true` and a sentence saying what to
change. The model reads it and corrects the call. Returning a protocol error instead makes
the client think the connection is broken.

Protocol errors are reserved for what really is broken: unauthenticated (`-32001`), quota
exceeded (`-32002`, carrying `limit` and `retry_after_seconds`), unknown method, bad JSON.

## Security posture

- **Keys are never stored.** Only a peppered scrypt hash; the pepper lives in the
  environment, so a stolen database file yields no working keys. Plaintext is shown once.
  A test greps the whole `api_keys` table for the key to prove it.
- **Comparison is constant-time** and checks every active hash, so timing does not reveal
  whether a key exists.
- **Results are scoped to the issuing key** — `get_backtest` on someone else's id refuses.
- **Three independent quota dimensions**, because any one alone is gameable: requests/hour
  stops a naive loop, CPU-seconds/hour stops a few very expensive queries, concurrency
  stops a burst monopolising the box. Every rejection says which limit and when it clears.
- **No stack traces leave the process.** Unhandled exceptions log locally and return a bare
  internal error.

## Known limits

- `quota.Concurrency` is in-process. Correct at one worker; a multi-worker deploy must move
  it to Redis first. `run.sh` pins `--workers 1` and says why.
- No OAuth, so ChatGPT web/desktop and Gemini cannot connect (they offer OAuth-or-none).
  Everything that accepts a bearer header works today.
- `STRATIFY_KEY_PEPPER` has a development default. `run.sh` refuses to start without a real
  one set.
