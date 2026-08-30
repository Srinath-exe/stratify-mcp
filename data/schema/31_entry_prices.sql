-- Bucket-aware entry prices on contract_day.
--
-- Decision B4 prices an entry from the last REAL print in the 15-minute bucket ending at
-- the entry time, and requires the strike to have traded in that bucket. Computing that
-- from options_1min meant scanning every bar of every expiry in play -- 41.6 M rows for a
-- one-year backtest, and the dominant cost once exit detection moved into SQL.
--
-- These columns answer the same question from a 232 K-row table. `ent_HHMM` is the last
-- traded close in (T-14 .. T]; 0 means the strike did not trade in that bucket, which is
-- exactly the condition that must exclude it.
--
-- px_HHMM (already present) is a different thing and stays: last close at or before T,
-- traded or not. Keeping both, named differently, is deliberate -- collapsing them is how
-- "entry price" quietly comes to mean two things.
ALTER TABLE {DB}.contract_day
  ADD COLUMN IF NOT EXISTS ent_0915 Float32, ADD COLUMN IF NOT EXISTS ent_0930 Float32,
  ADD COLUMN IF NOT EXISTS ent_1100 Float32, ADD COLUMN IF NOT EXISTS ent_1200 Float32,
  ADD COLUMN IF NOT EXISTS ent_1230 Float32, ADD COLUMN IF NOT EXISTS ent_1300 Float32,
  ADD COLUMN IF NOT EXISTS ent_1400 Float32, ADD COLUMN IF NOT EXISTS ent_1500 Float32,
  ADD COLUMN IF NOT EXISTS ent_1529 Float32;
