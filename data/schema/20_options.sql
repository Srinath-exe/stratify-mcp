-- NIFTY option bars, 1-minute, deduplicated and spot-joined.
--
-- Two defects in public_demo.nifty_options are fixed structurally here:
--
--   1. 1,479,308 duplicate (expiry, strike, type, minute) keys — 341,702 of them holding
--      CONFLICTING prices. Root cause: three ingestion sources (breeze / scraper / fyers)
--      overlap, and fno.options' ReplacingMergeTree cannot collapse them because
--      strike_offset and moneyness (which differ per source) sit in its ORDER BY.
--      Fix: pick ONE source per contract-minute, by the per-date precedence in
--      stratify.source_precedence, tie-broken by latest ingestion_time. The whole bar is
--      taken from a single row via argMin over a tuple — never assembled field by field.
--
--   2. underlying_value was <= 0 on 51 % of rows (100 % in 2025-07 and 2026-06).
--      Fix: the column does not exist here. Spot is joined from stratify.spot_1min, which
--      is guaranteed present for all 246 trading days, and moneyness derives from it.
--
-- See the 2026-08-21 data audit Findings 1 and 2.

DROP TABLE IF EXISTS stratify.options_1min;
CREATE TABLE stratify.options_1min
( expiry_date  Date,
  strike_price UInt32,
  option_type  Enum8('CE' = 1, 'PE' = 2),
  timestamp    DateTime('Asia/Kolkata'),
  open Float32, high Float32, low Float32, close Float32,
  volume UInt32, open_interest UInt32,
  spot Float32,
  src  Enum8('breeze' = 1, 'scraper' = 2, 'fyers' = 3),
  dte       UInt16 MATERIALIZED dateDiff('day', toDate(timestamp), expiry_date),
  moneyness Int32  MATERIALIZED toInt32(strike_price) - toInt32(spot) )
ENGINE = MergeTree
PARTITION BY toYYYYMM(timestamp)
ORDER BY (expiry_date, strike_price, option_type, timestamp);
