#!/bin/bash
# Daily crypto volatility dashboard update script
# Runs the data pipeline and commits results to git

set -e

cd /c/Users/ethan/crypto-volatility

# Generate fresh data
python generate_data.py --output data/data.json --self-contained

# Commit and push
git add data/data.json index.html
if ! git diff --cached --quiet; then
    git commit -m "Daily auto-update ($(date -u +%Y-%m-%dT%H:%M:%SZ))" || true
    git push origin main
    echo "Dashboard data updated and pushed to GitHub."
else
    echo "No changes to commit."
fi
