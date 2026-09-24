"""HyperTracker API client — liquidation heatmaps, positions, whale wallets.

Requires an API key set via the HYPERTRACKER_API_KEY env var.

Key endpoints:
  - /api/external/exports/coins/{coin}/liquidation-heatmap  (302 → pre-signed JSON)
  - /api/external/positions/open/coin/{coin}                 (302 → pre-signed CSV/JSON)
  - /api/external/trader/{address}                           (wallet-level data)
""";
import json, os, urllib.request, urllib.error;

BASE_URL = "https://ht-api.coinmarketman.com/api/external"


class HyperTrackerError(Exception):
    pass


# --------------------------------------------------------------------------- #
#  Low-level HTTP
# --------------------------------------------------------------------------- #
class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Capture 302 redirects so we can inspect / retry the Location URL."""
    def __init__(self):
        self.redirect_url = None
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.redirect_url = newurl
        return None  # don't auto-follow


def _get_raw(path, token=None, timeout=60, follow_redirects=True):
    """GET request to HyperTracker, optionally following the 302 to a pre-signed URL."""
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
        # Try JSON first; fall back to text (some endpoints return CSV text)
        try:
            return json.loads(body), resp
        except (json.JSONDecodeError, ValueError):
            return body.decode("utf-8", errors="replace"), resp
    except urllib.error.HTTPError as e:
        # Read the error body for diagnostics
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        if e.code in (401, 403):
            raise HyperTrackerError(f"Auth failed ({e.code}) for {path}. Body: {err_body}")
        raise HyperTrackerError(f"HTTP {e.code} for {path}. Body: {err_body}")


def _get(path, token=None, timeout=60):
    """GET request, following the 302 redirect that export endpoints issue.

    Returns parsed JSON (dict or list).
    """
    result, _ = _get_raw(path, token=token, timeout=timeout)
    if isinstance(result, bytes):
        result = result.decode("utf-8", errors="replace")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (json.JSONDecodeError, ValueError):
            pass
    return result


# --------------------------------------------------------------------------- #
#  Heatmap
# --------------------------------------------------------------------------- #
def get_liquidation_heatmap(coin, token=None):
    """Download the liquidation heatmap for a single coin.

    Returns a list of dicts with keys (depending on API version):
      priceBinStart, priceBinEnd, liquidationValue, positionsCount,
      mostImpactedSegment
    """
    result = _get(f"exports/coins/{coin}/liquidation-heatmap", token=token)
    # The pre-signed URL might return the data nested under different keys
    if isinstance(result, dict):
        for key in ("heatmap", "data", "bins", "levels"):
            if key in result:
                val = result[key]
                if isinstance(val, list):
                    return val
        # Maybe the result IS the heatmap directly
        if "priceBinStart" in result:
            return [result]
    if isinstance(result, list):
        return result
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
    result = _get(f"positions/open/coin/{coin}", token=token)
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ("positions", "data", "rows"):
            if key in result and isinstance(result[key], list):
                return result[key]
    return []


# --------------------------------------------------------------------------- #
#  Wallet info
# --------------------------------------------------------------------------- #
def get_wallet_info(address, token=None):
    """Fetch wallet-level info from HyperTracker."""
    try:
        result = _get(f"trader/{address}", token=token)
        if result and not (isinstance(result, dict) and "error" in result):
            return result
    except Exception:
        pass
    return {}
