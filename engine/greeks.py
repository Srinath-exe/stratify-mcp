"""Black-Scholes, used for one job: naming a strike by its delta.

WHY IT IS HERE AT ALL. Every price on this platform is a real traded print; nothing is
modelled. But "sell the 20-delta call" is how a large part of the options world actually
specifies a strike, and delta is not in the data -- it is a function of the price that IS.
So the model runs backwards: solve the implied volatility that reproduces the observed
mark, then read the delta off it. Nothing is priced by the model; it is only used to
translate a strike-selection rule into a strike.

r = 0 AND q = 0. Indian index options are cash-settled with no dividend leg, and the
carry over a weekly horizon moves delta in the fourth decimal. Introducing a rate would
add a parameter nobody can verify to a number used only for ranking strikes.
"""
import math

MIN_VOL, MAX_VOL = 0.005, 5.0
BISECTION_STEPS = 48
MIN_YEARS = 1.0 / (365.0 * 24.0 * 60.0)      # one minute, so T is never zero


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def price(kind, spot, strike, years, vol):
    if years <= 0 or vol <= 0:
        return max(0.0, spot - strike) if kind == "CE" else max(0.0, strike - spot)
    d1 = (math.log(spot / strike) + 0.5 * vol * vol * years) / (vol * math.sqrt(years))
    d2 = d1 - vol * math.sqrt(years)
    if kind == "CE":
        return spot * _norm_cdf(d1) - strike * _norm_cdf(d2)
    return strike * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def implied_vol(kind, spot, strike, years, mark):
    """Bisection, not Newton. The price is monotone in vol, so bisection cannot diverge --
    and a strike-selection helper that occasionally returns nonsense would pick a strike
    nobody asked for, silently, in one cycle out of a hundred."""
    years = max(years, MIN_YEARS)
    intrinsic = max(0.0, spot - strike) if kind == "CE" else max(0.0, strike - spot)
    if mark <= intrinsic + 1e-9:
        return None                     # no time value: delta is 0 or 1, nothing to solve
    lo, hi = MIN_VOL, MAX_VOL
    if price(kind, spot, strike, years, hi) < mark:
        return None                     # beyond the model's range; refuse rather than clamp
    for _ in range(BISECTION_STEPS):
        mid = (lo + hi) / 2
        if price(kind, spot, strike, years, mid) < mark:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def delta(kind, spot, strike, years, mark):
    """Signed for a call, unsigned magnitude for a put -- returned as a MAGNITUDE either
    way, because "the 20-delta put" always means 0.20, never -0.20."""
    years = max(years, MIN_YEARS)
    vol = implied_vol(kind, spot, strike, years, mark)
    if vol is None:
        intrinsic_itm = (spot > strike) if kind == "CE" else (strike > spot)
        return 1.0 if intrinsic_itm else 0.0
    d1 = (math.log(spot / strike) + 0.5 * vol * vol * years) / (vol * math.sqrt(years))
    return _norm_cdf(d1) if kind == "CE" else _norm_cdf(-d1)
