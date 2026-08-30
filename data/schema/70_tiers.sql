-- Tier enforcement at the storage layer.
--
-- One database serves both tiers. Measured on this box, an identical free-window backtest
-- costs 0.222 s against a one-year table and 0.226 s against the seven-year one -- a 4 ms
-- difference, because partition and primary-key pruning make the table's total size
-- almost irrelevant. Two databases would have bought 4 ms and cost a duplicated pipeline,
-- two sets of contract tests, and a standing risk of the copies drifting apart.
--
-- The tier boundary is therefore a DATE RANGE, and it is enforced twice:
--   1. In the spec validator, which refuses a period outside the caller's tier window.
--   2. Here, by a row policy, so a bug in (1) cannot widen what a free key can read.
-- Defence in depth matters specifically because the application check is the one most
-- likely to be edited by someone who does not know it is load-bearing.

CREATE USER IF NOT EXISTS stratify_free IDENTIFIED WITH no_password
  SETTINGS PROFILE 'demo_free';
CREATE USER IF NOT EXISTS stratify_paid IDENTIFIED WITH no_password
  SETTINGS PROFILE 'demo_free';

-- Free keys physically cannot read outside the free window, whatever the SQL asks for.
CREATE ROW POLICY IF NOT EXISTS free_window ON stratify.options_1min
  FOR SELECT USING timestamp >= '2025-07-01' AND timestamp < '2026-07-01'
  TO stratify_free;
CREATE ROW POLICY IF NOT EXISTS free_window_cd ON stratify.contract_day
  FOR SELECT USING trade_date >= '2025-07-01' AND trade_date < '2026-07-01'
  TO stratify_free;
CREATE ROW POLICY IF NOT EXISTS free_window_spot ON stratify.spot_1min
  FOR SELECT USING timestamp >= '2025-07-01' AND timestamp < '2026-07-01'
  TO stratify_free;
CREATE ROW POLICY IF NOT EXISTS free_window_hot ON stratify.hot_1min
  FOR SELECT USING timestamp >= '2025-07-01' AND timestamp < '2026-07-01'
  TO stratify_free;
-- A permissive policy for paid users on the same tables: without one, the restrictive
-- policy above would be the only policy and would apply to everyone.
CREATE ROW POLICY IF NOT EXISTS paid_all ON stratify.options_1min
  FOR SELECT USING 1 TO stratify_paid;
CREATE ROW POLICY IF NOT EXISTS paid_all_cd ON stratify.contract_day
  FOR SELECT USING 1 TO stratify_paid;
CREATE ROW POLICY IF NOT EXISTS paid_all_spot ON stratify.spot_1min
  FOR SELECT USING 1 TO stratify_paid;
CREATE ROW POLICY IF NOT EXISTS paid_all_hot ON stratify.hot_1min
  FOR SELECT USING 1 TO stratify_paid;

GRANT SELECT ON stratify.* TO stratify_free, stratify_paid;

-- COMPUTE ISOLATION. This is the part that separate databases were really being asked to
-- provide, and storage was never where it lived. `max_threads` is the lever: on an 8-core
-- box, capping a paid seven-year query at 3 threads means it cannot consume the machine
-- and starve free traffic, however heavy it is. Memory and row ceilings stop a runaway
-- query; execution time stops a stuck one.
CREATE SETTINGS PROFILE IF NOT EXISTS stratify_free_profile SETTINGS
  max_threads = 2,
  max_memory_usage = 2000000000,
  max_rows_to_read = 300000000,
  max_execution_time = 30,
  max_result_rows = 50000,
  -- readonly = 2, not 1: 1 forbids changing ANY setting, which blocks the query cache
  -- the engine asks for per query. 2 forbids writes and DDL while still allowing a
  -- read-only session to tune itself, which is exactly the privilege wanted here.
  readonly = 2;

CREATE SETTINGS PROFILE IF NOT EXISTS stratify_paid_profile SETTINGS
  max_threads = 3,
  max_memory_usage = 6000000000,
  max_rows_to_read = 3000000000,
  max_execution_time = 120,
  max_result_rows = 200000,
  readonly = 2;

ALTER USER stratify_free SETTINGS PROFILE 'stratify_free_profile';
ALTER USER stratify_paid SETTINGS PROFILE 'stratify_paid_profile';

-- Per-hour quotas at the database layer, underneath the application's own. The
-- application quota can be bypassed by a bug; this one cannot.
CREATE QUOTA IF NOT EXISTS stratify_free_quota
  FOR INTERVAL 1 HOUR MAX queries = 3000, execution_time = 900
  TO stratify_free;
CREATE QUOTA IF NOT EXISTS stratify_paid_quota
  FOR INTERVAL 1 HOUR MAX queries = 30000, execution_time = 9000
  TO stratify_paid;
