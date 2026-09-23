"""Hyperliquid API client — free, public endpoints.

Provides:
  - Perps metadata + asset contexts (prices, OI, volume, funding)
  - Hourly candle snapshots for volatility calculation
  - All mids for cross-reference pricing
"""
import json
import urllib.request
import time

BASE_URL = "https://api.hyperliquid.xyz"


def _post(payload, timeout=30):
    """Make a POST request to Hyperliquid's info endpoint."""
    url = f"{BASE_URL}/info"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "User-Agent": "crypto-viz/1.0"},
    )
    resp = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(resp.read())


def get_perps():
    """Get perp universe + asset contexts.

    Returns list of dicts for each perp with:
      name, szDecimals, maxLeverage, markPx, midPx, openInterest,
      dayNtlVlm, funding, prevDayPx, premium, impactPxs
    """
    result = _post({"type": "metaAndAssetCtxs", "dex": ""})
    universe = result[0]["universe"]
    contexts = result[1] if len(result) > 1 else []
    perps = []
    for u, ctx in zip(universe, contexts):
        perps.append({
            "name": u["name"],
            "szDecimals": u.get("szDecimals", 0),
            "maxLeverage": u.get("maxLeverage", 0),
            "markPx": float(ctx.get("markPx", 0)),
            "midPx": float(ctx.get("midPx", 0)) if ctx.get("midPx") else 0,
            "openInterest": float(ctx.get("openInterest", 0)),
            "dayNtlVlm": float(ctx.get("dayNtlVlm", 0)),
            "dayBaseVlm": float(ctx.get("dayBaseVlm", 0)),
            "funding": float(ctx.get("funding", 0)),
            "prevDayPx": float(ctx.get("prevDayPx", 0)),
            "premium": float(ctx.get("premium", 0)) if ctx.get("premium") else 0,
            "impactPxs": [float(x) for x in (ctx.get("impactPxs") or []) if x],
        })
    return perps


def get_all_mids():
    """Get mark prices for all coins."""
    return _post({"type": "allMids"})


def get_candles(coin, interval="1h", lookback_hours=192):
    """Get historical hourly candles for volatility calculation.

    Returns list of dicts: {t, T, o, h, l, c, v}
    """
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - lookback_hours * 3600000  # hours → ms
    payload = {
        "type": "candleSnapshot",
        "req": {"coin": coin, "interval": interval, "startTime": start_ms, "endTime": end_ms}
    }
    candles = _post(payload)
    result = []
    for c in candles:
        result.append({
            "t": c["t"],
            "T": c["T"],
            "o": float(c["o"]),
            "h": float(c["h"]),
            "l": float(c["l"]),
            "c": float(c["c"]),
            "v": float(c["v"]),
        })
    return result
