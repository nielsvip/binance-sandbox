#!/bin/bash
# kline_twins_apply_s1.sh - run ON S1 only, only when `python3 /tmp/seal_count.py` says 0 quarantined files are still without a seal.
# Composed from the steps that worked in FOLLOW-UP 7 (pause -> rewrite -> quarantine sealed gens + rebaseline guard -> reseal -> unpause). NOT yet executed.
set -u
cd ~/binance-sandbox
PY=/home/niels/.conda/envs/binance_env_mac/bin/python
TAG="#PAUSED_20261029_TWINS# "
crontab -l > ~/backups/crontab_ORIGINAL_pre_twins.txt
sed -e "/klines_vault.py --enforce/s|^\([^#]\)|$TAG\1|" -e "/klines_vault.py --seal/s|^\([^#]\)|$TAG\1|" -e "/# KLINES_GUARD_TRIPWIRE\$/s|^\([^#]\)|$TAG\1|" -e "/klines_append.py --all/s|^\([^#]\)|$TAG\1|" ~/backups/crontab_ORIGINAL_pre_twins.txt > ~/backups/crontab_twins_paused.txt
[ "$(wc -l < ~/backups/crontab_ORIGINAL_pre_twins.txt)" = "$(wc -l < ~/backups/crontab_twins_paused.txt)" ] || { echo "line count differs, abort"; exit 1; }
crontab ~/backups/crontab_twins_paused.txt
while pgrep -f "klines_guard.py|klines_vault.py|klines_append.py" >/dev/null; do sleep 3; done   # (kill a running klines_append by pid if it blocks: it is idempotent)
for i in 0 1 2 3; do $PY tools/kline_dedupe_twins.py --dir klines_cache/tradier --orig-backups ~/stamp_norm_backup/tradier --canonical-dir klines_canonical --apply --shard $i/4 --log data/reports/kline_dedupe_twins_s1.jsonl > /tmp/twins_apply_$i.out 2>&1 & done
wait; cat /tmp/twins_apply_*.out
$PY - <<'PYEOF'
import json, os, shutil
log = [json.loads(l) for l in open("data/reports/kline_dedupe_twins_s1.jsonl") if l.strip()]
names = ["tradier/" + d["file"] for d in log if d.get("applied")]
st = json.load(open("data/klines_guard_state.json")); q = os.path.expanduser("~/stamp_norm_backup/vault_twins"); os.makedirs(q, exist_ok=True); moved = 0
for nm in names:
    g = f"klines_vault/{nm}.gz"
    if os.path.exists(g): shutil.move(g, os.path.join(q, nm.replace("/", "__") + ".gz")); moved += 1
    st.pop("klines_cache_backtest/" + nm, None)
    s = f"klines_cache_backtest/.guard_snap/{nm}.gz"
    if os.path.exists(s): os.unlink(s)
json.dump(st, open("data/klines_guard_state.json.tmp", "w")); os.replace("data/klines_guard_state.json.tmp", "data/klines_guard_state.json")
print("sealed gens quarantined", moved, "of", len(names), "files")
PYEOF
$PY tools/klines_guard.py --tripwire | tail -1
sed -e "s|^#PAUSED_20261029_TWINS# ||" ~/backups/crontab_twins_paused.txt > ~/backups/crontab_twins_unpaused.txt
crontab ~/backups/crontab_twins_unpaused.txt     # the hourly --seal cron re-seals the rewritten files
echo "unpaused; then: tools/stamp_tripwire_fleet.sh FULL=1 and kline_dedupe_twins dry-run must report files_with_twins 0"
