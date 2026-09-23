# Crypto Volatility + Liquidation Dashboard

Real-time dashboard tracking liquidation levels and volatility across major crypto perps.

## Root Cause of the Previous Stale Dashboard

The old `index.html` had **all data hardcoded** as JavaScript `const` objects — no pipeline, no automation, no API calls. It was last generated on Sep 10, 2026 and never updated.

## How It Works Now

### Architecture
```
index.html  ← fetch → data/data.json
                  ↑
            generate_data.py
   (daily via GitHub Actions or local cron)
```

1. **`generate_data.py`** runs daily and fetches live data:
   - **Hyperliquid API** (free) — perp prices, 24h volumes, open interest, hourly candles
   - **SmartMoneyAPI** (free tier) — liquidation clusters for BTC, ETH, SOL
   - **HyperTracker API** (paid key) — liquidation heatmaps for all altcoins, whale wallet positions
   - **CoinGecko** (fallback) — prices for tokens not on Hyperliquid (e.g. SHIB)

2. The script computes:
   - **CC vol** — Close-to-Close realized volatility (annualized)
   - **PK vol** — Parkinson volatility (high/low range)
   - **HAR-RV** — Heterogeneous Autoregressive forecast
   - **Danger zones** — tokens with liquidations within 1%/2%/5% of spot price
   - **Square-root price impact** — estimated move if a cascade triggers (η·σ·√(Q/V))

3. Output: `data/data.json` — `index.html` fetches this on page load.

### Data Sources

| Source | Endpoint | What | Key needed? |
|--------|----------|------|-------------|
| Hyperliquid | `api.hyperliquid.xyz/info` | Prices, volumes, OI, candles (1h, 7d) | No |
| SmartMoneyAPI | `api.smartmoneyapi.com/v1/liquidations/heatmap` | Liquidation clusters (BTC, ETH, SOL only) | No |
| HyperTracker | `ht-api.coinmarketman.com/api/external/...` | Liquidation heatmaps (all tokens), whale positions | Yes |
| CoinGecko | `api.coingecko.com/api/v3/` | Fallback prices/volumes | No |

## Setup

### GitHub Actions (recommended — fully automated)

1. Set `HYPERTRACKER_API_KEY` as a GitHub repository secret
2. The daily workflow (`.github/workflows/update.yml`) runs at 6:00 AM UTC
3. It generates `data.json`, commits it, and pushes to `main`
4. GitHub Pages serves the result

### Local Cron

```bash
# Add to crontab (runs daily at 6 AM local time)
0 6 * * * cd /path/to/crypto-volatility && HYPERTRACKER_API_KEY=yourkey python generate_data.py

# Or use Hermes cron
hermes cron create "crypto-viz-update" "cd /c/Users/ethan/crypto-volatility && HYPERTRACKER_API_KEY=... python generate_data.py" --schedule "0 6 * * *"
```

### One-off generation

```bash
python generate_data.py
```

## What's Improved vs. the Static Version

1. **Live data** — prices, volumes, and liquidation levels refresh daily
2. **Square-root impact model** — estimated price impact uses `η · σ · √(Q/V)` instead of arbitrary thresholds
3. **Whale wallet tracking** — tracks specified wallets (e.g. `0x77dd…0e9` "Leviathan") for positions near liquidation
4. **Direction badges** — UP/DOWN with color-coded severity
5. **Refresh button** — reload data from the browser without redeploying
6. **Self-contained fallback** — `python generate_data.py --self-contained` bakes data into `index.html` for single-file deploy
7. **Data source attribution** — footer shows which APIs were used

## Token Coverage

| Token | Price Source | Liquidation Source |
|-------|-------------|-------------------|
| BTC, ETH, SOL | Hyperliquid | SmartMoneyAPI (free) |
| XRP, ADA, LTC, DOGE, AVAX, LINK, SUI, BNB, HYPE, DOT, XLM, HBAR, BCH | Hyperliquid | HyperTracker |
| SHIB | CoinGecko (fallback) | HyperTracker |
