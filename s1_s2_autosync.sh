#!/usr/bin/env bash
# s1_s2_autosync.sh — MacBook → S1 + S2 one-way script sync, checksum-based.
#
# User directive 2026-04-18: "ANY CHANGE HAPPENS ON ALL 3 MACHINES SIMULTANEOUSLY."
# 2026-04-19 update: "Trading scripts AND config should be auto-synced continuously
# (like market data). Monitors should focus on test progress, not code version."
#
# MacBook `/Users/niels/Documents/binance` is the AUTHORITY for every file.
# S1 `s1-int:/home/niels/binance-sandbox/` and S2 `s2-int:/home/niels/binance-sandbox/`
# must be bit-identical for every ez_*, tradier_*, wt_*, v8_*, backtest_v8_*,
# utils.py, symbols.json, breakout_multi_lung.py, config.py, config_tradier.py.
#
# Sweeps use V8_OVERRIDE_FILE overlays — they NEVER mutate config.py on sandboxes,
# so there's no reason for configs to diverge. Auto-syncing them eliminates the
# entire "sweep ran on stale config" bug class.
#
# EXCLUDED from sync (by design):
#   - *.pyc, __pycache__             (compiled junk)
#   - any .log / .json / .csv data files (not in the script globs anyway)
#   - per-account realtime stubs (MacBook-only)
#
# Push semantics:
#   rsync --checksum  → MacBook version wins on any md5 mismatch (regardless of mtime,
#                       so a stray server-side edit can't block the push by being newer).
#   rsync --existing  → don't CREATE new files on sandbox (prevents re-adding archived
#                       MacBook files per CLAUDE.md rule). New MacBook scripts need a
#                       one-time manual push to seed.
#
# Runs under launchd (com.niels.s1-s2-autosync, KeepAlive=true, 60s tick).
# Logs: /tmp/s1_s2_autosync.log. Kill via: pkill -f s1_s2_autosync
set -u

BASE=/Users/niels/Documents/binance
LOG=/tmp/s1_s2_autosync.log
# ControlMaster=no + ControlPath=none → bypass interactive-session sockets that launchd can't reach.
SSH_OPTS="-4 -i /Users/niels/.ssh/id_ed25519 -p 2201 -o ConnectTimeout=20 -o BatchMode=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=4 -o ControlMaster=no -o ControlPath=none"
S1_HOST=niels@127.0.0.1
DEST_PATH=/home/niels/binance-sandbox/
SLEEP_SEC=30  # serialized with the verified result transaction; avoids SSH banner floods
PAUSE_FILE="$BASE/.s1_s2_autosync.pause"
TRANSPORT_LOCK="$BASE/data/sync/.s1_transport.lock"
VERIFIED_SYNC_REQUEST="$BASE/data/sync/ALWAYS_CONNECTED_SYNC_REQUEST"
mkdir -p "$BASE/data/sync"
trap 'rmdir "$TRANSPORT_LOCK" 2>/dev/null || true' EXIT TERM INT

cd "$BASE" || { echo "$(date -u +%FT%TZ) CANNOT_CD $BASE" >>"$LOG"; exit 1; }
shopt -s nullglob

push_to() {
    local host="$1" label="$2"
    cd "$BASE" || return 1
    # Build file list once per tick (bash 3.2 compatible, no mapfile).
    local files=()
    # Skip per-account realtime stubs (MacBook-only, not on sandboxes).
    local skip_list=" ez_positions_realtime_ang.py ez_positions_realtime_fin.py ez_positions_realtime_flz.py ez_positions_realtime_inf.py ez_positions_realtime_men.py "
    for pat in ez_*.py tradier_*.py wt_*.py v8_*.py backtest_v8_*.py per_sym_*.py utils.py symbols.json breakout_multi_lung.py config.py config_tradier.py; do
        for f in $pat; do
            [[ -f "$f" ]] || continue
            [[ "$skip_list" == *" $f "* ]] && continue
            files+=("$f")
        done
    done
    [[ ${#files[@]:-0} -eq 0 ]] && return 1
    # Fetch all remote md5s in ONE ssh call (cheap).
    local remote_md5s
    remote_md5s=$(ssh $SSH_OPTS "${host}" "cd ${DEST_PATH} 2>/dev/null && md5sum ${files[*]} 2>/dev/null" 2>>"$LOG")
    if [[ -z "$remote_md5s" ]]; then
        echo "$(date -u +%FT%TZ) ${label} SSH_MD5_FETCH_FAIL — skipping tick" >>"$LOG"
        return 1
    fi
    # Diff: any file where local md5 != remote md5 goes to push list.
    local push_list=()
    local f local_md5 remote_md5
    for f in "${files[@]}"; do
        local_md5=$(md5 -q "$f" 2>/dev/null)
        [[ -z "$local_md5" ]] && continue
        remote_md5=$(echo "$remote_md5s" | awk -v f="$f" '$NF==f {print $1; exit}')
        # If remote md5 empty → file doesn't exist on sandbox → respect --existing semantic, skip.
        [[ -z "$remote_md5" ]] && continue
        [[ "$local_md5" != "$remote_md5" ]] && push_list+=("$f")
    done
    [[ ${#push_list[@]:-0} -eq 0 ]] && return 0  # no drift
    # Push only the drifted files, one rsync call (short arg list, no 3.4.1 abort).
    local n=${#push_list[@]}
    echo "$(date -u +%FT%TZ) ${label} DRIFT_PUSH ${n} file(s): ${push_list[*]:0:8}$([ $n -gt 8 ] && echo ' ...' )" >>"$LOG"
    rsync -a --checksum --existing \
        -e "ssh $SSH_OPTS" \
        "${push_list[@]}" "${host}:${DEST_PATH}" 2>>"$LOG"
    local rc=$?
    if [[ $rc -ne 0 ]]; then
        echo "$(date -u +%FT%TZ) ${label} RSYNC_FAIL rc=$rc files=${push_list[*]:0:4}" >>"$LOG"
        return 1
    fi
    echo "$(date -u +%FT%TZ) ${label} PUSHED_OK ${n} file(s)" >>"$LOG"
    return 0
}

PER_SYM_CFG_S1="$S1_HOST:/home/niels/binance-sandbox/data/hourly_reconfig/per_sym_active_config.json"
PER_SYM_CFG_S2="s2-int:/home/niels/binance-sandbox/data/hourly_reconfig/per_sym_active_config.json"
PER_SYM_CFG_MAC="$BASE/data/hourly_reconfig/per_sym_active_config.json"
S1_PLOTS="$S1_HOST:/home/niels/binance-sandbox/plots/"
MAC_PLOTS="$BASE/plots/"

_merge_persym() {
    # 2026-08-21 USER: baseline preserved (GOOD 410 keys), new 12000 switches best results added constantly WITHOUT losing baseline — additive improver-only merge.
    # S1 holds full 12000-switch beam candidates; Mac holds baseline. Merge only if S1 entry is strictly better and passes gates, else keep Mac baseline.
    local mac_path="${PER_SYM_CFG_MAC:-$BASE/data/hourly_reconfig/per_sym_active_config.json}"
    local s1_tmp=/tmp/per_sym_s1.json
    rsync -az --timeout=10 -e "ssh $SSH_OPTS" "$PER_SYM_CFG_S1" "$s1_tmp" 2>>"$LOG"
    /opt/anaconda3/envs/binance_env/bin/python3 - <<'PYEOF' && echo "$(date -u +%FT%TZ) PER_SYM_CFG merged S1→Mac (baseline preserved, 12000 improvers only)" >>"$LOG"
import json, os, sys
from pathlib import Path
s1 = json.load(open('/tmp/per_sym_s1.json')) if os.path.exists('/tmp/per_sym_s1.json') else {}
if not s1:
    print('s1 empty/missing — keeping existing Mac config (no write)', flush=True)
    sys.exit(0)
mac_path = Path(os.environ.get('PER_SYM_CFG_MAC', '/tmp/per_sym_merged.json'))
# Load Mac baseline (GOOD 410 keys) — preserved if S1 not better
try:
    mac = json.loads(mac_path.read_text()) if mac_path.exists() else {}
except Exception:
    mac = {}
# Gates for 12000-switch promotion: additive improver only
MIN_TRADES = 30
MIN_WSHARPE = 0.0
MIN_GAIN = 5.0
def is_promotable(e):
    if not isinstance(e, dict): return False, "not dict"
    if "PER_SYM_SIDE_DISABLED" in str(e.get("winning_tag","")): return True, "disable_entry"
    try: ws = float(e.get("wsharpe"))
    except: return False, "wsharpe NA"
    if ws <= MIN_WSHARPE: return False, f"wsharpe {ws} <= {MIN_WSHARPE}"
    g = e.get("total_pnl_pct")
    if g is None:
        rr = e.get("raw_returns")
        if isinstance(rr, list) and rr:
            try: g = sum(float(x) for x in rr)
            except: g = None
    if g is not None and float(g) < MIN_GAIN: return False, f"gain {g} < {MIN_GAIN}"
    tr = e.get("trades")
    if tr is not None and int(tr) < MIN_TRADES: return False, f"trades {tr} < {MIN_TRADES}"
    return True, f"ws {ws:.3f}"
# Additive merge: start from Mac baseline, apply S1 only if improver
merged = dict(mac)
# Preserve _meta baseline info
if "_meta" not in merged and "_meta" in s1:
    merged["_meta"] = s1["_meta"]
improved = 0
added = 0
blocked = 0
for k, v in s1.items():
    if k.startswith("_"): continue
    if not isinstance(v, dict): continue
    ok, reason = is_promotable(v)
    if not ok:
        blocked += 1
        continue
    live = merged.get(k)
    if live is None:
        merged[k] = v
        added += 1
    else:
        try:
            live_ws = float(live.get("wsharpe", -999))
            s1_ws = float(v.get("wsharpe", -999))
            if s1_ws > live_ws:
                merged[k] = v
                improved += 1
            else:
                blocked += 1
        except Exception:
            blocked += 1
# Never delete baseline keys not in S1 — 410 baseline preserved
out = Path(os.environ.get('PER_SYM_CFG_MAC', '/tmp/per_sym_merged.json'))
tmp = out.with_suffix('.tmp')
with open(tmp, 'w') as f:
    json.dump(merged, f, indent=2, default=str)
tmp.replace(out)
print(f'merged {len([k for k in merged if not k.startswith("_")])} total ({added} new, {improved} improved, {blocked} blocked/fake) — baseline preserved, S1 12000 best added', flush=True)
PYEOF
}

echo "$(date -u +%FT%TZ) autosync_start pid=$$ mode=checksum_macbook_authoritative interval=${SLEEP_SEC}s" >>"$LOG"
_pull_tick=0
_chart_tick=0
_matrix_tick=0
while true; do
    # A verified transaction clears its edge-trigger request on PASS. Repairs
    # discovered while that transaction is already frozen are queued under a
    # distinct name; after launchd reloads this loop, promote that request and
    # execute one more complete verified transaction instead of losing it.
    if [[ ! -e "$VERIFIED_SYNC_REQUEST" \
          && -e "$BASE/data/sync/ALWAYS_CONNECTED_SYNC_REQUEST_NEXT" ]]; then
        mv "$BASE/data/sync/ALWAYS_CONNECTED_SYNC_REQUEST_NEXT" "$VERIFIED_SYNC_REQUEST"
    fi
    # Bible §16.22 verified source/result synchronization supersedes this
    # legacy narrow script/config pusher. This already-loaded launchd daemon
    # owns the handoff so a request is not stranded when the optional
    # mac-live-heartbeat agent is unavailable.
    if [[ -e "$VERIFIED_SYNC_REQUEST" ]]; then
        rmdir "$TRANSPORT_LOCK" 2>/dev/null || true
        echo "$(date -u +%FT%TZ) VERIFIED_SYNC_HANDOFF request=$VERIFIED_SYNC_REQUEST" >>"$LOG"
        /bin/bash "$BASE/tools/always_connected_sync.sh" >>"$LOG" 2>&1 || true
        sleep 5
        continue
    fi
    if ! mkdir "$TRANSPORT_LOCK" 2>/dev/null; then
        sleep "$SLEEP_SEC"
        continue
    fi
    if [[ -f "$PAUSE_FILE" ]]; then
        if [[ "${_push_pause_logged:-0}" -eq 0 ]]; then
            echo "$(date -u +%FT%TZ) CODE_PUSH_PAUSED flag=$PAUSE_FILE" >>"$LOG"
            _push_pause_logged=1
        fi
    else
        _push_pause_logged=0
        push_to "$S1_HOST" S1
    fi
    # Release the push lane before either puller acquires its own transport
    # lock.  Holding this lock here used to make pull_r5_vector_results.sh
    # return TRANSPORT_BUSY on every tick, leaving a stale Mac view.
    rmdir "$TRANSPORT_LOCK" 2>/dev/null || true
    # Pull the compact coupled-vector truth every tick.  Raw receipts, NPZs,
    # databases and event ledgers remain on S1; these two small files are the
    # operator surface on Mac and must never present an old campaign view.
    # Public S1 is an operator-authorized fallback when localhost:2201 stalls.
    # This helper transfers only the compact JSON/Markdown pair and validates
    # the campaign/schema before atomically replacing the Mac view.
    /bin/bash "$BASE/tools/pull_r5_vector_results.sh" --once >>"$LOG" 2>&1 || true
    # Pull one immutable S1 snapshot of the operator truth surface.  The
    # helper allowlists compact reports and SWITCH_MATRIX_TRB.xlsx only, then
    # hash-verifies before atomic Mac promotion; NPZ/DB/archive payloads stay
    # on S1.  Its 30-minute digest is the commit marker and counts accepted
    # canonical cells only, never raw result rows.
    /bin/bash "$BASE/tools/pull_s1_compact_truth_surface.sh" --once >>"$LOG" 2>&1 || true
    # 2026-05-28 S2 DEAD permanently — push_to s2-int removed
    # Sync per_sym_active_config.json from S1 every ~60 s (S2 DEAD permanently 2026-05-28)
    _pull_tick=$(( (_pull_tick + 1) % 30 ))
    if [[ $_pull_tick -eq 0 ]]; then
        mkdir -p "$BASE/data/hourly_reconfig"
        export PER_SYM_CFG_MAC
        _merge_persym
    fi
    # Pull _trade_lists/ from S1 every ~60 s (per_sym_20d_agent writes exact BT trades)
    if [[ $_pull_tick -eq 0 ]]; then
        for _acct in trb trc; do
            mkdir -p "$BASE/data/hourly_reconfig/$_acct/_trade_lists"
            rsync -az --timeout=20 -e "ssh $SSH_OPTS" \
                "$S1_HOST:/home/niels/binance/data/hourly_reconfig/$_acct/_trade_lists/" \
                "$BASE/data/hourly_reconfig/$_acct/_trade_lists/" 2>>"$LOG"
        done
    fi
    # Pull CRYPTO matrix + beam ledger + BTC beam log every ~30s so Mac sees S1 progress live (user 2026-08-21)
    _matrix_tick=$(( (_matrix_tick + 1) % 15 ))
    if [[ $_matrix_tick -eq 0 ]]; then
        mkdir -p "$BASE/SPREADSHEETS" "$BASE/data/reports/gui_lab"
        rsync -az --timeout=30 -e "ssh $SSH_OPTS" "$S1_HOST:/home/niels/binance-sandbox/SPREADSHEETS/CRYPTO_1YR_REAL_MATRIX_NEXT_GEN.xlsx" "$BASE/SPREADSHEETS/CRYPTO_1YR_REAL_MATRIX_NEXT_GEN.xlsx" 2>>"$LOG" && echo "$(date -u +%FT%TZ) CRYPTO_MATRIX pulled S1→Mac" >>"$LOG" || true
        rsync -az --timeout=30 -e "ssh $SSH_OPTS" "$S1_HOST:/home/niels/binance-sandbox/SPREADSHEETS/CRYPTO_1YR_REAL_MATRIX_NEXT_GEN.csv" "$BASE/SPREADSHEETS/CRYPTO_1YR_REAL_MATRIX_NEXT_GEN.csv" 2>>"$LOG" || true
        rsync -az --timeout=30 -e "ssh $SSH_OPTS" "$S1_HOST:/home/niels/binance-sandbox/data/reports/gui_lab/next_gen_beam_crypto.json" "$BASE/data/reports/gui_lab/next_gen_beam_crypto.json" 2>>"$LOG" || true
        rsync -az --timeout=30 -e "ssh $SSH_OPTS" "$S1_HOST:/home/niels/binance-sandbox/data/reports/gui_lab/next_gen_beam_per_sym.json" "$BASE/data/reports/gui_lab/next_gen_beam_per_sym.json" 2>>"$LOG" || true
        rsync -az --timeout=30 -e "ssh $SSH_OPTS" "$S1_HOST:/home/niels/binance-sandbox/data/reports/gui_lab/next_gen_beam_per_sym.incremental.jsonl" "$BASE/data/reports/gui_lab/next_gen_beam_per_sym.incremental.jsonl" 2>>"$LOG" || true
        rsync -az --timeout=30 -e "ssh $SSH_OPTS" "$S1_HOST:/tmp/beam_BTC_3000.log" "$BASE/data/reports/gui_lab/beam_BTC_3000_S1.log" 2>>"$LOG" || true
    fi
    # Pull OPT_*.png charts from S1 every ~5 min (per-sym profiler writes them)
    _chart_tick=$(( (_chart_tick + 1) % 150 ))
    if [[ $_chart_tick -eq 0 ]]; then
        mkdir -p "$MAC_PLOTS"
        rsync -az --timeout=30 --include='OPT_*.png' --exclude='*' -e "ssh $SSH_OPTS" \
            "$S1_PLOTS" "$MAC_PLOTS" 2>>"$LOG" \
            && echo "$(date -u +%FT%TZ) OPT_charts pulled from S1" >>"$LOG"
    fi
    # Token-free TRB operator supervision. This only checks a PID/queue and
    # launches the sealed pipeline when the prior key/batch is terminal; the
    # hourly digest is local and compact. It never invokes an agent/model.
    /bin/bash "$BASE/tools/trb_operator_watchdog.sh" >>"$LOG" 2>&1 || true
    rmdir "$TRANSPORT_LOCK" 2>/dev/null || true
    sleep "$SLEEP_SEC"
done
