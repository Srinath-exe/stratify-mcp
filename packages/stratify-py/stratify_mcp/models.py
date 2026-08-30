"""Result wrapper. run_backtest and get_backtest return the same payload shape
(server/tools.py's get_backtest builds it by re-loading exactly what run_backtest stored),
so one class serves both.
"""


class BacktestResult:
    """Wraps a run_backtest/get_backtest response. Every field the server sent is on
    `.raw`; the properties below are conveniences over the common ones, built lazily so a
    caller who only wants `.summary` never pays for a DataFrame construction they didn't
    ask for.

    `detail="summary"` responses have no `trades`/`equity_curve` at all (server/tools.py's
    _trim() drops them to keep that request cheap and non-exposing) -- .trades and
    .equity_curve return an empty DataFrame in that case rather than raising, since "there
    is no per-trade data in a summary response" is an expected, not exceptional, state.
    """

    def __init__(self, payload):
        self.raw = payload
        self._trades_df = None
        self._equity_df = None

    @property
    def summary(self):
        """Aggregate metrics: total_pnl_rupees, mean_return_on_margin, win_rate,
        max_drawdown_rupees, avg_margin_points, charges_share_of_gross, and
        ratios.{sharpe,profit_factor,calmar} -- see explain_methodology('honesty') for
        what each does and does not prove. No ratios at all below 30 trades (the sample
        floor); check .honesty for why a number is or isn't here."""
        return self.raw.get("summary", {})

    @property
    def honesty(self):
        """The out-of-sample split, walk-forward folds, bootstrap interval and deflated
        Sharpe -- read this before trusting `.summary` at all."""
        return self.raw.get("honesty", {})

    @property
    def interpretation(self):
        """Plain-language reading of this specific result, and what it does not support."""
        return self.raw.get("interpretation")

    @property
    def trades(self):
        """pandas.DataFrame, one row per trade. Requires pandas (a hard dependency of this
        package, not optional -- see pyproject.toml)."""
        if self._trades_df is None:
            import pandas as pd
            self._trades_df = pd.DataFrame(self.raw.get("trades") or [])
        return self._trades_df

    @property
    def equity_curve(self):
        """pandas.DataFrame, one row per trade in exit order -- cumulative net P&L and the
        gap to the running peak. See .raw['equity_curve']['note'] for the exact column
        semantics the server documents."""
        if self._equity_df is None:
            import pandas as pd
            curve = self.raw.get("equity_curve") or {}
            rows = curve.get("rows") or []
            columns = curve.get("columns")
            self._equity_df = pd.DataFrame(rows, columns=columns) if columns else pd.DataFrame(rows)
        return self._equity_df

    @property
    def qualified(self):
        """True if this result was strong enough (out-of-sample and walk-forward, not
        just P&L) to be saved into this account's strategy book. See .why_not_qualified
        when False."""
        return (self.raw.get("strategy_book") or {}).get("qualified", False)

    @property
    def why_not_qualified(self):
        """None when .qualified is True; otherwise the server's explanation of which
        check(s) failed."""
        book = self.raw.get("strategy_book") or {}
        return None if book.get("qualified") else book.get("note")

    @property
    def backtest_id(self):
        return self.raw.get("backtest_id")

    @property
    def report_url(self):
        """A shareable, human-readable report page -- the full equity curve as a chart,
        monthly bars, and every trade regardless of what `detail` was requested here."""
        return self.raw.get("report_url")

    @property
    def warnings(self):
        """Disclosed modeling gaps and approximations -- e.g. a naked-margin ratio
        calibrated today and applied to the past. Always worth reading, never worth
        hiding behind a flag."""
        return self.raw.get("warnings", [])

    def to_dict(self):
        """The complete, unwrapped server payload."""
        return self.raw

    def __repr__(self):
        # Field names verified against a real run_backtest response, not assumed: there is
        # no "cagr" -- the summary's actual return metrics are total_pnl_rupees,
        # mean_return_on_margin, and ratios.sharpe/profit_factor/calmar.
        s = self.summary
        n = s.get("n_trades")
        pnl = s.get("total_pnl_rupees")
        pnl_txt = f"pnl={pnl:+,.0f}Rs" if isinstance(pnl, (int, float)) else "pnl=n/a"
        n_txt = f"n_trades={n}" if n is not None else "insufficient_sample"
        return f"<BacktestResult {self.backtest_id or '(unsaved)'} {pnl_txt} {n_txt}>"
