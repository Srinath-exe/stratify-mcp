# Frozen Decisions — v1

**Date frozen:** 2026-08-21 · **Source:** the 30 open questions (internal)
answered "accepted as recommended" with two clarifications (E2, F1).

This file is the build's source of truth. Where a spec document and this file disagree,
**this file wins** and the spec document is stale.

**Amended 2026-09-06 (§0, §7):** OAuth 2.1 was *built*, not bought — `server/mcpauth.py`,
with PKCE, dynamic client registration and client-ID metadata documents — because every
provider quoted needed the identity to live with them, and identity here is the Google
sign-in the site already had. Bearer keys remain the header path. ChatGPT, the Gemini web
app and Claude's connector directory are therefore reachable. Everything else in §0 stands:
no approval step, no usage inspection, quotas as the only bound.

**Amended 2026-09-16 (§0):** an admin-*issued* password sign-in exists (`/login`) so a
directory reviewer can be handed working credentials. It is not a signup — nobody can set
a password for themselves, and there is no reset flow.

---

## 0. Access model (the decision that framed the rest)

> "we are not looking to see anything so just will be giving access after making people
> signup and generate api keys, and thats about it — they can do whatever they want with
> the 1 year of data backtesting"

- Public self-serve **signup**, then self-serve **API key** generation.
- Key is used as `Authorization: Bearer sk_live_…`.
- **No approval step, no waitlist, no manual review, no usage inspection.**
- Freedom is bounded only by the quotas in §5 and the anti-oracle floors in §6 — both
  automated, neither requiring a human in the loop.
- **No OAuth in v1.** ChatGPT web/desktop and Gemini are therefore *not* supported at launch
  (ChatGPT offers OAuth-or-none; it cannot send a bearer header). Phase 2 buys OAuth from a
  provider (WorkOS / Logto / Ory) rather than building it.

---

## 1. Strategy specification (A1–A5)

Public spec adopts the shape already proven across 39 live paper-trading strategies:

```jsonc
{ "structure": "short_strangle", "symbol": "NIFTY",
  "params": { "pct_offset": 1.5, "pct_width": 1.0, "sl_mult": 2.0, "tp_pct": 0.5 },
  "entry_time": "09:30",          // "HH:MM" or null for EOD
  "gate": "always", "bias": "calendar",
  "period": { "from": "2025-07-01", "to": "2026-06-30" } }
```

- **Structures in v1 (5):** `short_strangle`, `iron_condor`, `iron_fly`, `credit_spread`,
  `long_option`.
- **`debit_spread` is held back** — a live Fyers SPAN check confirmed a margin-sizing bug in
  `structures.py`, producing implausible 200–900 % CAGR. It ships when the bug is fixed and
  re-verified, not before.
- **Gates (4)** and **biases (10)** from `signals.py` are all exposed by name. They are
  conditioning logic, not data. 5 × 4 × 10 = 200 base combinations before parameters.
- **Strike selection:** nearest-listed-strike to a target **percent distance from spot**
  (the live convention). ATM = nearest-listed-strike to spot. The CE/PE-price-parity ATM
  used in `BACKTEST/strategies/options_iron_condor.py` is a *different* definition and is
  explicitly not what the public spec means.
- **Structures only.** No arbitrary user code in v1 — it caps the compiler surface,
  guarantees single-pass SQL, and keeps the anti-oracle floors enforceable.

---

## 2. Cost model (B1)

`weekly_options_research/charges.py` is the model of record, used **verbatim**:

| Component | Rate |
|---|---|
| Brokerage | ₹20 per order per leg |
| STT | 0.15 %, sell side only |
| Exchange txn | NSE 0.03553 % · BSE 0.0325 % |
| SEBI | ₹10 per crore |
| GST | 18 % on (brokerage + exchange + SEBI) |
| Stamp duty | 0.003 %, buy side |
| Worthless threshold | 0.05 points |

**Known gap, surfaced in every response's `warnings`:** deep-ITM assignment carries STT on
intrinsic value and is **not modelled**.

**Amended 2026-08-22 — the model is now direction-aware.** `two_leg_charges()` assumes
entry is a sell and exit a buy, which is right for every credit structure and **wrong for
`long_option`**, which ships in v1. STT is sell-side only and stamp duty buy-side only, so
the credit formula applied to a debit trade charges STT on the buy and omits it on the
sell. `engine/config/charges.py` takes leg direction; a parametrized test asserts it is
byte-identical to the production function across 18 credit cases, so the port cannot drift.

## 3. Slippage (B2) — **amended 2026-08-22**

The intent stands; the data does not exist yet. Two things changed after building it:

- **Paper trading has one trade, with no slippage recorded.** The daemon went live
  2026-08-20. `measured_half_spread_pts()` reads the live book when it holds 30 fills
  (matching the C1 floor) and otherwise returns `None` **with a reason** — it never
  silently falls back to the assumption.
- **Slippage cannot be inferred from the served data, and this was checked, not assumed.**
  Roll (1984) needs negative serial covariance; measured on Feb-2026 weeklies it is
  *positive* in every near-the-money bucket (+0.54, +0.37, +0.32) because option prices
  trend with the underlying. Corwin-Schultz (2012) returns 0.008–0.028 points OTM, below
  the 0.05 tick — sub-tick output is proof the estimator fails, not a tight spread.

**So v1 ships slippage as a declared assumption**, floored at half a tick, presented in the
report as an input the user can change, with the basis stated. Measured slippage remains
the differentiator and switches on by itself once the live book is deep enough.

## 4. Margin and fills (B3, B4)

- Report **return-on-margin** alongside P&L, using `naked_margin_pct` /
  `naked_margin_pct_by_distance`. Flagged as *modelled, not live SPAN*.
- Fills priced off **real 15-minute-bucketed prints** (`entry_snapshot()`), with
  `require_volume=True` at entry. The assumption is restated in every response.

## 4b. Contract specification — measured, not assumed (added 2026-08-21)

Found while rebuilding the serving database; binding on the engine.

- **Lot size changes inside the free-tier window.** Expiries through **2025-12-30 trade in
  lots of 75**; from **2026-01-06 onward, 65**. Measured per expiry against NSE bhavcopy
  (`stratify.contract_spec`), not hard-coded. The flat `LOT_SIZE = {"NIFTY": 65, ...}` in
  `BACKTEST/weekly_options_research/build_intraday_grid.py` understates position size by
  **15.4 %** across the first half of the window — every rupee figure depends on this.
- **Strike-selection tie-break: further OTM.** Targets land on multiples of the 50-point
  strike step, so when the exact target is not listed, `target ± 50` are equidistant and a
  bare `argMin` is non-deterministic — the same backtest could return different strikes on
  re-run. Rule: higher strike for CE, lower for PE.
- **Strike step is 50** for every expiry in the window (measured).
- **`contract_day.moneyness` is relative to 09:15 spot**, not to spot at the entry time. Any
  compiler filtering on it for a later entry must pad by intraday drift or it will silently
  drop strikes.

See `../data/README.md`; the underlying data audit is internal.

---

## 5. Honesty panel — hard numbers (C1–C6)

| Rule | Value |
|---|---|
| Minimum trades before **any** ratio is emitted | **30** — below this, return `insufficient_sample` with `n_trades` and no Sharpe/Calmar/profit-factor at all |
| Sharpe sanity ceiling | **> 4** flagged as probable computation or sample artefact |
| Out-of-sample split | Chronological **70/30** + 3-fold walk-forward. **Never** random |
| Multiple-comparisons window | Rolling **24 h**, reported explicitly ("variant 47 of the last 24 h") |
| Health-score rubric | **Published.** An opaque score reads as marketing |
| Verdicts | **Never** say a strategy is good. Report evidence and its limits. Educational, not advisory |

These are direct fixes for defects observed live in a competitor: Sharpe printed on 2
trades, values of 64.91 and 13.59, and "OVERFITTED — do not trade live" emitted on
`oos_total_trades: 0`.

---

## 6. Limits (D1–D4)

**Anti-oracle floors** — enforced in the spec validator, *before* the router:
- Reject any spec touching **< 20 distinct contracts**.
- Reject any spec spanning **< 20 trading days**.
- **Never** return per-contract P&L.

> **Open question, raised 2026-08-22 by building it.** The floors are measured on what the
> strategy *traded*, and that produces false positives on legitimate selective strategies:
> a `credit_spread` with a `donchian` bias over the full year trades on only 15 days,
> because the bias reads neutral most weeks — and is refused. An attacker's spec looks
> nothing like that; it scans one contract on one day. **Recommendation:** measure the
> floor on the contracts a query *scans*, not the ones a strategy ends up trading, since
> scanning is what leaks. Statistical adequacy is already handled separately and gracefully
> by the 30-trade ratio floor. Not changed unilaterally — this is a security control and
> the call is Srinath's.

**Free-tier quota:** 100 backtests/hour · 60 CPU-seconds/hour · 2 concurrent. At the
measured 5 ms per precomputed backtest, 60 CPU-s is ≈ 12,000 cheap backtests — generous for
real use, tight against abuse.

**Data egress: zero.** `/v1/export` is dropped and will not be built. `/v1/chain` (one day,
row-capped, floors apply) is the only route that shows raw prices at all.

**Free tier stays 1-minute / 12-month / NIFTY.** Fidelity is not the dial we turn — gating is
by symbol and history depth, not by resolution.

### 6b. Paid tier and the one-database decision (added 2026-08-22)

The paid tier serves **2019-01-01 → present, 288.9 M rows, 9.33 GiB** including derived
layers. It lives in the **same database** as the free slice, not a separate one.

Measured, not assumed: an identical free-window backtest costs **0.222 s against a
one-year table and 0.226 s against the seven-year one** — 4 ms, because partition and
primary-key pruning make total table size nearly irrelevant. Two databases would have
bought that 4 ms and cost a duplicated pipeline, two sets of contract tests, and a
standing drift risk — the exact failure that produced `public_demo`'s 1.48 M duplicate
keys and half-empty hot layer.

The tier boundary is a date range, enforced twice: in the spec validator (readable error)
and by a **ClickHouse row policy** on the `stratify_free` user (verified: that user sees
exactly 76,894,449 rows, and a query for pre-2024 data returns 0 rows). Compute isolation
comes from per-tier **settings profiles** capping `max_threads`, plus a global admission
cap on concurrent paid backtests.

**Honest limit:** isolation is imperfect and separation would not have fixed it. Four
concurrent seven-year backtests cost the free tier **28 % of its throughput**, and capping
paid concurrency at 1 only improved that to 33 % retained loss. The contention is CPU on a
shared box, not storage. More cores fix it; a second database does not.

Full measurements are in the internal performance analysis.

---

## 7. Infrastructure (E1–E4)

- **Host:** dedicated-vCPU, 8 vCPU / 16 GB / NVMe. The current box loses 23.7 % of CPU to
  steal under load; moving off it is ~25–30 % for free, before any code change.
- **Auth:** bearer only in v1 (§0). OAuth bought, not built, in phase 2.
- **Reports:** `report_url` always (universal, shareable); `ui://` MCP App as progressive
  enhancement where the client supports it.
- **Endpoint:** `https://stratify-mcp.aeon-labs.site/mcp` — matches the ecosystem norm measured across
  1,538 registry servers (`mcp.*` host, `/mcp` path).

## 8. Launch scope (F1–F4)

Streamable HTTP + bearer · 6 tools · `run_backtest` with honesty panel · report URLs ·
coverage page · signup and key issuance. Covers claude.ai, Claude Desktop, Claude Code,
Codex CLI, Cursor, VS Code.

- **Pricing:** tiered by symbol coverage and history depth, metered on backtests and CPU,
  never gigabytes. Current tiers and prices: https://stratify.aeon-labs.site — the figures
  frozen here on 2026-08-21 have since been superseded.
- **Licence:** MIT on the client repo. The moat is the data, not the code.
- **`search` / `fetch` aliases** ship — thin wrappers over `describe_coverage` and
  `get_backtest`, single string param each, for ChatGPT deep research.

## 9. Legal (G1–G3)

- Spec **hashes** retained indefinitely (metering, caching, dedup); spec **bodies** 30 days,
  then purged. Published **no-training** commitment.
- Positioned as an **educational backtesting tool**. No recommendations, no signals, no
  "trade this". Indian securities lawyer reviews landing copy before launch.

---

## 10. Open actions

| # | Action | Owner | Blocks |
|---|---|---|---|
| ~~1~~ | ~~GFDL / Breeze redistribution terms (G1)~~ — **CLOSED 2026-08-21.** The 1-year window is Srinath's own scraped data, owned outright. No third-party terms apply; not a launch blocker. | — | — |
| 2 | Indian securities lawyer reviews landing copy (G3) | Srinath | Public launch |
| 3 | **PARTIALLY FIXED 2026-08-25.** The margin-sizing bug itself (the thing that produced 200-900% CAGR) was in `margin.py`'s defined-risk branch: it returned pure theoretical max-loss with no floor, when a live Fyers SPAN check found real broker margin runs 16-17x that for a tight combo. Fixed: `margin_points()` now applies the same `MIN_MARGIN_PCT_OF_NOTIONAL` floor the research codebase and paper trading already use, capped at the combo's own width (margin can never exceed a hedged position's structural max loss — see `tests/invariants.py`'s `defined_risk_margin_exceeds_width` check, which this respects). Regression-tested against the exact SENSEX leg shape the live check used (`engine/tests/test_config.py`). This fix also improves `iron_condor`/`credit_spread`/`iron_fly` margin accuracy, which were quietly undermargined the same way. **Still open:** `debit_spread` has no leg-selection/routing implementation in `engine/backtest.py` at all — the margin model was fixed in isolation, but the structure itself was never ported from `weekly_options_research/structures.py`. Spec validator still rejects it. Building that port is separate work, not done here (A4) | — | `debit_spread` in public spec |
| 4 | Provision dedicated-vCPU host (E1) | Srinath | Load-bearing launch |
| 5 | Drop `bench_user` / `bench_prof` ClickHouse objects (benchmark leftovers) | — | Production hardening |
| 6 | Decide whether to drop `public_demo` now that `stratify` supersedes it (1.75 GB, other sessions may reference it) | Srinath | Housekeeping |
| 7 | 3 days (2025-07-21, 08-20, 08-21) have partial index-feed spot, backfilled by put-call parity. Repair from raw source if the files still exist | Srinath | Nothing — parity is validated and flagged |
| ~~8~~ | ~~Decide the anti-oracle floor basis~~ — **DECIDED AND FIXED 2026-08-25 by Srinath: scanned, not traded.** `backtest.py`'s `run()` now measures `n_contracts_scanned`/`n_days_scanned` from `snapshots` (the chain data returned for every candidate cycle, before gate/bias decides whether to trade) instead of from `opened`/`trades`. The honesty-panel-facing `n_contracts`/`n_trading_days` fields are unchanged (still trade-based, still describe what the strategy did). Regression test reproduces the exact donchian-bias false positive this note described and confirms it no longer trips (`engine/tests/test_engine.py::test_coverage_floor_is_measured_on_scans_not_trades`) | — | — |
| ~~9~~ | ~~Move `quota.Concurrency` out of process~~ — **DONE 2026-08-22.** It is now in the service database, and `run.sh` runs one worker per core minus two. Redis is only needed for a multi-BOX deployment | — | — |
| ~~10~~ | ~~Set `STRATIFY_KEY_PEPPER`~~ — **DONE 2026-08-25.** 64-char random value in `stratify_mcp/.env` (gitignored, mode 600). The dev-pepper service database was archived and production started clean; every key issued before today is dead by design | — | — |
| 11 | **SECURITY, FIXED 2026-08-25.** ClickHouse was published on `0.0.0.0:8123` / `:9020` with an empty-password `default` holding full admin (`DROP`, `CREATE USER`, `URL`/`S3`/`FILE`), answering anonymous queries from the internet. Docker publishes past ufw, so the firewall never applied. Now bound to `127.0.0.1`; the MCP container reaches it by container name over an internal Docker network. **Still open:** `default`, `stratify_free` and `stratify_paid` have no password and `::/0` host masks — defence in depth, needs a coordinated sweep of ~62 call sites | Srinath | Hardening |
| 12 | **BUG, FIXED 2026-08-25.** Per-account request and CPU quotas never fired on the deployed database. `usage` was created before `account_id` existed, `ALTER TABLE` appended it last, and a positional `INSERT ... VALUES (?,?,?,?)` wrote cpu_seconds into `account_id` — so `usage_since()` matched nothing. Tests passed because they build a fresh database. All INSERTs now name their columns, and two regression tests cover it | — | — |
| 13 | **BUG, FIXED 2026-08-25.** `ix_usage` indexed a migrated column from inside `SCHEMA`, so `executescript` raised on **connect** for any pre-migration database — taking the whole service down, not one request. Moved to `POST_MIGRATION` | — | — |
| 14 | Point DNS at the box and issue TLS. nginx vhost is live for `mcp.stratify.io` and verified end-to-end via Host header; it serves plain HTTP until a certificate exists | Srinath | Public launch |

Neither open action blocks Phase 1 engineering.
