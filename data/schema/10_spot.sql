-- NIFTY spot, 1-minute OHLC, for the full 246-day window.
--
-- Primary source  : fno.spot (symbol='NIFTY') — true OHLC from the index feed.
-- Fallback source : fno.options.underlying_value, aggregated across all contracts
--                   reporting that minute. Used only for minutes the primary lacks.
--                   Marked src='options_uv'; open and close are the per-minute median
--                   (sub-minute ordering is not recoverable), high/low are true extremes.
--
-- Rationale: the previous public_demo.nifty_spot_1min held close only and covered 182 of
-- 246 days. Six of the ten biases in signals.py need high/low/open and could not be
-- computed at all. See the 2026-08-21 data audit Finding 3.

DROP TABLE IF EXISTS stratify.spot_1min;
CREATE TABLE stratify.spot_1min
( timestamp DateTime('Asia/Kolkata'),
  open Float64, high Float64, low Float64, close Float64,
  volume UInt64,
  src   Enum8('index_feed' = 1, 'options_uv' = 2) )
ENGINE = MergeTree
PARTITION BY toYYYYMM(timestamp)
ORDER BY timestamp;

-- 1. primary
INSERT INTO stratify.spot_1min
SELECT toDateTime(toStartOfMinute(timestamp), 'Asia/Kolkata') AS ts,
       argMin(open, timestamp), max(high), min(low), argMax(close, timestamp),
       sum(volume), 'index_feed'
FROM fno.spot
WHERE symbol = 'NIFTY' AND timestamp >= '2025-07-01' AND timestamp < '2026-07-01'
GROUP BY ts;

-- 2. fallback, only for minutes the index feed does not cover
INSERT INTO stratify.spot_1min
SELECT ts, med, mx, mn, med, 0, 'options_uv'
FROM (
  SELECT toDateTime(toStartOfMinute(timestamp), 'Asia/Kolkata') AS ts,
         max(underlying_value) AS mx, min(underlying_value) AS mn,
         median(underlying_value) AS med
  FROM fno.options
  WHERE symbol = 'NIFTY' AND instrument = 'OPTIDX' AND underlying_value > 0
    AND timestamp >= '2025-07-01' AND timestamp < '2026-07-01'
  GROUP BY ts
) AS f
WHERE ts NOT IN (SELECT timestamp FROM stratify.spot_1min);
