import urllib.request, json

resp = urllib.request.urlopen(
    "https://munchmabudi.github.io/crypto-volatility/data/data.json",
    timeout=15,
)
d = json.loads(resp.read())

print(f'Generated: {d["generated_at"]}')
tokens = [a["token"] for a in d["analysis"]]
print(f'Tokens with liquidation data: {len(tokens)}')
print(f'Tokens: {tokens}')
print(f'Whale wallets: {len(d.get("whale_wallets", []))}')
for w in d.get("whale_wallets", []):
    notional_m = w["notional"] / 1e6
    print(f'  {w["label"]}: {w["token"]} {w["direction"]} ${notional_m:.1f}M notional, liq at ${w["liq_price"]:.2f} ({w["dist_to_liq_pct"]:+.1f}%)')
print(f'Data sources: {d.get("data_sources", {})}')
print()
for a in d["analysis"][:5]:
    print(f'{a["token"]}: liq=${a["liq_price"]:.2f} cur=${a["current_price"]:.2f} dir={a["direction"]} dist={a["distance_pct"]:+.2f}% mag={a["magnitude_ratio"]:.1f}x levels={a["count"]}')
