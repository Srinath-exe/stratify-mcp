-- Derived serving layers, built from stratify.options_1min (unique, spot-complete).
--
-- The fixed-time snapshots are the exact entry_time values used by the 39 live
-- paper-trading strategies (09:30, 11:00, 12:30, 14:00, EOD) plus open, noon, 13:00
-- and 15:00. A backtest whose entry and exit both land on these minutes is served from
-- contract_day at ~84x less CPU than the raw table, with identical numbers.
--
-- 15:29 is EOD. It is NOT 15:00. Conflating the two previously produced a 53 % P&L
-- discrepancy between the raw and fast paths; the column names here are deliberately
-- explicit so "exit at 3pm" can never silently mean two different things again.

DROP TABLE IF EXISTS stratify.contract_day;
CREATE TABLE stratify.contract_day
( trade_date Date, expiry_date Date, strike_price UInt32,
  option_type Enum8('CE'=1,'PE'=2), dte UInt16,
  open Float32, high Float32, low Float32, close Float32,
  volume UInt64, oi_close UInt32,
  spot_open Float32, spot_close Float32, moneyness Int32,
  px_0915 Float32, px_0930 Float32, px_1100 Float32, px_1200 Float32,
  px_1230 Float32, px_1300 Float32, px_1400 Float32, px_1500 Float32, px_1529 Float32,
  n_bars UInt16, traded_bars UInt16 )
ENGINE = MergeTree
PARTITION BY toYYYYMM(trade_date)
ORDER BY (dte, moneyness, trade_date, expiry_date, strike_price, option_type);

INSERT INTO stratify.contract_day
SELECT toDate(timestamp) AS trade_date, expiry_date, strike_price, option_type,
       any(dte),
       argMin(open, timestamp), max(high), min(low), argMax(close, timestamp),
       sum(volume), argMax(open_interest, timestamp),
       argMin(spot, timestamp), argMax(spot, timestamp),
       toInt32(strike_price) - toInt32(argMin(spot, timestamp)),
       argMaxIf(close, timestamp, mod_ <= 555), argMaxIf(close, timestamp, mod_ <= 570),
       argMaxIf(close, timestamp, mod_ <= 660), argMaxIf(close, timestamp, mod_ <= 720),
       argMaxIf(close, timestamp, mod_ <= 750), argMaxIf(close, timestamp, mod_ <= 780),
       argMaxIf(close, timestamp, mod_ <= 840), argMaxIf(close, timestamp, mod_ <= 900),
       argMaxIf(close, timestamp, mod_ <= 929),
       count(), countIf(volume > 0)
FROM ( SELECT expiry_date, strike_price, option_type, timestamp,
              open, high, low, close, volume, open_interest, spot, dte,
              toHour(timestamp) * 60 + toMinute(timestamp) AS mod_
       FROM stratify.options_1min )
GROUP BY trade_date, expiry_date, strike_price, option_type;

-- Full 1-minute fidelity where nearly all real strategies live: near expiry, near the money.
DROP TABLE IF EXISTS stratify.hot_1min;
CREATE TABLE stratify.hot_1min
( expiry_date Date, strike_price UInt32, option_type Enum8('CE'=1,'PE'=2),
  timestamp DateTime('Asia/Kolkata'), minute_of_day UInt16,
  open Float32, high Float32, low Float32, close Float32,
  volume UInt32, open_interest UInt32, spot Float32, moneyness Int32, dte UInt16 )
ENGINE = MergeTree
PARTITION BY toYYYYMM(timestamp)
ORDER BY (dte, moneyness, expiry_date, strike_price, option_type, timestamp);

INSERT INTO stratify.hot_1min
SELECT expiry_date, strike_price, option_type, timestamp,
       toHour(timestamp) * 60 + toMinute(timestamp),
       open, high, low, close, volume, open_interest, spot, moneyness, dte
FROM stratify.options_1min
WHERE dte <= 45 AND abs(moneyness) <= 2000;
