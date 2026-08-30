-- Coverage router. A request is served by the FIRST layer whose declared coverage is a
-- superset of what the request needs. Registry-driven so a layer can never be used
-- outside its footprint: the earlier design served an ATM+/-2500 request from a +/-2000
-- layer and silently returned 7,472 of 8,139 contract-days -- 8.2 % missing, no error.

DROP TABLE IF EXISTS stratify.layer_registry;
CREATE TABLE stratify.layer_registry
( layer_name String, priority UInt8, resolution String,
  max_dte UInt16, max_abs_moneyness UInt32, allowed_minutes Array(UInt16),
  n_rows UInt64, disk_mb Float32, typical_cpu_sec Float32 )
ENGINE = TinyLog;

-- typical_cpu_sec is MEASURED, on one reference workload: a full-year (246-day) short
-- strangle, 1.5 % OTM both legs, entry and exit at a fixed time, weekly expiry.
-- All three layers return identical P&L on it (tests/equivalence.sql).
--
--   contract_day   0.074 CPU-s ·   66 ms ·   422 K rows ·   6 MB peak
--   hot_1min       2.134 CPU-s · 1206 ms ·  16.0 M rows · 900 MB peak
--   options_1min   5.032 CPU-s · 2572 ms ·  77.1 M rows · 3.55 GB peak
--
-- 68x CPU between the ends, and 600x memory -- which is what actually bounds concurrency.
INSERT INTO stratify.layer_registry VALUES
 ('contract_day',  1, 'fixed-times', 65535, 4294967295,
  [555,570,660,720,750,780,840,900,929],       232172,  13.80, 0.0737),
 ('hot_1min',      2, '1min',           45,       2000, [],  52196937, 914.88, 2.1337),
 ('options_1min',  3, '1min',        65535, 4294967295, [],  76894449, 997.48, 5.0324);
