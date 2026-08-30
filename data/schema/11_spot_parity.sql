-- Third spot source: put-call parity, for minutes neither the index feed nor
-- underlying_value covers (3 days in 2025-07/08, inside the breeze-only period).
--
--   S = C - P + K * exp(-r * dte/365),  r = 6.5 %,  median across all strikes
--   with both legs traded and dte <= 10.
--
-- Validated against the index feed on 4 full control days (1,500 minutes):
--   mean bias 2.41 pts · mean abs error 10.52 pts · p95 26.65 pts, on spot ~25,000
--   (0.042 % mean). Strike interval is 50 pts, so the p95 case can shift a selected
--   strike by half an interval. Acceptable for 3 of 246 days when the alternative is
--   no spot at all, and flagged src='parity' so every consumer can see it.
--
-- Dropping the discount term costs 22 pts of bias — it is not optional.

ALTER TABLE stratify.spot_1min MODIFY COLUMN src
  Enum8('index_feed' = 1, 'options_uv' = 2, 'parity' = 3);

INSERT INTO stratify.spot_1min
SELECT ts, s, s, s, s, 0, 'parity'
FROM (
  SELECT ts, median(s_est) AS s
  FROM (
    SELECT c.timestamp AS ts,
           c.close - p.close + c.strike_price * exp(-0.065 * (c.dte / 365.0)) AS s_est
    FROM stratify.options_1min AS c
    INNER JOIN stratify.options_1min AS p
      ON  c.expiry_date = p.expiry_date AND c.strike_price = p.strike_price
      AND c.timestamp   = p.timestamp
    WHERE c.option_type = 'CE' AND p.option_type = 'PE'
      AND c.dte <= 10 AND c.volume > 0 AND p.volume > 0
      AND c.timestamp NOT IN (SELECT timestamp FROM stratify.spot_1min)
  )
  GROUP BY ts
  HAVING count() >= 5          -- need a real cross-section for the median to mean anything
);
