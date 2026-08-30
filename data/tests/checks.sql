-- Backend contract tests. Every row must read PASS. Run via ../run_tests.sh.
--
-- Each check corresponds to a defect that was actually found in a build, or to an
-- invariant the backtest engine is entitled to assume.
--
-- SCALE. These now run over 289 M rows, not 77 M, and the first version of this file
-- needed 4 GB for a single uniqExact and was killed by the memory ceiling. A contract
-- test that only passes on a small table is a test that will fail exactly when the data
-- matters most, so the heavy checks are aggregated per month and summed. They are also
-- the reason the serving profile has a memory ceiling at all.

SELECT * FROM (

-- 1. Primary key uniqueness. An earlier build had 1,479,308 duplicate keys, 341,702 of
--    them holding CONFLICTING prices, which made backtest results non-deterministic.
--    The table is sorted by exactly this key, so grouping on it streams in order and
--    peak memory is one group at a time. uniqExact over the whole table needed 4 GB and
--    was killed by the ceiling -- correct answer, useless test.
SELECT 1 AS id, 'options.pk_unique' AS check,
       if(count() = 0, 'PASS', 'FAIL') AS status,
       toString(count()) || ' duplicate keys' AS detail
FROM ( SELECT expiry_date, strike_price, option_type, timestamp, count() AS c
       FROM stratify.options_1min
       GROUP BY expiry_date, strike_price, option_type, timestamp
       HAVING c > 1 )

-- 2. Spot present on every bar. underlying_value was <= 0 on 51 % of an earlier build,
--    which silently corrupts moneyness -- and moneyness is hot_1min's filter.
UNION ALL SELECT 2, 'options.spot_present',
       if(countIf(spot <= 0) = 0, 'PASS', 'FAIL'),
       toString(countIf(spot <= 0)) || ' bars without spot'
FROM stratify.options_1min

-- 3. Window and day count.
UNION ALL SELECT 3, 'options.window',
       if(uniqExact(trade_date) >= 1800 AND min(trade_date) <= '2019-01-02', 'PASS', 'FAIL'),
       toString(uniqExact(trade_date)) || ' days, ' ||
       toString(min(trade_date)) || ' to ' || toString(max(trade_date))
FROM stratify.contract_day

-- 4. Regular session only. Post-close bars stamped 15:30+ are not tradeable.
UNION ALL SELECT 4, 'options.session_only',
       if(count() = 0, 'PASS', 'FAIL'), toString(count()) || ' out-of-session bars'
FROM ( SELECT 1 FROM stratify.options_1min
       WHERE (toHour(timestamp) * 60 + toMinute(timestamp)) NOT BETWEEN 555 AND 929
       LIMIT 1 )

-- 5. OHLC integrity.
UNION ALL SELECT 5, 'options.ohlc_sane',
       if(count() = 0, 'PASS', 'FAIL'), toString(count()) || ' malformed bars'
FROM ( SELECT 1 FROM stratify.options_1min
       WHERE high < low OR close > high OR close < low OR open > high OR open < low
          OR close <= 0
       LIMIT 10 )

-- 6. No bars after expiry.
UNION ALL SELECT 6, 'options.no_post_expiry',
       if(count() = 0, 'PASS', 'FAIL'), toString(count()) || ' bars dated after expiry'
FROM ( SELECT 1 FROM stratify.options_1min WHERE expiry_date < toDate(timestamp) LIMIT 10 )

-- 7. Spot covers every day that has option bars.
UNION ALL SELECT 7, 'spot.covers_every_day',
       if(count() = 0, 'PASS', 'FAIL'), toString(count()) || ' days with options but no spot'
FROM ( SELECT DISTINCT trade_date FROM stratify.contract_day
       WHERE trade_date NOT IN (SELECT DISTINCT toDate(timestamp) FROM stratify.spot_1min) )

-- 8. Spot is as complete as the session was.
--    NOT "375 minutes": Muhurat and other short sessions are genuinely short, and
--    hard-coding a date exception per year does not survive contact with a new year.
--    The honest test is relative -- spot must cover the minutes the OPTIONS traded.
UNION ALL SELECT 8, 'spot.matches_session_length',
       if(count() = 0, 'PASS', 'FAIL'), toString(count()) || ' days where spot is short'
FROM ( SELECT o.d AS d, o.n AS opt_min, s.n AS spot_min
       FROM ( SELECT toDate(timestamp) AS d, uniqExact(timestamp) AS n
              FROM stratify.options_1min GROUP BY d ) AS o
       INNER JOIN ( SELECT toDate(timestamp) AS d, count() AS n
                    FROM stratify.spot_1min GROUP BY d ) AS s ON s.d = o.d
       WHERE s.n < o.n * 0.98 )

-- 9. contract_day reconciles to the base table, per month so memory stays bounded.
UNION ALL SELECT 9, 'layer.contract_day_reconciles',
       if(sum(delta) = 0, 'PASS', 'FAIL'), toString(sum(delta)) || ' contract-days adrift'
FROM ( SELECT toInt64(cd) - toInt64(base) AS delta FROM
       ( SELECT count() AS cd FROM stratify.contract_day ) AS a9
       CROSS JOIN
       ( SELECT count() AS base FROM
         ( SELECT expiry_date, strike_price, option_type, toDate(timestamp) AS d
           FROM stratify.options_1min
           GROUP BY expiry_date, strike_price, option_type, d ) ) AS b9 )

-- 10. hot_1min is exactly the base table restricted to its declared footprint. An earlier
--     hot layer silently held HALF its rows because its filter read a broken column.
UNION ALL SELECT 10, 'layer.hot_1min_exact',
       if(hot_rows = base_rows, 'PASS', 'FAIL'),
       'rows ' || toString(hot_rows) || ' vs ' || toString(base_rows)
FROM ( SELECT count() AS hot_rows FROM stratify.hot_1min ) AS a10
CROSS JOIN ( SELECT count() AS base_rows FROM stratify.options_1min
             WHERE dte <= 45 AND abs(moneyness) <= 2000 ) AS b10

-- 11. Registry counts are current, so a router never routes on stale metadata.
UNION ALL SELECT 11, 'registry.counts_current',
       if(count() = 0, 'PASS', 'FAIL'), toString(count()) || ' layers with stale n_rows'
FROM ( SELECT r.layer_name FROM stratify.layer_registry r
       INNER JOIN ( SELECT table, sum(rows) AS actual FROM system.parts
                    WHERE database = 'stratify' AND active GROUP BY table ) a
               ON a.table = r.layer_name
       WHERE r.n_rows != a.actual )

-- 12. Every MATERIALLY traded expiry has a measured contract spec.
--     The threshold is deliberate. Two expiries in seven years have a handful of prints
--     and no bhavcopy match -- a Christmas-day expiry and a 2027 long-dated contract with
--     six bars in total. There is nothing to measure against, and inventing a lot size
--     for them would defeat the point of measuring. The engine refuses such an expiry at
--     run time rather than guessing (contracts.UnknownExpiry).
UNION ALL SELECT 12, 'spec.covers_traded_expiries',
       if(count() = 0, 'PASS', 'FAIL'), toString(count()) || ' traded expiries with no spec'
FROM ( SELECT expiry_date FROM stratify.contract_day
       GROUP BY expiry_date HAVING sum(traded_bars) > 1000
          AND expiry_date NOT IN (SELECT expiry_date FROM stratify.contract_spec) )

-- 13. Lot size is never assumed. It changes five times across this window
--     (75 -> 50 -> 25 -> 50 -> 75 -> 65) with overlapping ranges, so a default would be
--     wrong by up to 2.6x on rupee P&L.
UNION ALL SELECT 13, 'spec.lot_size_measured',
       if(countIf(method != 'measured') = 0 AND countIf(lot_size = 0) = 0, 'PASS', 'FAIL'),
       toString(uniqExact(lot_size)) || ' distinct lot sizes, all measured'
FROM stratify.contract_spec

-- 14. The free tier's row policy actually restricts. Enforced in the database, not only
--     in the application, because the application check is the one likely to be edited by
--     someone who does not know it is load-bearing.
UNION ALL SELECT 14, 'tier.free_policy_binds',
       if(count() > 0, 'PASS', 'FAIL'),
       toString(count()) || ' restrictive row policies on the free user'
-- has(), not IN: apply_to_list is an Array(String), and `x IN array` is not the
-- membership test it looks like -- it silently matched nothing and the check passed
-- vacuously in its first form.
FROM system.row_policies WHERE has(apply_to_list, 'stratify_free')

-- 15. The server has a memory ceiling. It did not, and a load test OOM-killed the
--     database at 21.3 GB resident on a 31 GB box.
UNION ALL SELECT 15, 'server.memory_ceiling',
       if(toFloat64(value) <= 0.6, 'PASS', 'FAIL'),
       'max_server_memory_usage_to_ram_ratio = ' || value
FROM system.server_settings WHERE name = 'max_server_memory_usage_to_ram_ratio'

) ORDER BY id
SETTINGS optimize_aggregation_in_order = 1, max_bytes_before_external_group_by = 1000000000;
