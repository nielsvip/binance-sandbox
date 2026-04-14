#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# TRADIER REAL V5 SWEEP — ACTUAL process_position() results
# Every row = reproducible command with ALL parameters
# ═══════════════════════════════════════════════════════════════════

PY="$HOME/.conda/envs/binance_env/bin/python3"
[ -f "$PY" ] || PY="$HOME/miniconda3/envs/binance_env/bin/python3"
BASE="$HOME/binance"
RESULTS="$HOME/tradier_real_sweep"
CSV="$RESULTS/tradier_real_results.csv"
LOG="$RESULTS/sweep.log"

mkdir -p "$RESULTS/logs"

log() { echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') $1" | tee -a "$LOG"; }

# CSV header — EVERY parameter + results so you can reproduce
if [ ! -f "$CSV" ]; then
    echo "config_name,noloss,wt_exit_tfs,wt_exit_mode,wt_vel_threshold,entry_d_gate,ablation,extra_config,start_date,capital,account,total_trades,realized_pnl,unrealized_pnl,total_pnl,win_rate,wins,losses,opens,augments,sharpe,max_dd_pct,avg_win_pct,avg_loss_pct,ibs_exits,wt_exits,dc_exits,struct_exits,other_exits,open_positions,open_losers,elapsed_s,reproduce_command" > "$CSV"
fi

# Parse V5 full tradier output into CSV fields
parse_results() {
    local LOGFILE="$1" NAME="$2" CMD="$3"
    $PY -c "
import json, re, sys

logfile = '$LOGFILE'
lines = open(logfile).readlines()

# Parse trades from JSONL (find the trades file path from log)
trades_file = None
for l in lines:
    if 'Summary from' in l and 'trades in' in l:
        m = re.search(r'trades in (.+\.jsonl)', l)
        if m: trades_file = m.group(1)

trades = []
if trades_file:
    try:
        for l in open(trades_file):
            try: trades.append(json.loads(l))
            except: pass
    except: pass

closes = [t for t in trades if t.get('action') == 'CLOSE']
opens_t = [t for t in trades if t.get('action') == 'OPEN']
augments = [t for t in trades if t.get('action') == 'AUGMENT']
wins = [t for t in closes if t.get('gain', 0) > 0]
losses = [t for t in closes if t.get('gain', 0) <= 0]

realized = sum(t.get('pnl', 0) for t in closes)
wr = len(wins) / max(1, len(closes)) * 100
avg_w = sum(t.get('gain', 0) for t in wins) / max(1, len(wins))
avg_l = sum(t.get('gain', 0) for t in losses) / max(1, len(losses))

# Count exit reasons
reason_counts = {}
for t in closes:
    r = t.get('reason', '').split('(')[0].split('_')[0][:25]
    reason_counts[r] = reason_counts.get(r, 0) + 1
ibs = sum(v for k, v in reason_counts.items() if 'IBS' in k.upper())
wt = sum(v for k, v in reason_counts.items() if 'WT' in k.upper())
dc = sum(v for k, v in reason_counts.items() if 'DC' in k.upper())
struct = sum(v for k, v in reason_counts.items() if 'STRUCT' in k.upper())
other = len(closes) - ibs - wt - dc - struct

# Parse sharpe/dd from log output
sharpe = 0.0
max_dd = 0.0
unrealized = 0.0
open_pos = 0
open_losers = 0
for l in lines:
    if 'Sharpe:' in l:
        m = re.search(r'Sharpe: ([\d.-]+)', l)
        if m: sharpe = float(m.group(1))
        m = re.search(r'Max DD: ([\d.]+)', l)
        if m: max_dd = float(m.group(1))
    if 'Unrealized:' in l:
        m = re.search(r'Unrealized: \\\$([\d.-]+)', l)
        if m: unrealized = float(m.group(1))
    if 'Still open:' in l:
        m = re.search(r'Still open: (\d+)', l)
        if m: open_pos = int(m.group(1))
    if 'OPEN LOSERS' in l:
        m = re.search(r'OPEN LOSERS \((\d+)\)', l)
        if m: open_losers = int(m.group(1))

total_pnl = realized + unrealized

print(f'{len(closes)},{realized:.2f},{unrealized:.2f},{total_pnl:.2f},{wr:.1f},{len(wins)},{len(losses)},{len(opens_t)},{len(augments)},{sharpe:.3f},{max_dd:.1f},{avg_w:.3f},{avg_l:.3f},{ibs},{wt},{dc},{struct},{other},{open_pos},{open_losers}')
" 2>/dev/null
}

# ═══════════════════════════════════════════════════════════════════
# SWEEP GRID — focused on parameters that matter
# ═══════════════════════════════════════════════════════════════════

NOLOSS="0 0.5 1.0 2.0 3.0 5.0"
WT_TFS="off 4h D 4h,D 1h,4h,D"
WT_MODES="cross velocity"
WT_VELS="-2.0 -5.0 -8.0"
DGATES="0 1"
ABLATIONS="ALL WT_EXIT"
START="2024-06-01"
CAPITAL="70000"
ACCOUNT="trb"

TOTAL=0
for nl in $NOLOSS; do for wtf in $WT_TFS; do for wm in $WT_MODES; do
    if [ "$wtf" = "off" ] && [ "$wm" = "velocity" ]; then continue; fi
    if [ "$wm" = "cross" ]; then vels="0"; else vels="$WT_VELS"; fi
    for vel in $vels; do for dg in $DGATES; do for abl in $ABLATIONS; do
        TOTAL=$((TOTAL + 1))
    done; done; done
done; done; done
log "═══ TRADIER REAL V5 SWEEP — $TOTAL configs ═══"

DONE=0
T_START=$(date +%s)

for nl in $NOLOSS; do
for wtf in $WT_TFS; do
for wm in $WT_MODES; do
    # Skip velocity mode when WT is off
    if [ "$wtf" = "off" ] && [ "$wm" = "velocity" ]; then continue; fi
    # For cross mode, only one vel value
    if [ "$wm" = "cross" ]; then VLIST="0"; else VLIST="$WT_VELS"; fi
    for vel in $VLIST; do
    for dg in $DGATES; do
    for abl in $ABLATIONS; do
        NAME="nl${nl}_wt${wtf}_${wm}_v${vel}_dg${dg}_${abl}"
        NAME=$(echo "$NAME" | tr ',' '+')

        # Skip if already done
        grep -q "^${NAME}," "$CSV" 2>/dev/null && { DONE=$((DONE+1)); continue; }

        DONE=$((DONE + 1))
        LOGFILE="$RESULTS/logs/${NAME}.log"

        # Build the EXACT command
        CMD="$PY $BASE/backtest_v5_full_tradier.py --all --start $START --capital $CAPITAL --account $ACCOUNT --noloss $nl --ablation $abl"
        [ "$wtf" != "off" ] && CMD="$CMD --wt-exit-tfs $wtf --wt-exit-mode $wm"
        [ "$wm" != "cross" ] && [ "$wtf" != "off" ] && CMD="$CMD --wt-vel-threshold $vel"
        [ "$dg" = "1" ] && CMD="$CMD --entry-d-gate"

        log "  [$DONE/$TOTAL] $NAME — STARTING"
        T0=$(date +%s)

        # RUN THE REAL ENGINE
        eval $CMD > "$LOGFILE" 2>&1
        EXIT_CODE=$?
        T1=$(date +%s)
        ELAPSED=$((T1 - T0))

        # Parse results
        METRICS=$(parse_results "$LOGFILE" "$NAME" "$CMD")
        if [ -z "$METRICS" ]; then
            METRICS="0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0"
        fi

        # Escape command for CSV
        CMD_ESC=$(echo "$CMD" | sed 's/,/;/g')

        # Write CSV row
        echo "${NAME},${nl},${wtf},${wm},${vel},${dg},${abl},,${START},${CAPITAL},${ACCOUNT},${METRICS},${ELAPSED},${CMD_ESC}" >> "$CSV"

        # Parse quick stats for log
        TRADES=$(echo "$METRICS" | cut -d',' -f1)
        PNL=$(echo "$METRICS" | cut -d',' -f2)
        WR=$(echo "$METRICS" | cut -d',' -f5)
        SHARPE=$(echo "$METRICS" | cut -d',' -f10)

        NOW=$(date +%s)
        AVG=$(( (NOW - T_START) / DONE ))
        ETA=$(( (TOTAL - DONE) * AVG / 60 ))

        log "  [$DONE/$TOTAL] $NAME: Trades=$TRADES PnL=\$$PNL WR=$WR% Sharpe=$SHARPE (${ELAPSED}s) ETA=${ETA}m"

    done; done; done
done; done; done

log "═══ SWEEP COMPLETE — $DONE configs ═══"
log "Results: $CSV"
