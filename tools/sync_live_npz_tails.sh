#!/bin/bash
# tools/sync_live_npz_tails.sh — USER 2026-10-25: live follows what the latest
# NPZ bar on S1 dictates. S1 holds the canonical full indicator set; the Mac
# holds a partial mirror. Every 15m bar this pulls ONLY the traded syms'
# NPZ tails S1 -> Mac with rsync delta-transfer (appended bars = kilobytes,
# not the ~65MB files). The live engines' vec twin reloads stores on mtime,
# so the next per-bar decision runs on S1-identical rows — same inputs,
# same deterministic engine, same OPEN/CLOSED state, structurally.
# Enforcement of that state already lives in execute_now (BAR-STATE gate);
# this script removes the last input divergence that could make Mac disagree.
# Cron: 3,18,33,48 * * * * (just after bar close + keeper write; off the
# forward-parity 7,22,37,52 slots). Lock: lockf, skip if previous still runs.
set -u
ROOT="/Users/niels/Documents/binance"
cd "$ROOT" || exit 1
if [ -z "${NPZ_TAIL_SYNC_LOCKED:-}" ] && [ -x /usr/bin/lockf ]; then
  export NPZ_TAIL_SYNC_LOCKED=1
  /usr/bin/lockf -s -t 0 /tmp/live_npz_tail_sync.lock /bin/bash "$0" "$@" || exit 0
  exit 0
fi
LOG="/tmp/live_npz_tail_sync.log"
S1="s1-int"
REMOTE_DIR="~/binance-sandbox/backtest_v8/indicators"
LOCAL_DIR="backtest_v8/indicators"
# traded syms: open positions + recently filled + qualified lists (union)
SYMS=$(python3 - "$@" <<'EOF' 2>/dev/null
import json, glob, re, sys
syms = set()
for f in glob.glob("ang/long_positions.json") + glob.glob("*/long_positions.json") + glob.glob("*/short_positions.json"):
    try:
        d = json.load(open(f))
    except Exception:
        continue
    if isinstance(d, dict):
        for k, v in d.items():
            try:
                if abs(float((v or {}).get("positionAmt") or 0)) <= 1e-9:
                    continue
            except (TypeError, ValueError):
                continue
            s = str((v or {}).get("symbol") or "")
            if not s:
                parts = str(k).split(":")
                s = parts[1] if len(parts) > 1 else parts[0]
                s = re.sub(r"_(LONG|SHORT)$", "", s.upper())
            if s:
                syms.add(s.upper())
for qf in ("data/rankings_qualified_crypto.json", "data/rankings_qualified_stocks.json"):
    try:
        d = json.load(open(qf))
    except Exception:
        continue
    keys = d.keys() if isinstance(d, dict) else d
    for k in keys or []:
        m = re.match(r"(.+)_(LONG|SHORT)$", str(k).upper())
        if m:
            syms.add(m.group(1))
for f in glob.glob("data/exchange_fills/*/*.json"):
    m = re.match(r"(.+)\.json$", f.split("/")[-1])
    if m:
        syms.add(m.group(1).upper())
print(" ".join(sorted(syms)))
EOF
)
[ -z "$SYMS" ] && { echo "[$(date -u +%H:%M:%S)] no syms, skip" >> "$LOG"; exit 0; }
FILES=""
for s in $SYMS; do FILES="$FILES $REMOTE_DIR/$s.npz"; done
# shellcheck disable=SC2086
rsync -az --timeout=60 -e "ssh -S none -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10" $FILES "$LOCAL_DIR/" >> "$LOG" 2>&1
RC=$?
N=$(echo "$SYMS" | wc -w | tr -d " ")
echo "[$(date -u +%H:%M:%S)] synced tails rc=$RC nsyms=$N" >> "$LOG"
exit 0
