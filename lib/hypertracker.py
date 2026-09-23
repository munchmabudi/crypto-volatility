"""HyperTracker API client — liquidation heatmaps, positions, whale wallets.

Requires an API key set via the HYPERTRACKER_API_KEY env var.

Key endpoints:
  - /api/external/exports/coins/{coin}/liquidation-heatmap  (302 → pre-signed JSON)
  - /api/external/positions/open/coin/{coin}                 (302 → pre-signed CSV/JSON)
  - /api/external/trader/{address}                           (wallet-level data)
"""
import json
import os
import urllib.request
import urllib.error


BASE_URL = "https://ht-api.coinmarketman.com/api/external"


class HyperTrackerError(Exception):
    pass


def _get(path, token=None, timeout=60):
    """GET request, following the 302 redirect that export endpoints issue."""
    key = token or os.environ.get("HYPERTRACKER_API_KEY", "")
    url = f"{BASE_URL}/{path}"
    headers = {
        "Accept": "application/json",
        "User-Agent": "crypto-viz/1.0",
    }
    if key:
        headers["Authorization"] = f"Bearer {key}"

    # The export endpoints return 302 to a pre-signed S3/Cloud URL.
    # urllib follows redirects by default, so a single urlopen suffices.
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        body = resp.read()
        return json.loads(body)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise HyperTrackerError(f"Auth failed ({e.code}). Set HYPERTRACKER_API_KEY env var.")
        raise


def get_liquidation_heatmap(coin, token=None):
    """Download the liquidation heatmap for a single coin.

    Returns a list of dicts with keys:
      priceBinStart, priceBinEnd, liquidationValue, positionsCount,
      mostImpactedSegment
    """
    result = _get(f"exports/coins/{coin}/liquidation-heatmap", token=token)
    if isinstance(result, dict) and "heatmap" in result:
        return result["heatmap"]
    return []


def get_open_positions(coin, token=None):
    """Download all open perp positions for a coin.

    Returns a list of position dicts (parsed from CSV or JSON depending
    on the Accept header).  Each has:
      address, coin, side, size, value, entryPrice, liquidationPrice,
      crossLeverage, liquidationProgress, unrealizedPnl, funding
    """
    result = _get(f"positions/open/coin/{coin}", token=token)
    if isinstance(result, list):
        return result
    if isinstance(result, dict) and "positions" in result:
        return result["positions"]
    return []


def get_wallet_info(address, token=None):
    """Fetch wallet-level info from HyperTracker.

    Returns equity, perp positions, PnL, funding, etc.
    """
    try:
        result = _get(f"trader/{address}", token=token)
        if result and "error" not in result:
            return result
    except Exception:
        pass
    return {}
