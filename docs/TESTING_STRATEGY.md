# End-to-End Test Automation

**Status:** Design v1 · **Date:** 2026-08-21

Goal: every push runs the full stack — protocol conformance, correctness, statistics,
guardrails, client-compatibility limits, and load — with no human in the loop.

---

## 1. The pyramid

```
  L7  Load & soak            clickhouse-benchmark · k6          nightly
  L6  Client-compat limits   size · latency · tool count        every push
  L5  Protocol conformance   mcp-compliance · Inspector CLI     every push
  L4  Guardrails / abuse     readonly · quotas · cross-db       every push
  L3  Statistical gates      honesty-panel correctness          every push
  L2  Cross-layer equivalence same spec → identical result      every push
  L1  Golden SQL + fixtures  compiler snapshots, offline        every push  (fast)
  L0  Data verification      verify_layers.sql                  after any rebuild
```

L0–L1 must run offline with no credentials, so contributors can work without data access.

---

## 2. Tooling

| Layer | Tool | Why |
|---|---|---|
| Unit / in-memory | **FastMCP `Client`** (or TS `InMemoryTransport`) | Sub-second, no subprocess |
| Protocol conformance | **`YawLabs/mcp-compliance`** — 88 tests, 8 categories, A–F grade | Works against HTTP *and* stdio |
| Scripted smoke | **`npx @modelcontextprotocol/inspector --cli`** | Official; exit codes drive CI |
| Evals / agent behaviour | **`lastmile-ai/mcp-eval`** or **MCPJam** | pytest-style; MCPJam also tests OAuth flows and MCP Apps |
| DB correctness | `sql/verify_layers.sql` | Already green: 25,394,143 bars, 0 mismatches |
| Load | `clickhouse-benchmark`, `k6` | Measured ceilings already established |

MCPJam is worth adopting specifically because it covers **OAuth flow testing** — the part
of our stack that is hardest to verify by hand and gates ChatGPT support.

---

## 3. Layer by layer

### L0 · Data verification
`verify_layers.sql`, run after every layer rebuild. Any non-PASS fails the build.
Currently 4/4 PASS.

### L1 · Golden SQL + offline fixtures
- **Compiler snapshots.** For each canonical spec, assert the emitted SQL matches a
  committed golden file. Catches silent semantic drift.
- **No-self-join lint.** Reject any compiled SQL containing `JOIN` against a layer table
  (measured 2.4× cost penalty, scales backwards).
- **Recorded response fixtures.** Replay stored API responses (~1 KB aggregates, never
  market data) so contract tests run with no network and no key.

### L2 · Cross-layer equivalence — the most important test we have
For a fixture set of specs, run each against **every layer that covers it** and assert
byte-identical results.

Already proven manually: raw / `hot_1min` / `contract_day` all returned
`50 days, ₹231,664, 0.70 win rate`. Automate it, and it permanently protects the
optimisation that gives us 84×.

Also assert the router table:

| Spec | Expected layer |
|---|---|
| DTE≤2, ATM±50, 09:20→15:00 | `contract_day` |
| DTE≤2, ATM±500, 09:47 | `hot_1min` |
| DTE≤2, ATM±2500, 09:47 | `nifty_options` |
| DTE≤2, ATM±500, 5-min | `nifty_5m_12mo` |

**Property test:** fuzz random specs; assert the chosen layer's declared bounds always
cover the spec's requirements. This is the class of bug that silently drops 8.2% of data.

### L3 · Statistical gates — derived from defects observed in a shipping competitor
Each is a regression test, because each is a real bug found in the wild:

| Test | Assertion |
|---|---|
| Small-sample suppression | n_trades < threshold ⇒ `insufficient_sample`, **never** a ratio |
| Zero-trade fold | fold with 0 test trades ⇒ `inconclusive`, **never** `overfitted` |
| Sample size always present | every emitted statistic carries its `n_trades` |
| Ratio sanity bound | Sharpe > 5 ⇒ flagged, not printed as a finding |
| Multiple comparisons | 47 variants ⇒ deflated Sharpe < raw Sharpe |
| **Strict JSON** | response contains no `Infinity` / `NaN`; serialise to `null` + reason |
| Cost application | gross − costs == net, per leg, per trade |

The strict-JSON test is not hypothetical: `"profit_factor": Infinity` was observed in a
live competitor and breaks conforming parsers.

### L4 · Guardrails
Automate the four manual probes:
1. Legitimate backtest succeeds
2. `SELECT *` bulk dump → blocked
3. `INSERT` → `ACCESS_DENIED`
4. `SELECT count() FROM fno.options` → `ACCESS_DENIED`

Plus: quota exhaustion returns 429 with `Retry-After`; the free DB user has grants on the
public database and nothing else.

### L5 · Protocol conformance
```bash
npx @modelcontextprotocol/inspector --cli http://localhost:8080/mcp --method tools/list
npx @modelcontextprotocol/inspector --cli http://localhost:8080/mcp \
    --method tools/call --tool-name run_backtest --tool-arg spec=@fixtures/straddle.json
npx mcp-compliance test http://localhost:8080/mcp --min-grade A
```
Assert capability negotiation, JSON-RPC framing, and error shapes. Gate the build on grade A.

### L6 · Client-compatibility limits — cheap tests that prevent silent breakage
| Assertion | Threshold | Source |
|---|---|---|
| Any tool response | < 25,000 tokens | Claude Code cap |
| Any tool latency | **< 30 s** | ChatGPT Desktop timeout |
| Tool count | ≤ 8 | selection accuracy degrades past 30–50 |
| Each tool description | non-empty, states presentation rules | — |
| Server description | ≤ 100 chars | registry convention |
| `get_trades` | paginates; never exceeds the cap on a full year | — |
| Transport | Streamable HTTP reachable over TLS | 97% ecosystem norm |

These are the failures that only appear in a user's client, never in your own tests.

### L7 · Load and soak
Nightly, not per-push. Established baselines to regress against:

| Workload | Expected |
|---|---|
| `contract_day` backtest | ~384/s saturated; p95 < 600 ms at c=128 |
| raw backtest | ~5.2/s; degrades by queueing, never errors |
| 256 concurrent clients | **zero errors** |
| Peak query memory | < 20 MB |

Fail the build on >20% regression against the recorded baseline.

---

## 4. CI pipeline

```yaml
on: [push, pull_request]
jobs:
  fast:            # no credentials, no data — contributors can run this
    - schema validation, compiler golden tests, fixture replay
    - no-self-join lint, strict-JSON lint
    - tool count / description / server-description limits
  integration:     # needs ClickHouse service container
    - docker compose up
    - sql/verify_layers.sql            → all PASS
    - cross-layer equivalence          → identical results
    - router property test             → coverage always satisfied
    - guardrail probes                 → 1 pass, 3 blocked
    - statistical gate suite
  protocol:
    - mcp-compliance                   → grade A
    - inspector --cli tools/list, tools/call
    - client-compat limit assertions
  nightly:
    - load sweep vs recorded baselines
    - live smoke against staging with a real key
    - OAuth flow test (MCPJam) once OAuth ships
```

## 5. What "end-to-end" must include that is easy to forget

- **A real MCP client handshake**, not just HTTP calls to `api-core`. The gateway is where
  protocol bugs live.
- **The report URL actually renders** — fetch it and assert HTTP 200 plus expected content.
- **Async submit/poll path** — assert a >15 s job returns `status: running` and that polling
  eventually yields the result.
- **A cold-cache run**, so we never measure only the cached path.
- **Both auth modes** once OAuth exists — bearer and OAuth must return identical results.
