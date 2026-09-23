#!/usr/bin/env python
"""
Data pipeline for the Crypto Volatility + Liquidation Dashboard.

Fetches live data from:
  - Hyperliquid API (free): prices, 24h volume, open interest, hourly candles
  - SmartMoneyAPI (free tier): liquidation clusters for BTC, ETH, SOL
  - HyperTracker API (paid, env key): liquidation heats for all other tokens
  - CoinGecko (fallback): prices and volumes for tokens not on Hyperliquid

Computes:
  - CC (Close-to-Close), PK (Parkinson), HAR-RV volatility forecasts
  - Liquidation levels with price-distance, magnitude, and estimated impact
  - Danger zones (tokens with liquidations within 1%/2%/5% of spot)
  - Whale wallet risk (using a configurable watchlist)

Outputs:
  - data/data.json  — consumed by index.html at runtime
  - index.html      — optionally baked with data for single-file deploy

Usage:
    python generate_data.py                  # writes data/data.json
    HYPERTRACKER_API_KEY=... python generate_data.py
    python generate_data.py --self-contained # also rewrites index.html with inline data
"""
import argparse
import json
import math
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

# --- lib imports ----------------------------------------------------------- #
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))
import hyperliquid   # noqa: E402
import volatility    # noqa: E402
import hypertracker  # noqa: E402

# --------------------------------------------------------------------------- #
#  Configuration
# --------------------------------------------------------------------------- #
TARGET_TOKENS = [
    "BTC", "ETH", "SOL", "XRP", "ADA", "LTC", "DOGE", "AVAX",
    "LINK", "SUI", "BNB", "HYPE", "DOT", "XLM", "SHIB", "HBAR", "BCH",
]

MAJORS = {"BTC", "ETH", "SOL"}
ALTS = set(TARGET_TOKENS) - MAJORS

# Tokens not available on Hyperliquid perps — fall back to CoinGecko
COINGECKO_FALLBACK = {"SHIB"}

COINGECKO_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
    "XRP": "ripple", "ADA": "cardano", "LTC": "litecoin",
    "DOGE": "dogecoin", "AVAX": "avalanche-2", "LINK": "chainlink",
    "SUI": "sui", "BNB": "binancecoin", "HYPE": "hyperliquid",
    "DOT": "polkadot", "XLM": "stellar", "SHIB": "shiba-inu",
    "HBAR": "hedera-hashgraph", "BCH": "bitcoin-cash",
}

# Whale wallets to track (user-specified + known large positions)
WHALE_WALLETS = {
    "0x77ddcc4b6440b3a32b7802f053105c2fad3ae0e9": {"label": "Leviathan", "exchange": "HyperTracker"},
}

TOKEN_COLORS = {
    "BTC": "#f7931a", "ETH": "#627eea", "SOL": "#9945ff", "XRP": "#23292f",
    "ADA": "#0033ad", "LTC": "#345d9d", "DOGE": "#c2a633", "AVAX": "#e84142",
    "LINK": "#2a5ada", "SUI": "#4da2ff", "BNB": "#f3ba2f", "HYPE": "#8b5cf6",
    "DOT": "#e6007a", "XLM": "#7d00ff", "SHIB": "#ffa409", "HBAR": "#00b39f",
    "BCH": "#8dc351",
}

# Square-root impact model constant (per user's research-backed preference)
IMPACT_ETA = 0.10

# --------------------------------------------------------------------------- #
#  Data fetchers
# --------------------------------------------------------------------------- #
def fetch_hyperliquid_data():
    """Fetch prices, volumes, OI, funding, and candles from Hyperliquid."""
    print("[1/4] Fetching Hyperliquid perp data ...")
    perps = hyperliquid.get_perps()
    perp_map = {p["name"]: p for p in perps}
    print(f"      Found {len(perps)} perps, {len(perp_map)} mapped to our tokens")

    data = {}
    for token in TARGET_TOKENS:
        if token in perp_map:
            p = perp_map[token]
            data[token] = {
                "current_price": p["markPx"],
                "volume": p["dayNtlVlm"],
                "open_interest": p["openInterest"],
                "funding": p["funding"],
            }
            # Fetch candles for volatility
            try:
                candles = hyperliquid.get_candles(token, "1h", lookback_hours=200)
                data[token]["candles"] = candles
                data[token]["candle_count"] = len(candles)
            except Exception as e:
                print(f"      WARNING: candles for {token} failed: {e}")
                data[token]["candles"] = []
                data[token]["candle_count"] = 0
        elif token in COINGECKO_FALLBACK:
            print(f"      {token} not on Hyperliquid, will use CoinGecko fallback")
            data[token] = None

    return data


def fetch_coingecko_fallback(tokens):
    """Fetch prices and volumes for tokens not on Hyperliquid."""
    import urllib.request
    prices = {}
    vols = {}
    ids = ",".join(COINGECKO_IDS[t] for t in tokens if t in COINGECKO_IDS)
    if not ids:
        return {}, {}

    # Current prices + volume
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_24hr_vol=true"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "crypto-viz/1.0"})
        resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
        for token in tokens:
            cg_id = COINGECKO_IDS.get(token)
            if cg_id and cg_id in resp:
                prices[token] = resp[cg_id]["usd"]
                vols[token] = resp[cg_id].get("usd_24h_vol", 0)
    except Exception as e:
        print(f"      CoinGecko fallback error: {e}")

    return prices, vols


def fetch_liquidation_clusters_smartmoney(token):
    """Fetch liquidation clusters from SmartMoneyAPI (free tier: BTC, ETH, SOL)."""
    import time as _time
    last_err = None
    for attempt in range(3):
        try:
            url = f"https://api.smartmoneyapi.com/v1/liquidations/heatmap?symbol={token}"
            req = urllib.request.Request(url, headers={"User-Agent": "crypto-viz/1.0"})
            resp = urllib.request.urlopen(req, timeout=30)
            data = json.loads(resp.read())
            clusters = data.get("clusters", [])
            print(f"      SmartMoneyAPI {token}: {len(clusters)} liquidation clusters")
            return clusters
        except Exception as e:
            last_err = e
            _time.sleep(2 * (attempt + 1))
    print(f"      SmartMoneyAPI {token}: failed after 3 attempts — {last_err}")
    return []


def fetch_liquidation_heatmap_hypertracker(token, ht_key):
    """Fetch liquidation heatmap from HyperTracker API."""
    try:
        heatmap = hypertracker.get_liquidation_heatmap(token, token=ht_key)
        print(f"      HyperTracker {token}: {len(heatmap)} heatmap bins")
        return heatmap
    except hypertracker.HyperTrackerError as e:
        print(f"      HyperTracker {token}: {e}")
        return []
    except Exception as e:
        print(f"      HyperTracker {token}: {e}")
        return []


def fetch_whale_positions(ht_key=None):
    """Fetch positions for tracked whale wallets.

    Uses Hyperliquid's public clearinghouseState endpoint (no key needed)
    as the primary source, since it returns any address's positions.
    """
    whales = []
    for addr, meta in WHALE_WALLETS.items():
        try:
            positions = hyperliquid._post({
                "type": "clearinghouseState",
                "user": addr,
            })
            asset_positions = positions.get("assetPositions", [])
            total_value = float(positions.get("marginSummary", {}).get("totalNtlPos", 0))
            for ap in asset_positions:
                pos = ap.get("position", {})
                coin = pos.get("coin", "")
                if not coin:
                    continue
                szi = float(pos.get("szi", 0))
                notional = float(pos.get("positionValue", 0))
                mark_px = notional / szi if szi else 0
                entry_px = float(pos.get("entryPx", 0))
                liq_px = float(pos.get("liquidationPx", 0))
                # Distance to liquidation
                if mark_px > 0 and liq_px > 0:
                    dist = (liq_px - mark_px) / mark_px * 100
                else:
                    dist = 0.0
                leverage_obj = pos.get("leverage", {})
                leverage = leverage_obj.get("value", 0)
                # PnL
                upnl = float(pos.get("unrealizedPnl", 0))
                roi = (upnl / notional * 100) if notional > 0 else 0
                direction = "LONG" if szi > 0 else "SHORT"

                whales.append({
                    "address": addr,
                    "label": meta["label"],
                    "token": coin,
                    "direction": direction,
                    "size": szi,
                    "notional": notional,
                    "entry_price": entry_px,
                    "liq_price": liq_px,
                    "dist_to_liq_pct": dist,
                    "leverage": leverage,
                    "pnl": upnl,
                    "roi_30d": roi,
                    "side": "long" if szi > 0 else "short",
                })
        except Exception as e:
            print(f"      WARNING: whale {meta['label']} ({addr}): {e}")

    return whales


# --------------------------------------------------------------------------- #
#  Metric calculations
# --------------------------------------------------------------------------- #
def compute_volatility(token, candles):
    """Compute CC, PK, and HAR-RV volatility from hourly candles."""
    if not candles or len(candles) < 24:
        return {"cc_vol": 0.0, "pk_vol": 0.0, "har_forecast": 0.0}
    cc = volatility.cc_volatility(candles)
    pk = volatility.parkinson_volatility(candles)
    har = volatility.har_rv_forecast(candles)
    return {"cc_vol": cc, "pk_vol": pk, "har_forecast": har}


def compute_estimated_move(cumulative_notional, daily_volume, daily_vol_pct):
    """Square-root price impact: ΔP/P = η * σ_daily * sqrt(Q / V_daily).

    eta = IMPACT_ETA (0.10, calibrated for crypto perp markets)
    sigma_daily = daily vol as decimal
    Q = cumulative liquidation notional (USD)
    V = daily volume (USD)
    Returns (est_move_pct, move_per_vol)
    """
    if daily_volume <= 0 or daily_vol_pct <= 0:
        return 0.0, 0.0
    sigma_daily = daily_vol_pct / 100.0  # convert to decimal
    q_over_v = cumulative_notional / daily_volume
    if q_over_v <= 0:
        return 0.0, 0.0
    impact_decimal = IMPACT_ETA * sigma_daily * math.sqrt(q_over_v)
    est_move_pct = impact_decimal * 100.0
    move_per_vol = est_move_pct / daily_vol_pct if daily_vol_pct > 0 else 0.0
    return est_move_pct, move_per_vol


def build_level(price, current_price, daily_vol_pct, cumulative_notional,
                daily_volume, count):
    """Build a single liquidation level dict."""
    distance_pct = (price - current_price) / current_price * 100
    abs_distance = abs(distance_pct)
    adj_distance = abs_distance / (daily_vol_pct / math.sqrt(365)) if daily_vol_pct > 0 else 0
    mag_ratio = cumulative_notional / daily_volume if daily_volume > 0 else 0
    est_move, move_per_vol = compute_estimated_move(
        cumulative_notional, daily_volume, daily_vol_pct
    )
    direction = "UP" if distance_pct > 0 else "DOWN"
    return {
        "liq_price": price,
        "current_price": current_price,
        "distance_pct": distance_pct,
        "abs_distance": abs_distance,
        "cumulative_notional": cumulative_notional,
        "count": count,
        "magnitude_ratio": mag_ratio,
        "direction": direction,
        "adj_distance": adj_distance,
        "est_move_pct": est_move,
        "move_per_vol": move_per_vol,
    }


def process_token(token, hl_data, vol_data, ht_key):
    """Process a single token: fetch liquidation data and build analysis entry."""
    current_price = hl_data["current_price"]
    daily_volume = hl_data["volume"]
    daily_vol = vol_data["har_forecast"]

    # Fetch liquidation clusters
    clusters = []
    if token in MAJORS:
        clusters = fetch_liquidation_clusters_smartmoney(token)
    elif ht_key:
        heatmap = fetch_liquidation_heatmap_hypertracker(token, ht_key)
        # Convert heatmap bins to clusters
        for bin in heatmap:
            mid = (float(bin["priceBinStart"]) + float(bin["priceBinEnd"])) / 2
            clusters.append({
                "price": mid,
                "notional": float(bin.get("liquidationValue", 0)),
                "count": int(bin.get("positionsCount", 0)),
                "dominant_side": "long" if mid < current_price else "short",
            })
    else:
        print(f"      {token}: no liquidation data source (set HYPERTRACKER_API_KEY)")
        return None

    if not clusters:
        return None

    # Filter to clusters near current price (within 50% distance for relevance)
    max_dist_pct = 50.0
    relevant = []
    for c in clusters:
        price = float(c["price"])
        distance = abs(price - current_price) / current_price * 100
        if distance <= max_dist_pct:
            relevant.append((price, c))

    if not relevant:
        return None

    # Sort by absolute distance from current price (nearest first)
    relevant.sort(key=lambda x: abs(x[0] - current_price))

    # Build all_levels with cumulative notional (running sum from nearest)
    all_levels = []
    cumulative = 0.0
    cumulative_count = 0
    for price, cluster in relevant:
        notional = float(cluster.get("notional", 0))
        count = int(cluster.get("count", 0))
        cumulative += notional
        cumulative_count += count
        level = build_level(
            price, current_price, daily_vol, cumulative,
            daily_volume, cumulative_count
        )
        all_levels.append(level)

    # Top-level entry uses the nearest level
    nearest = all_levels[0]
    notional_at_nearest = float(relevant[0][1].get("notional", 0))

    return {
        "token": token,
        "liq_price": nearest["liq_price"],
        "current_price": current_price,
        "distance_pct": nearest["distance_pct"],
        "abs_distance": nearest["abs_distance"],
        "notional": notional_at_nearest,
        "cumulative_notional": nearest["cumulative_notional"],
        "count": nearest["count"],
        "magnitude_ratio": nearest["magnitude_ratio"],
        "direction": nearest["direction"],
        "adj_distance": nearest["adj_distance"],
        "est_move_pct": nearest["est_move_pct"],
        "move_per_vol": nearest["move_per_vol"],
        "all_levels": all_levels,
    }


# --------------------------------------------------------------------------- #
#  Danger zones
# --------------------------------------------------------------------------- #
def compute_danger_zones(analysis):
    """Classify tokens into danger zones based on distance to liquidation."""
    zone_defs = [
        ("within_1pct", 1.0),
        ("within_2pct", 2.0),
        ("within_5pct", 5.0),
    ]
    alts_zones = {k: [] for k, _ in zone_defs}
    majors_zones = {k: [] for k, _ in zone_defs}

    for entry in analysis:
        token = entry["token"]
        dist = entry["abs_distance"]
        for key, threshold in zone_defs:
            if dist <= threshold:
                if token in MAJORS:
                    majors_zones[key].append(token)
                else:
                    alts_zones[key].append(token)

    return {
        "alts": alts_zones,
        "majors": majors_zones,
    }


# --------------------------------------------------------------------------- #
#  Main
# --------------------------------------------------------------------------- #
def main():
    parser = argparse.ArgumentParser(description="Generate crypto-volatility data.json")
    parser.add_argument("--self-contained", action="store_true",
                        help="Also bake data into index.html for single-file deploy")
    parser.add_argument("--output", default=None,
                        help="Output JSON path (default: data/data.json)")
    args = parser.parse_args()

    ht_key = os.environ.get("HYPERTRACKER_API_KEY")
    if ht_key:
        print("HyperTracker API key found.")
    else:
        print("No HYPERTRACKER_API_KEY set — altcoin liquidations will be limited.")

    # ----------------------------------------------------------------------- #
    #  Step 1: Fetch Hyperliquid perps + candles
    # ----------------------------------------------------------------------- #
    hl_data = fetch_hyperliquid_data()

    # CoinGecko fallback for tokens not on Hyperliquid
    fallback_tokens = [t for t in TARGET_TOKENS if hl_data.get(t) is None]
    if fallback_tokens:
        print(f"\n[1b/4] Fetching CoinGecko data for {fallback_tokens} ...")
        cg_prices, cg_vols = fetch_coingecko_fallback(fallback_tokens)
        for t in fallback_tokens:
            if t in cg_prices:
                hl_data[t] = {
                    "current_price": cg_prices[t],
                    "volume": cg_vols[t],
                    "open_interest": 0,
                    "funding": 0,
                    "candles": [],
                    "candle_count": 0,
                }

    # ----------------------------------------------------------------------- #
    #  Step 2: Compute volatility
    # ----------------------------------------------------------------------- #
    print("\n[2/4] Computing volatility metrics ...")
    vol_data = {}
    for token in TARGET_TOKENS:
        d = hl_data.get(token)
        if d is None:
            continue
        candles = d.get("candles", [])
        if not candles or len(candles) < 24:
            print(f"      {token}: skipping volatility (no candle data)")
            # Still include the token in vol_data with zero values so it
            # shows in the grid, but mark it
            vol_data[token] = {"cc_vol": 0.0, "pk_vol": 0.0, "har_forecast": 0.0}
            continue
        vol = compute_volatility(token, candles)
        vol_data[token] = vol
        print(f"      {token}: CC={vol['cc_vol']:.2f}%  PK={vol['pk_vol']:.2f}%  HAR={vol['har_forecast']:.2f}%")
    print("\n[3/4] Fetching liquidation data ...")
    analysis = []
    for token in TARGET_TOKENS:
        d = hl_data.get(token)
        if d is None:
            continue
        v = vol_data.get(token)
        if v is None:
            continue
        entry = process_token(token, d, v, ht_key)
        if entry:
            analysis.append(entry)
        else:
            print(f"      {token}: no liquidation data — skipping")

    # Sort by absolute distance (nearest first)
    analysis.sort(key=lambda x: x["abs_distance"])

    # ----------------------------------------------------------------------- #
    #  Step 4: Compute danger zones + whale wallets
    # ----------------------------------------------------------------------- #
    print("\n[4/4] Computing danger zones + whale wallets ...")
    danger_zones = compute_danger_zones(analysis)

    whale_wallets = fetch_whale_positions(ht_key)
    print(f"      Whale wallets tracked: {len(whale_wallets)}")

    # ----------------------------------------------------------------------- #
    #  Build output
    # ----------------------------------------------------------------------- #
    volumes = {t: hl_data[t]["volume"] for t in TARGET_TOKENS if t in hl_data}

    output = {
        "generated_at": datetime.now(timezone.utc).strftime("%b %d, %Y %H:%M UTC"),
        "analysis": analysis,
        "volatility": vol_data,
        "volumes": volumes,
        "token_colors": TOKEN_COLORS,
        "danger_zones": danger_zones,
        "whale_wallets": whale_wallets,
        "data_sources": {
            "prices": "Hyperliquid API (hyperliquid.xyz)",
            "volumes": "Hyperliquid API",
            "candles": "Hyperliquid candleSnapshot",
            "liquidations_majors": "SmartMoneyAPI free tier",
            "liquidations_alts": "HyperTracker API" if ht_key else "not available (set HYPERTRACKER_API_KEY)",
            "whale_wallets": "Hyperliquid clearinghouseState (public)",
        },
    }

    # Write data.json
    out_path = args.output or os.path.join(os.path.dirname(__file__), "data", "data.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\n✓ data.json written to {out_path}")
    print(f"  Tokens with liquidation data: {len(analysis)}")
    print(f"  Whale wallets: {len(whale_wallets)}")

    # Optionally bake into index.html for single-file deploy
    if args.self_contained:
        bake_into_html(out_path, output)

    return output


def bake_into_html(data_path, data):
    """Bake data.json contents directly into index.html as inline JavaScript.

    This is useful for single-file deployment without a web server
    capable of serving data.json.
    """
    # Read the existing index.html
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    if not os.path.exists(html_path):
        print(f"      WARNING: {html_path} not found, skipping self-contained build")
        return

    with open(html_path, "r") as f:
        html = f.read()
    data_json = json.dumps(data, separators=(",", ":"))
    html = html.replace("__DATA_JSON_PLACEHOLDER__", data_json)
    with open(html_path, "w") as f:
        f.write(html)
    print(f"      ✓ index.html baked with inline data")


if __name__ == "__main__":
    main()
