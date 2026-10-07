#!/bin/bash
# stock_daily_refresh — run ON s2 (cron 15 21 * * 1-5 UTC, after the US close): (1) on s1 (owner of the live Tradier klines cache) gap-fill the
# 5m/15m/1h/4h caches of today's stock universe (tradier_klines.py times out for most symbols), (2) rsync those klines s1 -> s2 (update-only, with backup),
# (3) refresh s2's stock NPZs (validated with the sweep loader, never replaces a good/longer file with a worse one, backups in ~/npz_backup_<date>).
# Idempotent + flock singleton. Log: ~/binance-sandbox/logs/stock_daily_refresh.log
exec 9>/tmp/stock_daily_refresh.lock; flock -n 9 || exit 0
cd ~/binance-sandbox || exit 3
S1=niels@10.0.0.3
D=$(date -u +%Y%m%d)
U=$(ssh -o BatchMode=yes $S1 'ls -t ~/binance-sandbox/data/daily_universe/*.json | head -1')
[ -n "$U" ] || { echo "no universe json on s1"; exit 4; }
ssh -o BatchMode=yes $S1 "python3 -c \"import json;print('\n'.join(json.load(open('$U'))['stocks']))\"" > /tmp/stock_universe_daily.txt
[ -s /tmp/stock_universe_daily.txt ] || { echo "empty universe"; exit 5; }
python3 - <<'PY' > /tmp/klines_files_daily.txt
for s in open('/tmp/stock_universe_daily.txt').read().split():
    for tf in ['1m','5m','15m','1h','4h','D']:
        print(f'{s}_{tf}.json')
PY
scp -q /tmp/stock_universe_daily.txt /tmp/klines_files_daily.txt $S1:/tmp/
ssh -o BatchMode=yes $S1 "cd ~/binance-sandbox && nice -n 10 .venv/bin/python -u tools/stock_klines_gapfill.py --symbols-file /tmp/stock_universe_daily.txt --conc 5 | tail -3; mkdir -p ~/klines_bak_$D; cd klines_cache/tradier && nice -n 10 rsync -a --update --backup --backup-dir=\$HOME/klines_bak_$D --files-from=/tmp/klines_files_daily.txt ./ 10.0.0.4:/home/niels/binance-sandbox/klines_cache/tradier/"
nice -n 10 .venv/bin/python -u tools/stock_npz_refresh.py --symbols-file /tmp/stock_universe_daily.txt --stage $HOME/npz_stage_$D --backup $HOME/npz_backup_$D
