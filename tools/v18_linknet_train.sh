#!/bin/bash
# v18_linknet_train.sh — nightly retrain of the V18 generalising network (E8b). NO CRON IS INSTALLED; the user schedules it
# AFTER tools/v18_brain_export.sh (it reads the brain's spills, never re-extracts the corpus itself).
#
#   1. calibration of YESTERDAY's predictions first (before the model is replaced): a fresh incremental extract of the S2 run
#      logs with a COPY of the brain's watermarks (the brain's own watermark files are never touched), matched against the
#      current linknet_scores_<CS>.npz -> one row per cat_side appended to data/learn/linknet_calibration.jsonl
#      (predicted vs realised P(pos) of the template cells / top proposals the pilot actually evaluated since the scoring)
#   2. features  -> data/learn/features_<CS>.json (+ coverage report)
#   3. dataset   -> data/learn/linknet/ds_<CS>.npz   (one pass over the brain's spills)
#   4. train     -> holdout report + final model per cat_side (data/learn/linknet/linknet_{report,model}_<CS>.*)
#   5. score     -> data/learn/linknet_scores_<CS>.npz (Mac only, <= 500 MB) + linknet_proposals_<CS>.json (<= 10 MB)
#   6. digest    -> only with V18_LINKNET_DIGEST=1: python3 tools/v18_digest.py --apply (adds "proposals"/"proposed_rows")
# Every *_pred number it writes is a PREDICTION. Nothing here touches the pilot, engines, templates, configs, gate flags or crontabs.
#
# env: V18_BRAIN_SPILLS="data/brain_scratch/spill_mac data/brain_scratch/spill_s2"  V18_LINKNET_S2=s2  V18_LINKNET_NO_CALIB=1
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1
PY=$(bash tools/pyinterp.sh)
LN=data/learn/linknet
LOG=$LN/train_$(date -u +%Y%m%dT%H%MZ).log
mkdir -p $LN/tmp $LN/calib_spill
# corpus: the precision agent's per-cat_side events (brain event format + the chain value pinned per eval, cv/ck) when they
# exist, else the brain's own spills (in-force then inferred causally: last promotion on the sym_side, else template bold)
if [ -z "${V18_BRAIN_SPILLS:-}" ] && ls data/learn/precision/events_*.tsv.gz > /dev/null 2>&1; then
  mkdir -p $LN/prec_spill
  for f in data/learn/precision/events_*.tsv.gz; do cs=$(basename $f .tsv.gz); ln -sf "$ROOT/$f" $LN/prec_spill/spill_prec_${cs#events_}.tsv.gz; done
  SPILLS=$LN/prec_spill
else
  SPILLS=${V18_BRAIN_SPILLS:-"data/brain_scratch/spill_mac data/brain_scratch/spill_s2"}
fi
S2=${V18_LINKNET_S2:-s2}
log(){ echo "[$(date -u +%FT%TZ)] $*" | tee -a "$LOG"; }
free_gb=$(df -g . | tail -1 | awk '{print $4}')
if [ "${free_gb:-0}" -lt 10 ]; then log "ABORT: Mac has ${free_gb} GB free (< 10)"; exit 3; fi

# 1. calibration (yesterday's scores vs evals since)
if [ "${V18_LINKNET_NO_CALIB:-0}" != "1" ] && ls data/learn/linknet_scores_*.npz > /dev/null 2>&1; then
  s2free=$(ssh -o ConnectTimeout=10 "$S2" "df -BG --output=avail \$HOME | tail -1 | tr -dc 0-9" 2>/dev/null || echo 0)
  if [ "${s2free:-0}" -ge 5 ]; then
    ssh "$S2" "mkdir -p ~/v18_scratch/e8b/code/tools ~/v18_scratch/e8b/spill_calib && touch ~/v18_scratch/e8b/code/tools/__init__.py && rm -f ~/v18_scratch/e8b/spill_calib/spill_*.tsv.gz ~/v18_scratch/e8b/spill_calib/extract_*.json"
    rsync -a tools/v18_brain.py "$S2":v18_scratch/e8b/code/tools/
    # the watermark copy advances on S2 only inside e8b; seed it from the brain's Mac copy the first time
    ssh "$S2" "test -f ~/v18_scratch/e8b/spill_calib/watermarks_s2run33.json" || rsync -a data/brain_scratch/spill_s2/watermarks_s2run33.json "$S2":v18_scratch/e8b/spill_calib/
    ssh "$S2" "cd ~/v18_scratch/e8b/code && nice -n 10 \$(bash ~/binance-sandbox/tools/pyinterp.sh) tools/v18_brain.py extract --source logs --from \"\$HOME/v15_run33_20261019/progress/v15_delta_log/*.jsonl\" --host s2run33 --spill-dir ~/v18_scratch/e8b/spill_calib > ~/v18_scratch/e8b/extract_calib.log 2>&1" \
      && rm -f $LN/calib_spill/spill_*.tsv.gz && rsync -a "$S2":v18_scratch/e8b/spill_calib/spill_*.tsv.gz "$S2":v18_scratch/e8b/spill_calib/extract_*.json $LN/calib_spill/ \
      && $PY tools/v18_linknet.py calibrate --spill-dir $LN/calib_spill --tmp-dir $LN/tmp --note "nightly: S2 run33 evals after the previous scoring" >> "$LOG" 2>&1
    log "calibration rc=$?"
  else
    log "calibration skipped: S2 has ${s2free} GB free (< 5)"
  fi
fi

# 2-5. features, dataset, train, score
SP=""; for d in $SPILLS; do SP="$SP --spill-dir $d"; done
/usr/bin/time -l nice -n 10 $PY tools/v18_features.py build >> "$LOG" 2>&1 || { log "features FAILED"; exit 2; }
/usr/bin/time -l nice -n 10 $PY tools/v18_linknet.py dataset $SP --tmp-dir $LN/tmp --out-dir $LN >> "$LOG" 2>&1 || { log "dataset FAILED"; exit 2; }
/usr/bin/time -l nice -n 10 $PY tools/v18_linknet.py train --max-epochs ${V18_LINKNET_EPOCHS:-20} >> "$LOG" 2>&1 || { log "train FAILED"; exit 2; }
/usr/bin/time -l nice -n 10 $PY tools/v18_linknet.py score >> "$LOG" 2>&1 || { log "score FAILED"; exit 2; }
for cs in CRYPTO_LONG CRYPTO_SHORT STOCKS_LONG STOCKS_SHORT; do
  f=data/learn/linknet_scores_$cs.npz; s=$(stat -f %z "$f" 2>/dev/null || echo 0)
  [ "$s" -gt 500000000 ] && log "WARN $f is $s bytes (> 500 MB)"
done
if [ "${V18_LINKNET_DIGEST:-0}" = "1" ]; then
  $PY tools/v18_digest.py --apply >> "$LOG" 2>&1; log "digest rc=$?"
fi
log "DONE"
