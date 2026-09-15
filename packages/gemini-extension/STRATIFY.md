# Stratify

Backtesting for Indian index options on real 1-minute NIFTY data, computed server-side.

## Before you run anything

Call `describe_coverage` once. It returns the exact date window, the structures, the
signal gates and the entry/exit clock times this deployment accepts. Guessing a parameter
that is not on that list is the most common reason a spec is refused.

Reading coverage and methodology does **not** spend the backtest allowance — they are
metered separately, so there is no reason to skip them.

## What the results mean

- **No ratio is reported below 30 trades.** A Sharpe on 12 trades is noise, so the service
  returns `insufficient_sample` rather than a number that looks like evidence.
- **Costs are real**, not a percentage haircut: brokerage per leg, STT sell-side, exchange
  and SEBI fees, GST, stamp duty.
- **Slippage is modelled, not measured**, and says so. Treat it as an input you can change.
- **Margin is modelled from a Fyers SPAN calibration**, not live SPAN. Return-on-margin for
  naked short structures is therefore optimistic in a shock.
- The honesty panel's `health_score` is a **published rubric**, not a verdict. The service
  never says a strategy is good.

## How to be useful with it

Do not present a single backtest as a finding. Run the strategy, then look at what the
honesty panel says about it: whether the held-out final 30% still worked, whether the
walk-forward folds agree, and how wide the bootstrap interval is. A strategy that only
works in-sample is the normal outcome, and saying so is the useful answer.

`report_url` on every result is a full rendered report — calendar, equity curve, every
trade — and costs nothing extra to share.

## Not advice

Historical simulation on recorded data. Not investment advice, not a recommendation, and
not a forecast.
