# Stratify MCP — High-Level Design

**Status:** Draft v1 · **Date:** 2026-08-19 · Companion to the product requirements (internal)

---

## 1. Guiding principles

1. **Compute-to-data.** Rows never cross the network boundary. Specs go in, results come out.
2. **The registry is the source of truth for coverage.** No component may hardcode which
   layer holds what.
3. **Raw is always a valid fallback.** Every routing decision has a correct escape.
4. **Fail closed.** Ambiguity routes to the slower, more complete layer — never the faster one.
5. **Degrade by queueing.** Under overload we add latency, never errors or wrong answers.
6. **Single-pass SQL only.** Self-joins are banned in generated queries (measured 2.4× cost
   penalty and it scales backwards with dataset size).

## 2. System context

```
┌──────────────────────────────────────────────────────────┐
│  User's own AI  (Claude Code / Desktop, ChatGPT, Gemini,  │
│                  OpenCode, custom agents)                 │
│  — user pays for this inference, we never do              │
└───────────────┬──────────────────────────────────────────┘
                │  MCP (Streamable HTTP + Bearer)  or  REST
                ▼
┌──────────────────────────────────────────────────────────┐
│                    EDGE  (nginx / Caddy)                  │
│         TLS · IP rate-limit · request size cap            │
└───────────────┬──────────────────────────────────────────┘
                ▼
┌──────────────────────────────────────────────────────────┐
│  mcp-gateway          │  api-core          │  accounts    │
│  MCP protocol,        │  REST, spec        │  keys,       │
│  tool defs, server    │  validation,       │  quotas,     │
│  instructions         │  orchestration     │  billing     │
└───────────────┬───────┴──────────┬─────────┴──────────────┘
                │                  │
                ▼                  ▼
┌───────────────────────┐  ┌────────────────────────────────┐
│  backtest-engine      │  │  report-renderer               │
│  router → compiler →  │  │  server-side HTML, charts,     │
│  SQL → honesty panel  │  │  signed URLs, branding         │
└───────────┬───────────┘  └────────────────────────────────┘
            │
            ▼
┌──────────────────────────────────────────────────────────┐
│   ClickHouse  ── PRIVATE NETWORK, NEVER INTERNET-FACING   │
│   contract_day · hot_1min · nifty_5m · nifty_options      │
│   + layer_registry                                        │
└──────────────────────────────────────────────────────────┘
            │
            ▼
      Redis  (result cache · admission counters · quota counters)
```

**Trust boundary:** everything below `api-core` is private. ClickHouse binds only to the
Docker network. There is no code path from an HTTP request to a raw-row response.

## 3. Components

| Service | Language | Responsibility | Open source |
|---|---|---|---|
| `edge` | nginx | TLS, IP rate limiting, body-size caps | config only |
| `mcp-gateway` | TypeScript | MCP protocol, tool schemas, server instructions | **Yes** |
| `api-core` | Python (FastAPI) | REST, spec validation, orchestration, auth check | **Yes** |
| `backtest-engine` | Python | Routing, SQL compilation, honesty panel | **Yes** |
| `report-renderer` | Python (Jinja2) | Server-side HTML reports, signed URLs | **Yes** |
| `accounts` | Python (FastAPI) | Keys, quotas, billing, dashboard API | **Yes** |
| `clickhouse` | — | Storage and computation | No (config only) |
| `redis` | — | Cache, counters, admission control | No |
| `dashboard` | Next.js | Sign-up, keys, coverage, usage, billing | **Yes** |

`mcp-gateway` is deliberately thin: it translates MCP calls into `api-core` calls and
formats responses. All logic lives in `api-core` so every surface behaves identically.

## 4. Request lifecycle — `run_backtest`

```
1. Edge          TLS, IP rate limit, ≤64 KB body
2. api-core      Resolve API key → account, plan, quota state   (Redis, ~1 ms)
3. api-core      Validate strategy spec against JSON Schema     (reject on any unknown field)
4. api-core      Compute spec hash → check result cache         (Redis; hit ⇒ jump to 10)
5. admission     Acquire a slot (semaphore). Full ⇒ queue.
                 Queue depth > threshold ⇒ 429 + Retry-After
6. router        Read layer_registry → cheapest layer that FULLY covers the spec
7. compiler      Emit ONE single-pass SQL statement for that layer
8. clickhouse    Execute under the plan's settings profile
9. engine        Compute honesty panel (OOS, costs, benchmark, bootstrap,
                 multiple-comparisons deflation, data warnings, health score)
10. renderer     Render HTML report → store → mint signed URL
11. api-core     Cache result, increment counters, release slot
12. response     { summary, honesty, warnings, report_url, layer_used, cost_cpu_ms }
```

Steps 6–8 are the only place data is touched, and they run entirely inside the private network.

## 5. Data architecture

### 5.1 Layer stack (all verified bit-exact against raw)

| Layer | Rows | Disk | CPU-s | Coverage |
|---|---|---|---|---|
| `contract_day` | 232,173 | 15.7 MB | 0.0054 | 9 fixed decision times, any DTE/strike |
| `hot_1min` | 25,394,143 | 294 MB | 0.0525 | Full 1-min, DTE ≤ 45, ATM ± 2000 |
| `nifty_5m_12mo` | 15,694,031 | 196 MB | 0.194 | 5-min, everything |
| `nifty_options` | 78,391,808 | 763 MB | 0.454 | Everything — **source of truth** |

Total 1.27 GB. All four return identical results for the same strategy.

### 5.2 Routing

The router is a filter over `layer_registry`, not a chain of `if` statements:

```sql
SELECT layer_name FROM layer_registry
WHERE need_dte     <= max_dte
  AND need_mny     <= max_abs_moneyness
  AND (empty(allowed_minutes) OR hasAll(allowed_minutes, required_minutes))
  AND (resolution = need_res OR (resolution='fixed-times' AND hasAll(allowed_minutes, required_minutes)))
ORDER BY priority LIMIT 1;
```

Raw carries the widest bounds, so it always survives and the query cannot return empty.

**Why this matters:** routing a spec to an under-covering layer is the *only* way this
architecture produces a wrong answer. Measured: an ATM±2500 spec served from `hot_1min`
(±2000) silently returned 7,472 contract-days instead of 8,139 — 8.2% missing, no error.
Registry-driven filtering makes that unrepresentable.

### 5.3 Correctness guarantees

- `verify_layers.sql` runs in CI after every rebuild; any non-PASS fails the build
- Currently: 25,394,143 bars and 232,173 contract-days verified, **0 mismatches**
- Every response reports `layer_used`, so any wrong answer is traceable to a routing decision
- Layers are rebuilt from raw only — never from each other

## 6. Concurrency and admission control

Throughput is flat past saturation while latency grows linearly; the box queues rather
than failing. Therefore **the guarantee comes from limits we set, not from speed.**

| Control | Where | Value |
|---|---|---|
| Global in-flight backtests | Redis semaphore | 32 (precomputed), 8 (raw path) |
| Per-API-key in-flight | Redis | 2 free / 8 paid |
| Queue depth before 429 | api-core | 64; respond with `Retry-After` |
| ClickHouse `max_threads` | settings profile | 2 |
| ClickHouse `max_execution_time` | settings profile | 30 s |
| Result cache TTL | Redis | 1 h (data is immutable) |

Per-key in-flight limits are what stop one runaway agent from consuming every slot.

## 7. Quotas — gate on cost, not on rows

Two measured mistakes to avoid permanently:

- `max_rows_to_read = 20 M` blocked a *legitimate* backtest (DTE and time-of-day filters
  are not index-prunable).
- `read_rows = 2 B/hour` locked a user out after **54 backtests** — scans run at
  184 M rows/sec, so rows read is a terrible proxy for cost.

**Gate on `queries`, `execution_time`, `max_result_rows`, and `max_result_bytes`.**
Keep `read_rows` only as a runaway catcher at ≥ 20 B/hour.

## 8. Authentication

- One key per account, prefix-identified (`sk_live_…`), stored as a hash
- Bearer token on every surface — MCP, REST, SDKs
- Key → `{account_id, plan, limits}` cached in Redis, 60 s TTL
- ClickHouse users are **per-plan, not per-customer** (`demo_user`, `paid_user`);
  per-key accounting happens in `api-core`, since ClickHouse quotas are per-DB-user
- The free-tier ClickHouse user has `GRANT SELECT` on the public database **only** — it
  physically cannot see the paid corpus

## 9. Deployment

Everything is Docker. Single `docker-compose.yml` for dev; the same images for production.

```
edge ─ mcp-gateway ─ api-core ─ backtest-engine ─┬─ clickhouse (internal only)
     └ dashboard   └ accounts └ report-renderer  └─ redis      (internal only)
```

- Only `edge` publishes ports. ClickHouse and Redis have **no** host port mapping.
- Stateless services scale horizontally behind the edge.
- ClickHouse replicas are cheap: the full stack is 1.27 GB, so a read replica is minutes to build.

## 10. Scaling path

1. **Vertical first** — move off the oversubscribed VM. 23.7% of CPU is currently lost to
   steal; recovering it is the cheapest available win.
2. **Cache** — popular strategies collapse to 1 ms / 0.001 CPU-s.
3. **Precompute more layers** — each new layer is one `INSERT…SELECT`, one registry row,
   one verification check.
4. **Replicate** — N read-only ClickHouse replicas behind the engine; the corpus is small
   enough that this is nearly free.
5. **Shard by symbol** — only when the paid tier's multi-symbol load demands it.

## 11. Open risks

| Risk | Mitigation |
|---|---|
| Router serves an under-covering layer | Registry-driven filter; `layer_used` in every response; CI verification |
| Strategy-spec ambiguity ("exit at 3pm") | Spec requires explicit times; layers must match spec semantics exactly |
| Runaway agent loops | Per-key in-flight cap + queue-depth 429 |
| MCP client incompatibility | Verify per client before advertising; ship stdio proxy |
| Data licensing | Compute-to-data posture; confirm GFDL/Breeze terms before public launch |
| Query logs contain user strategies | Explicit retention policy; self-host option for paid tier |
| ClickHouse system logs (~25 GB today) | TTL on `trace_log` / `text_log` before imaging production |
