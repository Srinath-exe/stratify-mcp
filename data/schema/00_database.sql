-- Stratify serving database.
-- Rebuilt from fno.* with an explicit, documented contract. See ../README.md.
CREATE DATABASE IF NOT EXISTS stratify;

-- Per-trade-date source ranking. The three ingestion sources (breeze, scraper, fyers)
-- run as a relay across the window; the primary source changes month to month, so
-- precedence is derived from observed density per date rather than hard-coded.
DROP TABLE IF EXISTS stratify.source_precedence;
CREATE TABLE stratify.source_precedence
( trade_date Date, source LowCardinality(String), n_rows UInt64, rank UInt8 )
ENGINE = MergeTree ORDER BY (trade_date, rank);

INSERT INTO stratify.source_precedence
SELECT trade_date, source, n_rows,
       toUInt8(row_number() OVER (PARTITION BY trade_date ORDER BY n_rows DESC, source)) AS rank
FROM (
  SELECT toDate(timestamp) AS trade_date, source, count() AS n_rows
  FROM fno.options
  WHERE symbol = 'NIFTY' AND instrument = 'OPTIDX'
    AND timestamp >= '2025-07-01' AND timestamp < '2026-07-01'
  GROUP BY trade_date, source
);
