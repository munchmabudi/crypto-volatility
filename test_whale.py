import urllib.request, json

url = "https://api.hyperliquid.xyz/info"
payload = json.dumps({"type": "clearinghouseState", "user": "0x77ddcc4b6440b3a32b7802f053105c2fad3ae0e9"}).encode()
req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
resp = urllib.request.urlopen(req, timeout=15)
data = json.loads(resp.read())

print("Top-level keys:", list(data.keys()))
ap = data.get("assetPositions", [])
print(f"assetPositions count: {len(ap)}")
if ap:
    pos = ap[0].get("position", {})
    print("Position keys:", list(pos.keys()))
    print("coin:", pos.get("coin"))
    print("szi:", pos.get("szi"))
    print("entryPx:", pos.get("entryPx"))
    print("liqPx:", pos.get("liqPx"))
    print("markPx:", pos.get("markPx"))
