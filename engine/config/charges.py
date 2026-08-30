"""Indian F&O transaction costs. Rates verified against Zerodha's published charges, 2026.

This is a direction-aware port of BACKTEST/weekly_options_research/charges.py. The rates
are identical and are the model of record (decision B1). Two things changed:

1. DIRECTION. The original assumes entry is a sell and exit is a buy, which is right for
   every credit structure but WRONG for `long_option`, which ships in v1 (decision A4).
   STT is sell-side only and stamp duty is buy-side only, so applying the credit-structure
   formula to a debit trade charges STT on the buy and omits it on the sell. On a 100-point
   long option at lot 65 that misplaces about Rs 9.75 of STT per leg per side.
   `round_trip()` therefore takes the direction of each leg.

2. PER-LEG TURNOVER. Brokerage is per order, so it already scaled with n_legs; the
   percentage charges are on turnover and are unchanged by how the premium is split.
   Both forms agree exactly -- test_charges.py asserts equality against the original
   function on credit structures, so this port cannot silently drift from production.

KNOWN GAP, carried forward and surfaced in every response: a short leg finishing deep ITM
at expiry is assigned, which carries STT on INTRINSIC value rather than the buy-back cost
modelled here. Not modelled. Rare for the OTM-seller structures, real nonetheless.
"""
from dataclasses import dataclass

BROKERAGE_PER_ORDER = 20.0
STT_RATE = 0.0015                                        # sell side only
EXCHANGE_TXN_RATE = {"NSE": 0.0003553, "BSE": 0.000325}  # both sides
SEBI_RATE = 10.0 / 1e7                                   # Rs 10 per crore, both sides
GST_RATE = 0.18                                          # on brokerage + exchange + SEBI
STAMP_DUTY_RATE = 0.00003                                # buy side only
WORTHLESS_THRESHOLD_PTS = 0.05  # below this the position expired; no exit transaction

SYMBOL_EXCHANGE = {"NIFTY": "NSE", "BANKNIFTY": "NSE", "FINNIFTY": "NSE",
                   "MIDCPNIFTY": "NSE", "SENSEX": "BSE", "BANKEX": "BSE"}

DISCLOSED_GAPS = (
    "Deep-ITM assignment at expiry carries STT on intrinsic value and is not modelled.",
)


@dataclass(frozen=True)
class ChargeBreakdown:
    brokerage: float = 0.0
    stt: float = 0.0
    exchange: float = 0.0
    sebi: float = 0.0
    stamp_duty: float = 0.0
    gst: float = 0.0

    @property
    def total(self):
        return (self.brokerage + self.stt + self.exchange
                + self.sebi + self.stamp_duty + self.gst)

    def __add__(self, other):
        return ChargeBreakdown(
            self.brokerage + other.brokerage, self.stt + other.stt,
            self.exchange + other.exchange, self.sebi + other.sebi,
            self.stamp_duty + other.stamp_duty, self.gst + other.gst)


def _side(premium_pts, qty, n_orders, exchange, is_sell):
    """Charges for one side of one transaction. `premium_pts` is combined across legs."""
    turnover = max(0.0, premium_pts) * qty
    brokerage = BROKERAGE_PER_ORDER * n_orders
    exch = turnover * EXCHANGE_TXN_RATE[exchange]
    sebi = turnover * SEBI_RATE
    return ChargeBreakdown(
        brokerage=brokerage,
        stt=turnover * STT_RATE if is_sell else 0.0,
        exchange=exch,
        sebi=sebi,
        stamp_duty=0.0 if is_sell else turnover * STAMP_DUTY_RATE,
        gst=GST_RATE * (brokerage + exch + sebi))


def round_trip(entry_premium_pts, exit_premium_pts, lot_size, lots, symbol,
               n_legs=2, entry_is_sell=True):
    """Round-trip charges for an n-leg position, as a ChargeBreakdown.

    entry_premium_pts / exit_premium_pts are combined across legs, in index points.
    entry_is_sell=True for credit structures (strangle, condor, fly, credit spread),
    False for debit structures (long_option, debit spread).

    If the exit premium is at or below WORTHLESS_THRESHOLD_PTS the position expired
    worthless and no exit transaction occurred, so no exit charges apply.
    """
    if lots <= 0:
        return ChargeBreakdown()
    exchange = SYMBOL_EXCHANGE.get(symbol, "NSE")
    qty = lot_size * lots

    charges = _side(entry_premium_pts, qty, n_legs, exchange, is_sell=entry_is_sell)
    if exit_premium_pts > WORTHLESS_THRESHOLD_PTS:
        charges = charges + _side(exit_premium_pts, qty, n_legs, exchange,
                                  is_sell=not entry_is_sell)
    return charges
