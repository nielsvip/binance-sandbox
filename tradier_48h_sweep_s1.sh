#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# TRADIER 48-HOUR AUTONOMOUS SWEEP — REAL V5 ENGINE
# Runs in screen, lockfile protected, self-restarting
# DO NOT KILL — check /home/niels/SWEEP_RUNNING before touching
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

PY="$HOME/.conda/envs/binance_env/bin/python3"
[ -f "$PY" ] || PY="$HOME/miniconda3/envs/binance_env/bin/python3"
BASE="$HOME/binance"
DIR="$HOME/tradier_48h_sweep"
CSV="$DIR/results.csv"
LOG="$DIR/sweep.log"
LOCK="$HOME/SWEEP_RUNNING"
TRADES_DIR="$HOME/binance-sandbox/backtest_v5/logs"

mkdir -p "$DIR/logs"

# Lockfile — other scripts must check this
echo "TRADIER 48H SWEEP — started $(date -u) — PID $$ — DO NOT KILL" > "$LOCK"
trap "rm -f $LOCK" EXIT

log() { echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') $1" | tee -a "$LOG"; }

# CSV header
if [ ! -f "$CSV" ]; then
    echo "config_name,noloss,wt_exit_tfs,wt_exit_mode,wt_vel_threshold,entry_d_gate,ablation,start_date,total_trades,realized_pnl,win_rate,wins,losses,opens,augments,sharpe,max_dd_pct,avg_win_pct,avg_loss_pct,ibs_exits,wt_exits,dc_exits,struct_exits,other_exits,open_positions,elapsed_s,reproduce_command" > "$CSV"
fi

# Parse results from JSONL trade file directly
parse_trades() {
    local LOGFILE="$1"
    $PY << PYEOF
import json, re, sys, glob, os
from collections import defaultdict
import numpy as np

# Find the trades JSONL from the engine log
logfile = "$LOGFILE"
lines = open(logfile).readlines()
trades_file = None
for l in lines:
    if '.jsonl' in l and ('Writing' in l or 'trades in' in l or 'output' in l.lower()):
        m = re.search(r'(/[^\s]+\.jsonl)', l)
        if m: trades_file = m.group(1)
# Also try finding the most recent JSONL in logs dir
if not trades_file or not os.path.exists(trades_file):
    pattern = "$TRADES_DIR/full_*_trb_*.jsonl"
    files = sorted(glob.glob(pattern), key=os.path.getmtime)
    if files:
        trades_file = files[-1]
if not trades_file or not os.path.exists(trades_file):
    print("0,0.00,0.0,0,0,0,0,0.000,0.0,0.000,0.000,0,0,0,0,0,0")
    sys.exit(0)

trades = []
for l in open(trades_file):
    try: trades.append(json.loads(l))
    except: pass

closes = [t for t in trades if t.get('action') == 'CLOSE']
opens_t = [t for t in trades if t.get('action') == 'OPEN']
augments = [t for t in trades if t.get('action') == 'AUGMENT']
wins = [t for t in closes if t.get('gain', 0) > 0]
losses = [t for t in closes if t.get('gain', 0) <= 0]
realized = sum(t.get('pnl', 0) for t in closes)
wr = len(wins) / max(1, len(closes)) * 100
avg_w = np.mean([t.get('gain', 0) for t in wins]) if wins else 0
avg_l = np.mean([t.get('gain', 0) for t in losses]) if losses else 0

# Exit reasons
rc = defaultdict(int)
for t in closes:
    r = t.get('reason', '').upper()
    if 'IBS' in r: rc['IBS'] += 1
    elif 'WT' in r: rc['WT'] += 1
    elif 'DC' in r: rc['DC'] += 1
    elif 'STRUCT' in r: rc['STRUCT'] += 1
    else: rc['OTHER'] += 1

# Sharpe from log
sharpe = 0.0; max_dd = 0.0
for l in lines:
    if 'Sharpe:' in l:
        m = re.search(r'Sharpe: ([\d.-]+)', l)
        if m: sharpe = float(m.group(1))
        m = re.search(r'Max DD: ([\d.]+)', l)
        if m: max_dd = float(m.group(1))

# Open positions from log
open_pos = 0
for l in lines:
    if 'Still open:' in l:
        m = re.search(r'Still open: (\d+)', l)
        if m: open_pos = int(m.group(1))

print(f"{len(closes)},{realized:.2f},{wr:.1f},{len(wins)},{len(losses)},{len(opens_t)},{len(augments)},{sharpe:.3f},{max_dd:.1f},{avg_w:.3f},{avg_l:.3f},{rc.get('IBS',0)},{rc.get('WT',0)},{rc.get('DC',0)},{rc.get('STRUCT',0)},{rc.get('OTHER',0)},{open_pos}")
PYEOF
}

# ═══ SWEEP GRID ═══
NOLOSS="0.5 2.0 5.0"
WT_TFS="off 4h D 4h,D 1h,4h,D"
WT_MODES="cross velocity"
WT_VELS="-2.0 -5.0 -8.0"
DGATES="0 1"
ABLATIONS="ALL WT_EXIT"
START="2024-06-01"

TOTAL=0
for nl in $NOLOSS; do for wtf in $WT_TFS; do for wm in $WT_MODES; do
    if [ "$wtf" = "off" ] && [ "$wm" = "velocity" ]; then continue; fi
    if [ "$wm" = "cross" ]; then vels="0"; else vels="$WT_VELS"; fi
    for vel in $vels; do for dg in $DGATES; do for abl in $ABLATIONS; do
        TOTAL=$((TOTAL + 1))
    done; done; done
done; done; done

log "═══ TRADIER 48H SWEEP — $TOTAL configs — $(hostname) ═══"

DONE=0
T_START=$(date +%s)

for nl in $NOLOSS; do
for wtf in $WT_TFS; do
for wm in $WT_MODES; do
    [ "$wtf" = "off" ] && [ "$wm" = "velocity" ] && continue
    [ "$wm" = "cross" ] && VLIST="0" || VLIST="$WT_VELS"
    for vel in $VLIST; do
    for dg in $DGATES; do
    for abl in $ABLATIONS; do
        NAME="nl${nl}_wt${wtf}_${wm}_v${vel}_dg${dg}_${abl}"
        NAME=$(echo "$NAME" | tr ',' '+')

        # Skip done
        grep -q "^${NAME}," "$CSV" 2>/dev/null && { DONE=$((DONE+1)); continue; }

        DONE=$((DONE + 1))
        LF="$DIR/logs/${NAME}.log"

        # Build command
        CMD="$PY $BASE/backtest_v5_full_tradier.py --all --start $START --capital 70000 --account trb --noloss $nl --ablation $abl"
        [ "$wtf" != "off" ] && CMD="$CMD --wt-exit-tfs $wtf --wt-exit-mode $wm"
        [ "$wm" != "cross" ] && [ "$wtf" != "off" ] && CMD="$CMD --wt-vel-threshold $vel"
        [ "$dg" = "1" ] && CMD="$CMD --entry-d-gate"

        log "[$DONE/$TOTAL] $NAME — START"
        echo "[$DONE/$TOTAL] $NAME" > "$LOCK"
        T0=$(date +%s)

        # RUN with timeout (max 30 min per config)
        timeout 1800 bash -c "$CMD" > "$LF" 2>&1 || true
        T1=$(date +%s)
        ELAPSED=$((T1 - T0))

        # Parse
        METRICS=$(parse_trades "$LF" 2>/dev/null)
        [ -z "$METRICS" ] && METRICS="0,0.00,0.0,0,0,0,0,0.000,0.0,0.000,0.000,0,0,0,0,0,0"

        CMD_ESC=$(echo "$CMD" | tr ',' ';')
        echo "${NAME},${nl},${wtf},${wm},${vel},${dg},${abl},${START},${METRICS},${ELAPSED},${CMD_ESC}" >> "$CSV"

        TRADES=$(echo "$METRICS" | cut -d',' -f1)
        PNL=$(echo "$METRICS" | cut -d',' -f2)
        WR=$(echo "$METRICS" | cut -d',' -f3)
        SHARPE=$(echo "$METRICS" | cut -d',' -f8)

        NOW=$(date +%s)
        ELAPSED_TOTAL=$((NOW - T_START))
        [ $DONE -gt 0 ] && AVG=$((ELAPSED_TOTAL / DONE)) || AVG=600
        ETA=$(( (TOTAL - DONE) * AVG / 60 ))

        log "[$DONE/$TOTAL] $NAME: Trades=$TRADES PnL=\$$PNL WR=${WR}% Sharpe=$SHARPE (${ELAPSED}s) ETA=${ETA}m"

    done; done; done
done; done; done

log "═══ SWEEP COMPLETE — $DONE/$TOTAL ═══"
rm -f "$LOCK"
