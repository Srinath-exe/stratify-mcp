# `engine`

Spec in, trades and evidence out. Every module is either **measured** from the serving
database and says so, or an explicit **assumption** with a stated basis — and says that
too. Nothing here is a bare constant.

```
python3 -m pytest engine/tests -q     # 80 tests
```

```python
from engine import backtest, metrics
r = backtest.run({"structure": "iron_condor", "entry_time": "09:30",
                  "params": {"pct_offset": 1.5, "pct_width": 1.0, "entry_dte": 4}})
metrics.summarise(r)
```

| Module | Does |
|---|---|
| `spec.py` | Parses and refuses. Closed validation, anti-oracle floors |
| `backtest.py` | Cycles → strikes → path → exit → P&L |
| `metrics.py` | Summary, with the honesty floors built in |

## How a backtest runs

A **cycle** is one weekly expiry. On the trading day whose DTE matches `entry_dte`, the
structure opens at `entry_time` and is held until a stop or target fires, or until the
contract settles. That is the shape the 39 live strategies actually trade — an intraday
entry time inside a weekly cycle — not a same-day round trip.

**Why it is fast without touching `contract_day`.** Strike selection is the only step that
looks across the chain, and it looks at *one minute* per cycle. Once the legs are chosen,
the path query reads only those contracts, only for the days the position was open:
~58 cycles × 4 legs × a few days. A full-year backtest takes **1–2 seconds** and reads well
under a million rows.

**Sign conventions**, stated once because getting them wrong silently is the failure mode:

```
net_value(t) = Σ (+1 if leg was SOLD else −1) × price(t)
entry_credit = net_value(entry)      positive for credit, negative for debit
exit_value   = −net_value(exit)      positive = credit received on closing
pnl_pts      = entry_credit + exit_value
```

This is paper trading's convention exactly, so a stop-loss rule written for the live system
means the same thing here. `test_exit_trigger_matches_production` lifts
`check_exit_trigger()` out of `paper_trading/engine.py` by AST and asserts agreement across
a grid of 8 configurations × 115 price levels.

**Entry pricing** is the last real print in the 15-minute bucket ending at `entry_time`, and
the strike must have traded in that bucket (decision B4). No modelled fills. **Expiry
settlement** is intrinsic value against the mean of the underlying over the final 30 minutes
— NSE's own convention, which is neither the 15:29 close nor the option's last print.

## What it refuses to do

- **`debit_spread`** — withheld with the reason attached (decision A4).
- **Unknown parameters** — an error, never silently dropped. A spec that quietly ignores a
  parameter the user believed was applied is worse than one that fails.
- **`gate` / `bias`** — not implemented, so they are **rejected**, not accepted-and-ignored.
- **Narrow windows** — the anti-oracle floors (20 contracts, 20 days) are checked both on
  the spec and on what actually ran, since a broad-looking spec can resolve to almost
  nothing.
- **Ratios below 30 trades** — no Sharpe, Calmar or profit factor at all. The descriptive
  numbers still appear; withholding a ratio is not hiding the data.
- **Sharpe above 4** — reported with a flag calling it a probable artefact.

| Module | Basis | Notes |
|---|---|---|
| `config/contracts.py` | **Measured** vs NSE bhavcopy | Lot size and strike step per expiry; deterministic strike selection |
| `config/charges.py` | **Verified rates**, Zerodha 2026 | Direction-aware port of `weekly_options_research/charges.py` |
| `config/margin.py` | **Exact** for defined risk, **calibrated** for naked | Naked shorts flagged optimistic |
| `config/liquidity.py` | **Measured** | Tradeability disclosure by DTE and signed distance |
| `config/slippage.py` | **Assumption** | Spread is not recoverable from OHLCV. See below |

## Three things worth knowing

**Charges are direction-aware now, and that is a fix, not a refactor.** The production
function assumes entry is a sell and exit is a buy — correct for every credit structure,
wrong for `long_option`, which ships in v1. STT is sell-side only and stamp duty buy-side
only, so the credit formula applied to a debit trade charges STT on the buy and omits it on
the sell. `test_matches_production_charges` asserts the port is byte-identical to production
across 18 credit cases, so it cannot drift; `test_debit_entry_pays_stt_on_the_sell_not_the_buy`
pins the case production never had to handle.

**Naked-short margin is optimistic and the report must say so.** Defined-risk combos are
exact — margin is max loss, which is what SPAN recognises. Naked shorts use a ratio
calibrated from Fyers' live SPAN calculator on 2026-08-19 and applied historically, because
SPAN depends on each day's volatility scan range and cannot be retrieved after the fact.
It beats the 11 %-of-notional rule of thumb (Fyers' own number for a 1-step-OTM NIFTY
strangle is 13.19 %), but margin *widens in a shock*, exactly when a naked short is losing.
Return-on-margin from a naked structure is therefore optimistic by an unknown amount.

**Slippage is modelled, and I could not make it measured.** Both standard inferences fail
on OHLCV data, and I checked rather than assumed:

- **Roll (1984)** needs negative serial covariance of price changes. Measured on Feb-2026
  weeklies it is *positive* in every near-the-money bucket (+0.54, +0.37, +0.32) — option
  prices trend with the underlying and that swamps the bid-ask bounce.
- **Corwin-Schultz (2012)** returns 0.008–0.028 points for OTM buckets. Tick size is 0.05,
  so a spread below one tick is impossible; sub-tick output is proof the estimator is not
  working, not a finding about tight spreads.

So slippage is a declared assumption, floored at half a tick, shown in the report as an
input the user can change. The real path to measuring it is `paper_trading`, which records
true `entry_slippage_pts` / `exit_slippage_pts` against live quotes — the differentiator no
competitor has. It is **not usable yet**: the daemon went live 2026-08-20 and the book holds
one trade with no slippage recorded. `measured_half_spread_pts()` reads it when there are
30 fills and otherwise returns `None` with a reason, rather than quietly falling back.
