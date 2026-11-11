-- Tier enforcement at the storage layer, for a PER-SYMBOL serving database.
-- __DB__ is substituted by build_symbol.sh; __FREE_FROM__ / __FREE_TO__ likewise.
--
-- Same two-layer shape as 70_tiers.sql and for the same reason: the spec validator
-- refuses a period outside the caller's tier window, and these policies make it
-- physically impossible for a free key to read outside it even if that check is wrong.
-- A new serving database WITHOUT these policies is the dangerous case -- not because a
-- free key would be refused, but because it would be granted everything: a restrictive
-- policy that does not exist cannot restrict, so stratify_free would read the full
-- history of the new symbol while being correctly refused on the old one.

CREATE ROW POLICY IF NOT EXISTS free_window ON __DB__.options_1min
  FOR SELECT USING timestamp >= '__FREE_FROM__' AND timestamp < '__FREE_TO__'
  TO stratify_free;
CREATE ROW POLICY IF NOT EXISTS free_window_cd ON __DB__.contract_day
  FOR SELECT USING trade_date >= '__FREE_FROM__' AND trade_date < '__FREE_TO__'
  TO stratify_free;
CREATE ROW POLICY IF NOT EXISTS free_window_spot ON __DB__.spot_1min
  FOR SELECT USING timestamp >= '__FREE_FROM__' AND timestamp < '__FREE_TO__'
  TO stratify_free;
CREATE ROW POLICY IF NOT EXISTS free_window_hot ON __DB__.hot_1min
  FOR SELECT USING timestamp >= '__FREE_FROM__' AND timestamp < '__FREE_TO__'
  TO stratify_free;

-- Permissive counterparts for the paid identity. Without one, the restrictive policy
-- above would be the ONLY policy on the table and would therefore apply to everybody.
CREATE ROW POLICY IF NOT EXISTS paid_all ON __DB__.options_1min
  FOR SELECT USING 1 TO stratify_paid;
CREATE ROW POLICY IF NOT EXISTS paid_all_cd ON __DB__.contract_day
  FOR SELECT USING 1 TO stratify_paid;
CREATE ROW POLICY IF NOT EXISTS paid_all_spot ON __DB__.spot_1min
  FOR SELECT USING 1 TO stratify_paid;
CREATE ROW POLICY IF NOT EXISTS paid_all_hot ON __DB__.hot_1min
  FOR SELECT USING 1 TO stratify_paid;

-- Tables with no free/paid split: the reference data a backtest needs in order to be
-- correct at all. contract_spec carries the lot size, and a free key that cannot read it
-- gets no rupee P&L rather than a restricted one.
GRANT SELECT ON __DB__.* TO stratify_free;
GRANT SELECT ON __DB__.* TO stratify_paid;
