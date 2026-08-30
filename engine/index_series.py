"""Daily index candles for the report chart.

THE INDEX IS NOT THE ASSET. NIFTY OHLC is cheap, widely republished, and worth nothing to a
competitor -- while withholding it costs every per-trade visual there is, because you cannot
draw a trade without the instrument it was traded on. So this goes out in full and is not
metered against the price budget, which counts OPTION prints.

DAILY, NOT 1-MINUTE, and that is a size decision rather than a policy one: seven and a half
years is 1,857 daily bars and about 97 KB, against roughly 700,000 minute bars. A chart
cannot use the latter and a page cannot carry it.
"""
from . import db

MAX_BARS = 4000


def daily(date_from, date_to):
    """[{time, open, high, low, close}], oldest first, in the format the chart expects."""
    rows = db.rows(
        f"SELECT toDate(timestamp) d, argMin(open, timestamp) o, max(high) h, "
        f"min(low) l, argMax(close, timestamp) c "
        f"FROM {db.DATABASE}.spot_1min "
        f"WHERE timestamp >= %(a)s AND timestamp < addDays(toDate(%(b)s), 1) "
        f"GROUP BY d ORDER BY d LIMIT {MAX_BARS}",
        {"a": f"{date_from} 00:00:00", "b": str(date_to)})
    return [{"time": str(r["d"]), "open": float(r["o"]), "high": float(r["h"]),
             "low": float(r["l"]), "close": float(r["c"])} for r in rows]
