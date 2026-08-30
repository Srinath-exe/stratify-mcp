-- Contract specification, derived empirically rather than hard-coded.
--
-- WHY THIS TABLE EXISTS: NIFTY's lot size changed inside the free-tier window.
-- Expiries up to 2025-12-30 trade in lots of 75; from 2026-01-06 onward, 65.
-- BACKTEST/weekly_options_research/build_intraday_grid.py carries a flat
-- LOT_SIZE = {"NIFTY": 65, ...} with no date dimension, which understates position size
-- by 15.4 % for the first half of the window. Every rupee P&L figure depends on this.
--
-- Derivation: fno.bhavcopy_ground_truth reports volume in LOTS; our bars report UNITS.
-- The per-expiry median of (our volume / bhavcopy volume) is therefore the lot size,
-- measured against NSE's own published numbers rather than assumed.

DROP TABLE IF EXISTS stratify.contract_spec;
CREATE TABLE stratify.contract_spec
( symbol LowCardinality(String), expiry_date Date,
  lot_size UInt16, strike_step UInt16, n_obs UInt32,
  method Enum8('measured' = 1, 'inferred' = 2) )
ENGINE = MergeTree ORDER BY (symbol, expiry_date);

INSERT INTO stratify.contract_spec
WITH bh AS (
  SELECT date, expiry, toUInt32(strike) AS strike,
         if(`right` = 'call', 'CE', 'PE') AS ot, volume AS bv
  FROM fno.bhavcopy_ground_truth
  WHERE symbol = 'NIFTY' AND instrument = 'OPT'
    AND date >= '2025-07-01' AND date < '2026-07-01' AND volume > 0 ),
cd AS (
  SELECT trade_date AS date, expiry_date AS expiry, strike_price AS strike,
         option_type AS ot, volume AS sv
  FROM stratify.contract_day ),
step AS (
  SELECT expiry_date AS step_expiry, toUInt16(min(gap)) AS strike_step
  FROM ( SELECT expiry_date, strike_price - lagInFrame(strike_price)
                  OVER (PARTITION BY expiry_date ORDER BY strike_price) AS gap
         FROM ( SELECT DISTINCT expiry_date, strike_price FROM stratify.contract_day ) )
  WHERE gap > 0 GROUP BY expiry_date )
SELECT 'NIFTY', expiry,
       toUInt16(round(median(sv / bv))) AS lot_size,
       any(step.strike_step) AS strike_step,
       toUInt32(count()) AS n_obs,
       'measured'
FROM cd INNER JOIN bh USING (date, expiry, strike, ot)
LEFT JOIN step ON step.step_expiry = cd.expiry
GROUP BY expiry HAVING count() >= 50
ORDER BY expiry;
