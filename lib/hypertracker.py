"""HyperTracker API client — liquidation heatmaps, positions, whale wallets.

Requires an API key set via the HYPERTRACKER_API_KEY env var.

Key endpoints:
  - /api/external/exports/coins/{coin}/liquidation-heatmap  (302 → pre-signed JSON or CSV)
  - /api/external/positions/open/coin/{coin}                 (302 → pre-signed JSON or CSV)
  - /api/external/trader/{address}                           (wallet-level data)

Some endpoints issue a 302 redirect to a pre-signed S3/Cloud URL.  Those URLs
serve CSV by default (the Accept header is not always honoured on S3).  We
handle both JSON and CSV transparently.
"""
import csv
import io
import json
import os
import urllib.request
import urllib.error


BASE_URL = "https://ht-api.coinmarketman.com/api/external"


class HyperTrackerError(Exception):
    pass


# --------------------------------------------------------------------------- #
#  Low-level HTTP
# --------------------------------------------------------------------------- #
def _get_raw(path, token=None, timeout=120):
    """GET request to HyperTracker, following the 302 to a pre-signed URL.

    Returns a tuple ``(data, resp)`` where *data* is one of:
      - parsed JSON (dict or list)
      - raw text string (if JSON parsing failed — caller may need CSV parse)
      - bytes (fallback)
    """
    key = token or os.environ.get("HYPERTRACKER_API_KEY", "")
    url = f"{BASE_URL}/{path}"
    headers = {
        "Accept": "application/json",
        "User-Agent": "crypto-viz/1.0",
    }
    if key:
        headers["Authorization"] = f"Bearer {key}"

    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        body = resp.read()
        try:
            return json.loads(body), resp
        except (json.JSONDecodeError, ValueError):
            # Not JSON — might be CSV text
            return body, resp
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        if e.code in (401, 403):
            raise HyperTrackerError(f"Auth failed ({e.code}) for {path}. Body: {err_body}")
        raise HyperTrackerError(f"HTTP {e.code} for {path}. Body: {err_body}")


def _parse_body(body, token=None, path=""):
    """Fetch and return parsed data, trying JSON then CSV.

    If *body* is bytes that aren't JSON, attempts CSV parsing.
    Returns the parsed result or raises HyperTrackerError on genuine errors.
    """
    if isinstance(body, (dict, list)):
        return body
    if isinstance(body, bytes):
        text = body.decode("utf-8", errors="replace")
    else:
        text = str(body)

    # Try JSON
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    # Try CSV
    try:
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        if rows and len(rows) > 0:
            return rows
    except Exception:
        pass

    # Couldn't parse — return raw text for diagnostics
    return text


# --------------------------------------------------------------------------- #
#  Heatmap
# --------------------------------------------------------------------------- #
def get_liquidation_heatmap(coin, token=None):
    """Download the liquidation heatmap for a single coin.

    Handles both JSON and CSV response formats (pre-signed URLs may serve
    either depending on the S3 bucket configuration).

    Returns a list of dicts.  Each dict has at least:
      priceBinStart, priceBinEnd, liquidationValue, positionsCount
    """
    data = _fetch(coin, "exports/coins/{coin}/liquidation-heatmap", token)
    if isinstance(data, dict):
        for key in ("heatmap", "data", "bins", "levels"):
            if key in data and isinstance(data[key], list):
                return data[key]
        if "priceBinStart" in data:
            return [data]
    elif isinstance(data, list):
        return data
    return []


# --------------------------------------------------------------------------- #
#  Open positions (fallback aggregation)
# --------------------------------------------------------------------------- #
def get_open_positions(coin, token=None):
    """Download all open perp positions for a coin.

    Returns a list of position dicts.  Each has:
      address, coin, side, size, value, entryPrice, liquidationPrice,
      crossLeverage, liquidationProgress, unrealizedPnl, funding
    """
    data = _fetch(coin, "positions/open/coin/{coin}", token)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("positions", "data", "rows"):
            if key in data and isinstance(data[key], list):
                return data[key]
    return []


# --------------------------------------------------------------------------- #
#  Wallet info
# --------------------------------------------------------------------------- #
def get_wallet_info(address, token=None):
    """Fetch wallet-level info from HyperTracker."""
    try:
        data = _fetch(address, "trader/{address}", token, is_address=True)
        if data and not (isinstance(data, dict) and "error" in str(data)):
            return data
    except HyperTrackerError:
        raise
    except Exception:
        pass
    return {}


# --------------------------------------------------------------------------- #
#  Internal: fetch + parse with CSV fallback
# --------------------------------------------------------------------------- #
def _fetch(coin, path_template, token=None, is_address=False):
    """Fetch from HyperTracker, handling 302 redirect + CSV fallback.

    Args:
        coin: coin symbol (e.g. "XRP") or address string
        path_template: e.g. "exports/coins/{coin}/liquidation-heatmap"
        token: HyperTracker API key (optional, uses env var if not given)
        is_address: if True, *coin* is an address, not a coin symbol
    """
    path = path_template.format(coin=coin, address=coin)
    body, _resp = _get_raw(path, token=token)
    return _parse_body(body, token=token, path=path)
