-- Layer equivalence: the same strategy, priced from contract_day and from options_1min,
-- must return identical P&L. This is the check that stops the fast path and the slow path
-- from silently meaning different things -- the earlier design differed by 53 % because
-- "exit at 3pm" resolved to 15:29 on one path and 15:00 on the other.
--
-- Strategy: short strangle, 1.5 % OTM both legs, entry 09:30, exit 15:29, dte 0-7.
--
-- SCOPED to 2024-01-01 onward, not the full seven years. Not to make it pass: the slow
-- path's join over 289 M rows needs more memory than the serving ceiling allows, and a
-- test that only runs with the safety limits removed is not testing the system that ships.
-- This window still spans three of the four lot-size regimes (25, 50, 75, 65), which is
-- the part of the history most likely to break equivalence.
--
-- STRIKE SELECTION TIE-BREAK. The target strike is always a multiple of the 50-point
-- strike step, so when the exact target is not listed, target-50 and target+50 are
-- EQUIDISTANT and a bare argMin picks arbitrarily -- the two paths disagreed on 4 of 490
-- legs before this was pinned down. The rule is: on a tie, take the strike further OTM
-- (higher for CE, lower for PE). It is the conservative choice for a seller, and more
-- importantly it is deterministic.

WITH ent AS (
  SELECT toDate(timestamp) AS d, close AS spot_entry
  FROM stratify.spot_1min
  WHERE toHour(timestamp) = 9 AND toMinute(timestamp) = 30
    AND toDate(timestamp) >= '2024-01-01' ),
exp AS (
  SELECT trade_date AS d, min(expiry_date) AS expiry
  FROM stratify.contract_day WHERE dte BETWEEN 0 AND 7
    AND trade_date >= '2024-01-01' GROUP BY trade_date ),

fast AS (
  SELECT cd.trade_date AS d, cd.option_type AS ot,
         argMin(cd.px_0930, (abs(toInt32(cd.strike_price) - tgt), otm_rank)) AS entry_px,
         argMin(cd.px_1529, (abs(toInt32(cd.strike_price) - tgt), otm_rank)) AS exit_px
  FROM ( SELECT cd.*, ent.spot_entry,
                toInt32(round(ent.spot_entry * if(cd.option_type = 'CE', 1.015, 0.985) / 50) * 50) AS tgt,
                if(cd.option_type = 'CE', -toInt32(cd.strike_price), toInt32(cd.strike_price)) AS otm_rank
         FROM stratify.contract_day AS cd
         INNER JOIN ent ON ent.d = cd.trade_date
         INNER JOIN exp ON exp.d = cd.trade_date AND exp.expiry = cd.expiry_date
         WHERE cd.traded_bars > 0 ) AS cd
  GROUP BY d, ot ),

slow AS (
  SELECT o.trade_date AS d, o.ot AS ot,
         argMin(o.entry_px, (abs(o.strike - o.tgt), o.otm_rank)) AS entry_px,
         argMin(o.exit_px,  (abs(o.strike - o.tgt), o.otm_rank)) AS exit_px
  FROM ( SELECT toDate(b.timestamp) AS trade_date, b.option_type AS ot,
                toInt32(b.strike_price) AS strike,
                toInt32(round(ent.spot_entry * if(b.option_type = 'CE', 1.015, 0.985) / 50) * 50) AS tgt,
                if(b.option_type = 'CE', -toInt32(b.strike_price), toInt32(b.strike_price)) AS otm_rank,
                argMaxIf(b.close, b.timestamp, toHour(b.timestamp) * 60 + toMinute(b.timestamp) <= 570) AS entry_px,
                argMaxIf(b.close, b.timestamp, toHour(b.timestamp) * 60 + toMinute(b.timestamp) <= 929) AS exit_px,
                countIf(b.volume > 0) AS traded
         FROM stratify.options_1min AS b
         INNER JOIN ent ON ent.d = toDate(b.timestamp)
         INNER JOIN exp ON exp.d = toDate(b.timestamp) AND exp.expiry = b.expiry_date
         WHERE toDate(b.timestamp) >= '2024-01-01'
         GROUP BY trade_date, ot, strike, tgt, otm_rank
         HAVING traded > 0 ) AS o
  GROUP BY d, ot )

SELECT 'equivalence' AS check,
       if(round(sum(fast.entry_px - fast.exit_px), 2) = round(sum(slow.entry_px - slow.exit_px), 2),
          'PASS', 'FAIL') AS status,
       count() AS legs,
       round(sum(fast.entry_px - fast.exit_px), 2) AS fast_pnl_pts,
       round(sum(slow.entry_px - slow.exit_px), 2) AS slow_pnl_pts
FROM fast INNER JOIN slow ON slow.d = fast.d AND slow.ot = fast.ot
SETTINGS optimize_aggregation_in_order = 1,
         max_bytes_before_external_group_by = 800000000,
         join_algorithm = 'partial_merge';
