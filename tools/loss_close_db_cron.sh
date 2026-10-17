#!/bin/bash
# Daily loss-close DB refresh (USER 2026-10-17): rebuild data/loss_prone_entries.json from board + live ledgers on the Mac, then ship the
# flag file to the S1/S2 sandboxes (the engine's LOSS_PRONE_ENTRY_FILTER / PEAK_GIVEBACK_SCOPE read it) and live dirs. Atomic, never empties.
set -u
cd /Users/niels/Documents/binance
PY=/opt/anaconda3/envs/binance_env/bin/python
F=data/loss_prone_entries.json
/opt/homebrew/bin/timeout 1800 $PY tools/loss_close_db.py || { echo "$(date -u +%FT%TZ) loss_close_db failed"; exit 1; }
$PY -c "import json,sys; d=json.load(open('$F')); sys.exit(0 if isinstance(d.get('crypto'),list) and isinstance(d.get('stocks'),list) else 1)" || { echo "bad flag file"; exit 1; }
rsync -az -e "ssh -o BatchMode=yes -o ConnectTimeout=15" $F s1-pub:/tmp/loss_prone_entries.json || exit 1
ssh -o BatchMode=yes -o ConnectTimeout=15 s1-pub 'for d in ~/binance-sandbox ~/binance; do cp /tmp/loss_prone_entries.json $d/data/.lpe.tmp && mv $d/data/.lpe.tmp $d/data/loss_prone_entries.json; done; scp -q /tmp/loss_prone_entries.json 10.0.0.4:/tmp/ && ssh 10.0.0.4 "for d in ~/binance-sandbox ~/binance; do cp /tmp/loss_prone_entries.json \$d/data/.lpe.tmp && mv \$d/data/.lpe.tmp \$d/data/loss_prone_entries.json; done"'
echo "$(date -u +%FT%TZ) pushed $(md5 -q $F | cut -c1-8) crypto=$($PY -c "import json; print(len(json.load(open('$F'))['crypto']))") stocks=$($PY -c "import json; print(len(json.load(open('$F'))['stocks']))")"
