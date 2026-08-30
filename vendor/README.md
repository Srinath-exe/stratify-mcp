# `vendor` — runtime inputs the engine loads but does not own

The engine reads three inputs from paths given by environment variables rather than
hard-coding them, so that this service, the research pipeline and the live paper-trading
daemon cannot drift into two definitions of "bullish" or two SPAN ratios.

**None of the three is included in this repository.** Two of them live in a private
research repo; the third is a live trading database. Supply your own, or run without them
and accept the documented behaviour below.

| Input | Environment variable | Without it |
|---|---|---|
| `signals.py` | `STRATIFY_SIGNALS_PATH` | Gates and biases raise on first use. Every other tool works. |
| `margin_calibration.json` | `STRATIFY_MARGIN_CALIBRATION` | Naked-short margin — and therefore return-on-margin — cannot be computed. |
| `paper_trades.sqlite` | `STRATIFY_PAPER_TRADING_DB` | Slippage falls back to the documented assumed half-spread **and says so** in the response. This is the designed path, not a degradation. |

## `signals.py`

A module exposing exactly two module-level dicts:

```python
GATES  = {"name": callable(spot_df, asof_date) -> bool}
BIASES = {"name": callable(spot_df, asof_date) -> str}   # "bullish" | "bearish" | "neutral"
```

`spot_df` is a pandas frame of daily spot OHLC; `asof_date` is the entry date. Both
callables must read **only rows strictly before `asof_date`** — the engine does not police
this for you, and a lookahead here silently inflates every gated backtest.

`engine/signals.py` loads the module by path, calls `sorted(GATES)` / `sorted(BIASES)` to
advertise names, and rejects any name not present. Anything satisfying that contract works.

The production module is ordinary technical analysis — RSI, MACD, Bollinger, Donchian, ATR,
EMA crossover, gap, day-of-week — and depends only on pandas.

## `margin_calibration.json`

Maps a strike offset, per symbol, to a measured SPAN margin for **one strangle** (1 short CE
+ 1 short PE) against one lot of notional:

```json
{"NIFTY": {"1": {"margin_rupees": 206372.74,
                 "notional_rupees": 1565089.5,
                 "margin_pct_of_notional": 0.13186,
                 "spot": 24078.3, "lot_size": 65}}}
```

Keys are integer strike steps out of the money, as strings. The production file is measured
against a broker's live SPAN calculator on a single day and then applied historically —
a today-calibrated ratio applied to the past. That is a real limitation, and every response
that uses it carries a caveat saying so. Read the docstring at the top of
`engine/config/margin.py` before you trust a return-on-margin figure from a naked structure.

## `paper_trades.sqlite`

A live paper-trading book recording `entry_slippage_pts` / `exit_slippage_pts` per fill.
`engine/config/slippage.py` switches from the assumed half-spread to the measured one once
the book holds 30 fills, and reports which of the two it used.
