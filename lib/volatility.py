"""Volatility calculations for crypto assets.

Implements three realized-volatility models:
  - CC  : Close-to-Close realized volatility
  - PK  : Parkinson volatility (uses high/low range)
  - HAR : HAR-RV forecast (Heterogeneous Autoregressive model)

All volatilities are annualized and expressed as percentages.

Candlesticks come from Hyperliquid's candleSnapshot endpoint, which
returns hourly bars.  Each candle is a dict:
  {t, T, o, h, l, c, v}
with o/h/l/c as floats.
"""
import math

TRADING_DAYS = 365
HOURS_PER_DAY = 24
HOURS_PER_WEEK = 7 * HOURS_PER_DAY
HOURS_PER_MONTH = 30 * HOURS_PER_DAY


# --------------------------------------------------------------------------- #
#  Low-level helpers
# --------------------------------------------------------------------------- #
def _log_returns(prices):
    """ln(p_t / p_{t-1})  — one value fewer than the price list."""
    out = []
    for i in range(1, len(prices)):
        p0, p1 = prices[i - 1], prices[i]
        if p0 > 0 and p1 > 0:
            out.append(math.log(p1 / p0))
    return out


# --------------------------------------------------------------------------- #
#  Close-to-Close realized vol
# --------------------------------------------------------------------------- #
def cc_volatility(candles):
    """Annualised CC (close-to-close) volatility in percent.

    sigma_CC = sqrt(1/(N-1) * sum(r_t - r_bar)^2) * sqrt(365*24)
    """
    closes = [c["c"] for c in candles]
    r = _log_returns(closes)
    if len(r) < 2:
        return 0.0
    mean = sum(r) / len(r)
    var = sum((x - mean) ** 2 for x in r) / (len(r) - 1)
    return math.sqrt(var) * math.sqrt(TRADING_DAYS * HOURS_PER_DAY) * 100


# --------------------------------------------------------------------------- #
#  Parkinson volatility
# --------------------------------------------------------------------------- #
def parkinson_volatility(candles):
    """Annualised Parkinson volatility in percent.

    sigma_PK = 1/(2*sqrt(N)) * sqrt(sum(ln(H/L)^2))
               * sqrt(365*24)

    Uses the high/low range, which is a better estimator when prices
    are range-bound within each bar.
    """
    n = len(candles)
    if n < 2:
        return 0.0
    s = 0.0
    for c in candles:
        h, l = c["h"], c["l"]
        if h > 0 and l > 0 and h > l:
            s += math.log(h / l) ** 2
    if s == 0:
        return 0.0
    return math.sqrt(s / (4 * n)) * math.sqrt(TRADING_DAYS * HOURS_PER_DAY) * 100


# --------------------------------------------------------------------------- #
#  HAR-RV forecast  (Corsi 2009)
# --------------------------------------------------------------------------- #
def har_rv_forecast(candles):
    """One-step-ahead HAR-RV forecast in percent (annualised).

    Step 1 — compute hourly squared returns.
    Step 2 — aggregate into *daily* realised variance:
             RV_daily[i] = sum of hourly r^2 within calendar day i
    Step 3 — compute longer-horizon averages:
             RV_weekly   = mean of last 7  daily RVs
             RV_monthly  = mean of last 30 daily RVs
    Step 4 — forecast next-day daily RV:
             RV_hat = 1/6 * RV_daily + 2/6 * RV_weekly + 3/6 * RV_monthly
             (standard HAR weighting, sums to 1)
    Step 5 — annualise:  sigma = sqrt(RV_hat * 24) * sqrt(365)   (since
             RV_hat is hourly-var averaged over a day → multiply by 24
             to get daily variance, then sqrt and annualise)

    Falls back to CC vol if too few candles.
    """
    r = _log_returns([c["c"] for c in candles])
    if len(r) < HOURS_PER_DAY:  # need at least one full day
        return cc_volatility(candles)

    sq = [x ** 2 for x in r]
    n = len(sq)

    # --- daily RVs --------------------------------------------------------- #
    # Each daily RV is the sum of 24 hourly squared returns.
    daily_rvs = []
    for i in range(n - HOURS_PER_DAY, -1, -HOURS_PER_DAY):
        day_slice = sq[i:i + HOURS_PER_DAY]
        if len(day_slice) == HOURS_PER_DAY:
            daily_rvs.append(sum(day_slice))
    if not daily_rvs:
        return cc_volatility(candles)

    # --- HAR forecast ------------------------------------------------------ #
    rv_daily = daily_rvs[0]                    # most recent full day's variance
    rv_weekly = sum(daily_rvs[:7]) / min(7, len(daily_rvs))   # 7-day avg
    rv_monthly = sum(daily_rvs[:30]) / min(30, len(daily_rvs))  # 30-day avg

    forecast = (1 / 6) * rv_daily + (2 / 6) * rv_weekly + (3 / 6) * rv_monthly
    if forecast <= 0:
        return cc_volatility(candles)

    # forecast is already a daily variance (sum of 24 hourly squared returns)
    # daily vol = sqrt(daily_var); annualised = daily_vol * sqrt(365)
    daily_vol = math.sqrt(forecast)
    annualised = daily_vol * math.sqrt(TRADING_DAYS) * 100
    return annualised
