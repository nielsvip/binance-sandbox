#!/bin/bash
# v18_brain_export.sh — daily rebuild of the V18 BRAIN (docs/v15_pending/19_E8_BRAIN_HANDOVER_20261020.md). Runs on the MAC
# (the ext drives, /history and the experience store are Mac-only). NO cron is installed by this script — the user schedules it.
# tools/v15_brain_export.sh (S2 cron 03:20, V15 brain) is untouched and independent.
#
#   1. S2: ship tools/v18_brain.py to ~/v18_scratch/e8/code (scratch, NOT the sandbox), extract every ~/v15_run*/ delta log
#      (watermarked, gz spills, nice 10, disk guard) -> pull the spills to the Mac -> delete them on S2.
#   2. S1 (read-only): rsync the run delta logs into data/brain_scratch/s1_logs/ and extract them on the Mac.
#   3. Mac: SSD2T archived run trees (watermarked), /history lifecycles, replayed ledgers, experience-store native quartets
#      (snapshot sources: their previous spills are replaced).
#   4. Mac: train (full prequential pass over every spill, --cut = now - V18_CUT_DAYS) -> data/brain/.v18_next/ -> atomic move of the
#      four data/brain/v18_brain_<CAT_SIDE>.json when all four exist and are <= 10 MB. Previous models kept on any failure.
#   5. Push to S1/S2 data/brain only when V18_BRAIN_PUSH=1 (the files are inert until a gated pilot reads them with V18_BRAIN=1).
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT" || exit 1
PY=${V18_PY:-python3}
SCR=$ROOT/data/brain_scratch
LOG=$SCR/v18_brain_export.log
CUT_DAYS=${V18_CUT_DAYS:-3}
S1=${V18_S1:-s1-int}
S2=${V18_S2:-s2}
S1_GLOB=${V18_S1_GLOB:-'v15_run*/progress/v15_delta_log'}
mkdir -p "$SCR"/spill_mac "$SCR"/spill_s2 "$SCR"/s1_logs data/brain
ts(){ date -u +%FT%TZ; }
log(){ echo "[$(ts)] $*" | tee -a "$LOG"; }
free_gb(){ df -g "$1" | tail -1 | awk '{print $4}'; }
if [ "$(free_gb "$ROOT")" -lt 8 ]; then log "skip: Mac has < 8 GB free"; exit 0; fi

# 1. S2 extraction
if scp -q tools/v18_brain.py "$S2":v18_scratch/e8/code/tools/v18_brain.py 2>>"$LOG"; then
    ssh "$S2" 'set -u; f=$(df -BG --output=avail $HOME | tail -1 | tr -dc 0-9); [ "$f" -lt 6 ] && { echo "S2 disk ${f}G — skipped"; exit 0; };
      cd ~/v18_scratch/e8/code && for d in ~/v15_run*/progress/v15_delta_log; do
        r=$(basename $(dirname $(dirname $d)));
        nice -n 10 ~/binance-sandbox/.venv/bin/python tools/v18_brain.py extract --source logs --from "$d/*.jsonl" --host s2_$r --spill-dir ~/v18_scratch/e8/spill --ctx-map ~/v18_scratch/e8/spill/ctxmap_s2.json > ~/logs/v18_brain_extract_$r.log 2>&1 || echo "FAIL $r";
      done' >>"$LOG" 2>&1
    rsync -a --remove-source-files "$S2":v18_scratch/e8/spill/'spill_*.tsv.gz' "$SCR"/spill_s2/ >>"$LOG" 2>&1 || log "WARN pull of S2 spills failed (kept on S2)"
    rsync -a "$S2":v18_scratch/e8/spill/'extract_*.json' "$SCR"/spill_s2/ >>"$LOG" 2>&1
    ssh "$S2" 'rm -f ~/v18_scratch/e8/spill/extract_*.json' >>"$LOG" 2>&1
else
    log "WARN S2 unreachable — training on the spills already on the Mac"
fi

# 2. S1 (read-only pull, retried: the gateway drops long transfers)
for i in 1 2 3 4 5; do
    timeout 600 rsync -aR --partial "$S1":"$S1_GLOB/" "$SCR"/s1_logs/ >>"$LOG" 2>&1 && break
done
nice -n 10 $PY tools/v18_brain.py extract --source logs --from "$SCR/s1_logs/*/progress/v15_delta_log/*.jsonl" --host s1 --spill-dir "$SCR"/spill_mac >>"$LOG" 2>&1 || log "WARN S1 extract failed"

# 3. Mac sources
if [ -d /Volumes/SSD2T/s1_offload ]; then
    nice -n 10 $PY tools/v18_brain.py extract --source logs --from '/Volumes/SSD2T/s1_offload/*/v15_run*/progress/v15_delta_log/*.jsonl' --host s1arch --spill-dir "$SCR"/spill_mac >>"$LOG" 2>&1
fi
for src in history ledgers native; do
    old=$(ls "$SCR"/spill_mac/spill_${src}_*.tsv.gz 2>/dev/null)
    case $src in
        history) args="--source history" ;;
        ledgers) args="--source ledgers --from data/learn/trade_ledgers --sets data/learn/replay_sets.jsonl.gz" ;;
        native)  args="--source native --from data/experience" ;;
    esac
    if nice -n 10 $PY tools/v18_brain.py extract $args --host mac --spill-dir "$SCR"/spill_mac >>"$LOG" 2>&1; then
        [ -n "$old" ] && rm -f $old
    else
        log "WARN $src extract failed — previous spill kept"
    fi
done

# 4. train + export
CUT=$(date -u -v-${CUT_DAYS}d +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d "-${CUT_DAYS} days" +%Y-%m-%dT%H:%M:%SZ)
NEXT=data/brain/.v18_next
rm -rf "$NEXT"; mkdir -p "$NEXT"
if /usr/bin/time -l nice -n 10 $PY tools/v18_brain.py train --spill-dir "$SCR"/spill_mac --spill-dir "$SCR"/spill_s2 --tmp-dir "$SCR"/tmp --cut "$CUT" \
       --state "$SCR"/v18_brain_state.pkl --out-dir "$NEXT" --report "$SCR"/v18_brain_report.json >>"$LOG" 2>&1; then
    n=$(ls "$NEXT"/v18_brain_*.json 2>/dev/null | wc -l | tr -d ' ')
    big=$(find "$NEXT" -name 'v18_brain_*.json' -size +10000k | wc -l | tr -d ' ')
    if [ "$n" -ge 4 ] && [ "$big" -eq 0 ]; then
        for f in "$NEXT"/v18_brain_*.json; do mv "$f" data/brain/; done
        log "OK $n models (cut $CUT) -> data/brain"
        if [ "${V18_BRAIN_PUSH:-0}" = "1" ]; then
            for h in "$S1" "$S2"; do rsync -a --delay-updates data/brain/v18_brain_*.json "$h":binance-sandbox/data/brain/ >>"$LOG" 2>&1 || log "WARN push $h failed"; done
        fi
    else
        log "FAIL $n model files / $big over 10 MB — kept previous models"
    fi
else
    log "FAIL train — kept previous models"
fi
rm -rf "$NEXT"
exit 0
