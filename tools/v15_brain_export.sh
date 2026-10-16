#!/bin/bash
# v15_brain_export.sh — daily rebuild of the V15 BRAIN model (BIBLE §108). Runs on S2 (largest delta-log volume), nice 19, 1 process
# (~7 min, ~1.6 GB). Prequential-trains tools/v15_brain.py over the last V15_BRAIN_SINCE_D days of real engine evals and writes
# data/brain/brain_<CAT_SIDE>.json (atomic per file), then copies the files to S1 (10.0.0.3) so every pilot reads the same model.
# Skips (exit 0) when MemAvailable < 6 GB so it never pushes a worker into the OOM reaper.
set -u
cd /home/niels/binance-sandbox || exit 1
LOG=/home/niels/logs/v15_brain_export.log
mkdir -p "$(dirname "$LOG")" data/brain
ts(){ date -u +%FT%TZ; }
avail=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
if [ "${avail:-0}" -lt 6000 ]; then echo "[$(ts)] skip: MemAvailable ${avail} MB < 6000" >> "$LOG"; exit 0; fi
SINCE=$(date -u -d "-${V15_BRAIN_SINCE_D:-14} days" +%Y-%m-%d)
TMP=$(mktemp -d /tmp/v15_brain_export.XXXX)
if nice -n 19 .venv/bin/python tools/v15_brain.py export --since "$SINCE" --out-dir "$TMP" >> "$LOG" 2>&1; then
    n=$(ls "$TMP"/brain_*.json 2>/dev/null | wc -l)
    if [ "$n" -ge 4 ]; then
        for f in "$TMP"/brain_*.json; do cp "$f" "data/brain/.$(basename "$f").tmp" && mv "data/brain/.$(basename "$f").tmp" "data/brain/$(basename "$f")"; done
        rsync -a --delay-updates -e "ssh -o BatchMode=yes -o ConnectTimeout=10" data/brain/brain_*.json 10.0.0.3:binance-sandbox/data/brain/ >> "$LOG" 2>&1 \
            && echo "[$(ts)] OK $n models since $SINCE -> S2 + S1" >> "$LOG" || echo "[$(ts)] WARN push to S1 failed" >> "$LOG"
    else
        echo "[$(ts)] FAIL only $n model files — kept previous models" >> "$LOG"
    fi
else
    echo "[$(ts)] FAIL export — kept previous models" >> "$LOG"
fi
rm -rf "$TMP"
exit 0
