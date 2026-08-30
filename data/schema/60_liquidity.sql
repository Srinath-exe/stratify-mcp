-- Measured liquidity profile, by days-to-expiry and distance from the money.
--
-- What this is FOR: deciding whether a strike is realistically tradeable, and with what
-- confidence, before a backtest is allowed to trade it. 58 % of all contract-minutes have
-- zero volume -- normal for an option chain, but it means "there is a bar" and "you could
-- have traded" are different statements, and the engine must not confuse them.
--
-- Distance is SIGNED relative to the option's own direction: otm_pts = strike - spot for
-- a call, spot - strike for a put. Positive is out-of-the-money, negative is in-the-money.
-- Using abs(moneyness) conflates the two and is wrong here -- an ITM option 250 points from
-- spot trades nothing like an OTM one at the same distance.
--
-- What this is NOT: a spread estimate. See ../the 2026-08-21 data audit and engine/config/
-- slippage.py -- spread is not recoverable from OHLCV and is modelled, not measured.

DROP TABLE IF EXISTS stratify.liquidity_profile;
CREATE TABLE stratify.liquidity_profile
( dte_bucket LowCardinality(String), otm_bucket Int16,
  n_contract_days UInt32, n_minutes UInt64,
  pct_minutes_traded Float32,
  median_volume_lots Float32, median_price_pts Float32,
  p10_price_pts Float32, median_day_volume_lots Float32 )
ENGINE = MergeTree ORDER BY (dte_bucket, otm_bucket);

INSERT INTO stratify.liquidity_profile
SELECT dte_bucket, otm_bucket,
       toUInt32(uniqExact((trade_date, expiry_date, strike_price, option_type))),
       toUInt64(sum(n_min)),
       round(100 * sum(n_traded) / sum(n_min), 2),
       round(median(med_vol_lots), 1), round(median(med_px), 2),
       round(quantile(0.1)(med_px), 2), round(median(day_vol_lots), 1)
FROM (
  SELECT toDate(o.timestamp) AS trade_date, o.expiry_date, o.strike_price, o.option_type,
         multiIf(o.dte = 0, '0', o.dte = 1, '1', o.dte <= 3, '2-3',
                 o.dte <= 7, '4-7', o.dte <= 15, '8-15', '16-45') AS dte_bucket,
         toInt16(greatest(-1000, least(2000,
           intDiv(if(o.option_type = 'CE', o.moneyness, -o.moneyness), 250) * 250))) AS otm_bucket,
         count() AS n_min, countIf(o.volume > 0) AS n_traded,
         medianIf(o.volume / cs.lot_size, o.volume > 0) AS med_vol_lots,
         median(o.close) AS med_px,
         sum(o.volume) / any(cs.lot_size) AS day_vol_lots
  FROM stratify.options_1min AS o
  INNER JOIN stratify.contract_spec AS cs ON cs.expiry_date = o.expiry_date
  WHERE o.dte <= 45
  GROUP BY trade_date, o.expiry_date, o.strike_price, o.option_type, dte_bucket, otm_bucket )
GROUP BY dte_bucket, otm_bucket;
