# Stratify MCP — Low-Level Design

**Status:** Draft v1 · **Date:** 2026-08-19 · Companion to `HIGH_LEVEL_DESIGN.md`

---

## 1. Repository layout

```
stratify_mcp/
├── context/                    # these three documents
├── docker-compose.yml          # dev; production overlays in deploy/
├── .env.example
├── services/
│   ├── mcp-gateway/            # TypeScript — MCP protocol only
│   │   ├── src/{server.ts,tools.ts,instructions.ts,transport.ts}
│   │   └── Dockerfile
│   ├── api-core/               # Python FastAPI — REST + orchestration
│   │   ├── app/{main.py,auth.py,admission.py,cache.py,schemas.py,routes/}
│   │   └── Dockerfile
│   ├── backtest-engine/        # Python — router, compiler, honesty panel
│   │   ├── engine/{router.py,compiler.py,honesty.py,costs.py,registry.py}
│   │   └── Dockerfile
│   ├── report-renderer/        # Python Jinja2 — server-side HTML
│   │   ├── renderer/{render.py,charts.py,templates/}
│   │   └── Dockerfile
│   └── accounts/               # Python FastAPI — keys, quotas, billing
├── packages/
│   ├── stratify-py/            # pip install stratify
│   └── stratify-npm/           # npm i stratify  (+ stdio→HTTP proxy binary)
├── sql/
│   ├── 001_layers.sql          # layer DDL
│   ├── 002_registry.sql        # layer_registry + seed rows
│   ├── 003_users.sql           # settings profiles, users, quotas
│   └── verify_layers.sql       # CI correctness harness
└── tests/
```

**Open-source boundary:** this entire repo is public. The ingestion pipeline and all data
live in the existing private `clickhouse_db/` tree and are never referenced here except by
table name.

## 2. Database objects

### 2.1 Layers (already built and verified)

```sql
-- L1 · fixed decision times, 232,173 rows, 15.7 MB
CREATE TABLE public_demo.contract_day (
    trade_date Date, expiry_date Date, strike_price UInt32,
    option_type Enum8('CE'=1,'PE'=2), dte UInt16,
    day_open Float32, day_high Float32, day_low Float32, day_close Float32,
    volume UInt64, oi_close UInt32,
    spot_open Float32, spot_close Float32, moneyness Float32,
    px_0920 Float32, px_0930 Float32, px_1000 Float32, px_1100 Float32,
    px_1200 Float32, px_1300 Float32, px_1400 Float32, px_1500 Float32, px_1515 Float32
) ENGINE = MergeTree
ORDER BY (dte, moneyness, trade_date, expiry_date, strike_price, option_type);

-- L2 · full 1-minute for tradeable contracts, 25,394,143 rows, 294 MB
CREATE TABLE public_demo.hot_1min (
    dte UInt16, trade_date Date, expiry_date Date, strike_price UInt32,
    option_type Enum8('CE'=1,'PE'=2), timestamp DateTime('Asia/Kolkata'),
    minute_of_day UInt16, moneyness Int32,
    open Float32, high Float32, low Float32, close Float32,
    volume UInt32, open_interest UInt32, underlying_value Float32
) ENGINE = MergeTree
ORDER BY (dte, trade_date, expiry_date, strike_price, option_type, timestamp);
```

`minute_of_day = hour*60 + minute`. 09:20 = 560, 15:00 = 900, 15:15 = 915.

### 2.2 Layer registry

```sql
CREATE TABLE public_demo.layer_registry (
    layer_name String,
    priority UInt8,                 -- lower = cheaper = try first
    resolution String,              -- 'fixed-times' | '1min' | '5min'
    max_dte UInt16,
    max_abs_moneyness UInt32,
    allowed_minutes Array(UInt16),  -- empty = every minute available
    n_rows UInt64, disk_mb Float32, typical_cpu_sec Float32
) ENGINE = TinyLog;

INSERT INTO public_demo.layer_registry VALUES
 ('contract_day',  1,'fixed-times',65535,4294967295,[560,570,600,660,720,780,840,900,915],232173,15.74,0.0054),
 ('hot_1min',      2,'1min',          45,      2000,[],                        25394143,294.04,0.0525),
 ('nifty_5m_12mo', 3,'5min',       65535,4294967295,[],                        15694031,196.21,0.1940),
 ('nifty_options', 4,'1min',       65535,4294967295,[],                        78391808,763.43,0.4544);
```

**Invariant:** the highest-priority row must have maximal bounds and empty `allowed_minutes`,
so the router can never return zero rows.

### 2.3 Access control

```sql
CREATE SETTINGS PROFILE mcp_free SETTINGS
  max_execution_time = 30 READONLY, max_result_rows = 100000 READONLY,
  max_result_bytes = 50000000 READONLY, result_overflow_mode = 'throw' READONLY,
  max_memory_usage = 2000000000 READONLY, max_threads = 2 READONLY,
  max_rows_to_read = 200000000 READONLY, readonly = 1;

CREATE USER mcp_free_user IDENTIFIED BY '…' SETTINGS PROFILE mcp_free;
GRANT SELECT ON public_demo.* TO mcp_free_user;   -- nothing else, ever

CREATE QUOTA mcp_free_quota FOR INTERVAL 1 hour
  MAX queries = 2000, execution_time = 900, read_rows = 20000000000
  TO mcp_free_user;
```

Note `read_rows = 20 B`, not 2 B — the tighter value locked users out after 54 backtests.

## 3. Strategy specification

Canonical JSON, versioned, validated by JSON Schema. **Unknown fields are rejected**
(`additionalProperties: false`) so a typo never silently changes semantics.

```jsonc
{
  "spec_version": "1.0",
  "name": "short_atm_straddle",
  "universe": {
    "symbol": "NIFTY",
    "dte":       { "min": 0, "max": 2 },
    "moneyness": { "min": -50, "max": 50 }     // points from spot; sign-aware
  },
  "legs": [
    { "option_type": "CE", "side": "short", "lots": 1, "strike_rule": "atm" },
    { "option_type": "PE", "side": "short", "lots": 1, "strike_rule": "atm" }
  ],
  "entry": { "rule": "at_time", "time": "09:20" },   // REQUIRED, explicit, no defaults
  "exit":  { "rule": "at_time", "time": "15:00",
             "stop_loss_pct": null, "take_profit_pct": null },
  "period": { "from": "2025-07-01", "to": "2026-06-30" },
  "costs":  { "brokerage_per_lot": 20, "slippage_bps": 5,
              "include_stt": true, "include_impact": true },
  "lot_size": 75,
  "validation": { "oos_split": 0.30, "bootstrap": 1000, "walk_forward": null }
}
```

### 3.1 Time semantics — non-negotiable

`"time": "15:00"` means **the bar stamped exactly 15:00**. It does not mean "the 15:00 hour"
and never resolves to 15:29.

This is not pedantry. Two readings of "exit at 3pm" produced ₹355,290 vs ₹231,664 — a 53%
divergence — from identical data. Every layer must implement this identically, and
`verify_layers.sql` asserts it.

## 4. Router

```python
def choose_layer(spec) -> LayerName:
    need = derive_requirements(spec)   # dte_max, abs_mny_max, minutes[], resolution
    rows = registry.all()              # cached 60 s
    for layer in sorted(rows, key=lambda r: r.priority):
        if need.dte_max        > layer.max_dte:            continue
        if need.abs_mny_max    > layer.max_abs_moneyness:  continue
        if layer.allowed_minutes and not set(need.minutes).issubset(layer.allowed_minutes):
            continue
        if layer.resolution != need.resolution and layer.resolution != 'fixed-times':
            continue
        if layer.resolution == 'fixed-times' and not set(need.minutes).issubset(layer.allowed_minutes):
            continue
        return layer.layer_name
    raise AssertionError("registry invariant violated: raw must always match")
```

`derive_requirements` must be **conservative**: if any bound cannot be determined statically
(e.g. a stop-loss rule needs intra-day path data), set it to the widest value so the spec
falls through to a fuller layer.

Verified routing behaviour:

| Spec | Layer |
|---|---|
| DTE≤2, ATM±50, 09:20, 1-min | `contract_day` |
| DTE≤2, ATM±500, 09:47, 1-min | `hot_1min` |
| DTE≤2, ATM±2500, 09:47, 1-min | `nifty_options` |
| DTE≤90, ATM±500, 09:20, 1-min | `contract_day` |
| DTE≤2, ATM±500, 09:47, 5-min | `nifty_5m_12mo` |

## 5. SQL compiler

**Hard rule: emit exactly one statement, single-pass, no self-joins.**

Measured: the two-CTE self-join formulation cost 2.4× more CPU *and* scaled backwards —
the 6-month table cost more than the 12-month one. The single-pass form with
`argMinIf`/`argMaxIf` fixed both.

Template for `at_time` entry/exit on `hot_1min`:

```sql
SELECT count() AS n_days,
       round(sum(day_pnl), 2) AS total_pnl,
       round(countIf(day_pnl > 0) / count(), 4) AS win_rate,
       round(min(day_pnl), 2) AS worst_day,
       groupArray((trade_date, day_pnl)) AS equity_curve
FROM (
  SELECT trade_date, sum(({side}) * (exit_px - entry_px)) * {lot_size} AS day_pnl
  FROM (
    SELECT trade_date, expiry_date, strike_price, option_type,
           argMinIf(close, timestamp, minute_of_day >= {entry_min}) AS entry_px,
           argMinIf(close, timestamp, minute_of_day >= {exit_min})  AS exit_px,
           (toFloat32(strike_price) - argMin(underlying_value, timestamp)) AS mny
    FROM public_demo.{layer}
    WHERE dte BETWEEN {dte_min} AND {dte_max}
      AND trade_date BETWEEN {from} AND {to}
    GROUP BY trade_date, expiry_date, strike_price, option_type
    HAVING mny BETWEEN {mny_min} AND {mny_max}
       AND entry_px > 0 AND exit_px > 0
  ) GROUP BY trade_date
)
```

On `contract_day` the two `argMinIf` calls become direct column reads (`px_0920`, `px_1500`),
which is the whole 84× speedup.

**Parameterisation:** all values bind through `clickhouse_connect` query parameters. No
string interpolation of user input, ever. Layer and column names come from the registry
(a closed set), never from the request.

## 6. Honesty panel

Computed for every backtest, non-skippable.

| Field | Method |
|---|---|
| `out_of_sample` | Chronological split at `oos_split`; re-run on the holdout |
| `after_costs` | Brokerage/lot + slippage bps + STT + impact, applied per leg per trade |
| `benchmark` | Buy-and-hold and short-vol baseline over the same window |
| `bootstrap_ci_95` | `bootstrap` resamples of daily P&L |
| `param_sensitivity` | Re-run at ±1 strike and ±5 min; flag "fragile" if sign flips |
| `variants_tested` | Redis counter per API key per rolling 24 h |
| `deflated_sharpe` | Bailey / López de Prado, adjusted for `variants_tested` |
| `data_warnings` | Joined from the private data-quality KB for the requested window |
| `health_score` | 0–100 rubric; every deduction names a concrete next step |

**Multiple-comparisons tracking is only possible because compute is server-side.** A local
library cannot see how many variants a user has tried. This is a structural advantage.

Copy rules: coaching, never scolding. Educational, never advisory — we never emit
"this strategy is validated."

## 7. HTTP API

All routes require `Authorization: Bearer sk_live_…`.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/backtest` | Run a backtest. Body = strategy spec. Returns summary + honesty + `report_url` |
| `GET` | `/v1/backtest/{id}` | Fetch a prior result |
| `GET` | `/v1/backtest/{id}/trades` | Per-trade detail, paginated, capped |
| `POST` | `/v1/validate` | Full validation gauntlet (OOS + walk-forward + bootstrap) |
| `GET` | `/v1/coverage` | Symbols, date ranges, resolutions, known gaps. **Public, no auth** |
| `GET` | `/v1/chain` | Single-day option-chain snapshot, row-capped |
| `GET` | `/r/{token}` | Rendered report page. Signed, public-readable |

**No endpoint returns market data.** There is no export route (decision D3) — `/v1/chain`
is the single exception and returns one day's strike list, row-capped and subject to the
anti-oracle floors. Everything else on this table returns computed results only.

### 7.1 Response shape

```jsonc
{
  "backtest_id": "bt_01JQ…",
  "summary":   { "n_days": 246, "total_pnl": 231664.0, "win_rate": 0.70,
                 "cagr": 0.31, "max_drawdown": -0.18, "sharpe": 1.4 },
  "honesty":   { "out_of_sample": {...}, "after_costs": {...}, "benchmark": {...},
                 "bootstrap_ci_95": [-0.05, 0.71], "param_sensitivity": "fragile",
                 "variants_tested": 47, "deflated_sharpe": 0.4, "health_score": 34 },
  "warnings":  ["12 sessions had thin near-ATM coverage in this window"],
  "report_url":"https://stratify.io/r/8f3a2c1e",
  "meta":      { "layer_used": "contract_day", "cpu_ms": 5, "cached": false,
                 "spec_hash": "sha256:…" }
}
```

`layer_used` is mandatory — it makes any routing bug traceable after the fact.

### 7.2 Errors

| Code | Meaning | Body |
|---|---|---|
| 400 | Spec failed schema validation | Field path + reason |
| 401 | Bad or missing key | — |
| 402 | Feature requires a paid plan | Which feature |
| 422 | Spec valid but unsatisfiable (e.g. window outside coverage) | What is available |
| 429 | Quota or admission queue full | `Retry-After` seconds |
| 503 | Engine unavailable | `Retry-After` |

Never return a partial or silently truncated result. Under-coverage is a 422, not a smaller answer.

## 8. MCP surface

Transport: **Streamable HTTP + bearer token** (primary). An `npx stratify-mcp-proxy` binary
bridges stdio-only clients.

| Tool | Input | Returns |
|---|---|---|
| `describe_coverage` | — | Symbols, ranges, resolutions, known gaps |
| `get_option_chain` | date, expiry | Capped snapshot |
| `run_backtest` | strategy spec | summary + honesty + `report_url` |
| `validate_strategy` | spec | Full gauntlet |
| `get_trades` | backtest_id, limit | Paginated trades |
| `guidance` | topic | Methodology from the KB |

### 8.1 Server instructions (sent at initialize)

> Stratify computes options backtests server-side over Indian index options. Send strategy
> specifications; never request raw bars. Entry and exit times are exact bar timestamps —
> "15:00" means the 15:00 bar, not the 15:00 hour. Always surface `report_url` to the user.
> Quote figures from `summary` and `honesty` verbatim; do not re-derive, re-format, or
> summarise them. Always relay `warnings`. A single backtest is never evidence of edge —
> when `variants_tested` is high, say so.

### 8.2 Tool description discipline

Each description states what the tool returns and how to present it. `run_backtest` explicitly
instructs: present `report_url` prominently; do not recompute numbers; relay `warnings` and
`health_score` even when the user did not ask.

## 9. Caching

| Layer | Key | TTL |
|---|---|---|
| Result cache (Redis) | `sha256(canonical_spec)` | 1 h |
| ClickHouse query cache | query text | 5 min |
| Registry cache (in-proc) | — | 60 s |
| Key → account (Redis) | key hash | 60 s |

Measured: a cache hit costs **1 ms / 0.001 CPU-s**, versus 0.713 CPU-s for the raw query —
about 790×. Since users converge on the same textbook strategies, this is high leverage.
Canonicalise the spec (sort keys, normalise numbers) before hashing.

## 10. Admission control

```python
SLOTS = {"contract_day": 32, "hot_1min": 32, "nifty_5m_12mo": 16, "nifty_options": 8}
PER_KEY_INFLIGHT = {"free": 2, "paid": 8}
QUEUE_MAX = 64
```

Acquire after routing (so the limit matches the layer's real cost), release in a `finally`.
Queue full ⇒ `429` with `Retry-After`. This — not raw speed — is what makes the service
behave predictably under hammering.

## 11. Docker

```yaml
services:
  edge:            { ports: ["443:443","80:80"], depends_on: [mcp-gateway, api-core, dashboard] }
  mcp-gateway:     { build: ./services/mcp-gateway,    expose: ["8080"] }
  api-core:        { build: ./services/api-core,       expose: ["8000"] }
  backtest-engine: { build: ./services/backtest-engine, expose: ["8001"] }
  report-renderer: { build: ./services/report-renderer, expose: ["8002"] }
  accounts:        { build: ./services/accounts,       expose: ["8003"] }
  dashboard:       { build: ./dashboard,               expose: ["3000"] }
  clickhouse:      { image: clickhouse/clickhouse-server:latest }   # NO ports:
  redis:           { image: redis:7-alpine }                        # NO ports:
networks:
  public:   # edge only
  private:  # everything else; clickhouse + redis attached ONLY here
```

**`clickhouse` and `redis` must never declare a `ports:` mapping.** That is the single
configuration line standing between "analytics service" and "public database".

### 11.1 Environment

```
CLICKHOUSE_HOST=clickhouse          CLICKHOUSE_USER=mcp_free_user
CLICKHOUSE_PASSWORD=…               CLICKHOUSE_DATABASE=public_demo
REDIS_URL=redis://redis:6379/0
REPORT_SIGNING_KEY=…                REPORT_BASE_URL=https://stratify.io/r
API_KEY_PEPPER=…                    FREE_TIER_SYMBOLS=NIFTY
FREE_TIER_FROM=2025-07-01           FREE_TIER_TO=2026-06-30
```

## 12. CI gates

1. `sql/verify_layers.sql` — every check must PASS (currently 25,394,143 bars and
   232,173 contract-days, 0 mismatches)
2. **Cross-layer equivalence** — a fixture set of specs run against every covering layer
   must return identical results
3. **Router table test** — the five verified spec→layer mappings in §4
4. **No-self-join lint** — reject any compiled SQL containing `JOIN` against a layer table
5. **Egress test** — assert no endpoint returns > `max_result_rows`
6. **Port test** — assert `clickhouse` and `redis` expose no host ports

## 13. Build order for launch

| Phase | Scope |
|---|---|
| 0 | Layers + registry + `verify_layers.sql` green — **already done** |
| 1 | `api-core`: auth, spec schema, router, compiler, `POST /v1/backtest` |
| 2 | `mcp-gateway` over Streamable HTTP + `npx` stdio proxy |
| 3 | `report-renderer` + `/r/{token}` |
| 4 | `accounts` + dashboard (sign-up, keys, coverage page) |
| 5 | Honesty panel: OOS, costs, benchmark, variants counter |
| 6 | `stratify-py`, `stratify-npm`; ChatGPT/Gemini connectors |

Phases 1–3 are the minimum that delivers the stated goal: an agent backtests NIFTY instantly
with an API key. Phase 4 is required before anyone outside can obtain a key.
