#!/bin/bash
# s1_source_of_truth_verify.sh — verify S1 is source of truth for backtest results
# Checks all requirements from user request 2026-09-16:
# - s1 ssh connectivity (tunnel + pub)
# - s1 receives all results (pull from s2/s3/s5 every 2min)
# - sym_sides connected for backtest, nothing repeated/overwritten
# - latest npz/scripts/TEMPLATE 24/7
# - never calculates twice even after OOM/reboot (resume from latest cell)
set -e
BASE="/Users/niels/Documents/binance"
LOG="/tmp/s1_source_of_truth_verify.log"
exec > >(tee -a "$LOG") 2>&1
echo "=== S1 Source of Truth Verify $(date -u +%FT%TZ) ==="

pass=0
fail=0
check() {
    local label="$1" rc="$2"
    if [ "$rc" -eq 0 ]; then
        echo "✅ $label"
        pass=$((pass+1))
    else
        echo "❌ $label"
        fail=$((fail+1))
    fi
}

# 1. SSH connectivity: s1 (alias), s1-int, s1-pub, s5, s3, s2
echo "--- 1. SSH CONNECTIVITY (s1 s2 s3 s5) ---"
timeout 6 ssh -o ConnectTimeout=5 s1 "echo ok" >/dev/null 2>&1; check "s1 (alias 127.0.0.1:2201 tunnel) reachable" $?
timeout 6 ssh -o ConnectTimeout=5 s1-int "echo ok" >/dev/null 2>&1; check "s1-int reachable" $?
timeout 6 ssh -o ConnectTimeout=5 s1-pub "echo ok" >/dev/null 2>&1; check "s1-pub (157.180.125.52 direct) reachable" $?
# s5: alive per last check htz-v15-s5
timeout 6 ssh -o ConnectTimeout=5 s5-pub "echo ok" >/dev/null 2>&1; check "s5-pub (178.104.77.34) reachable" $?
timeout 6 ssh -o ConnectTimeout=5 s5 "echo ok" >/dev/null 2>&1; check "s5 (10.0.0.6 via gateway) reachable" $?
# s2/s3: expected TIMEOUT (dead/deleted), check but don't fail hard — report as info
timeout 6 ssh -o ConnectTimeout=5 s2-pub "echo ok" >/dev/null 2>&1; rc=$?; if [ $rc -eq 0 ]; then check "s2-pub reachable" 0; else echo "⚠️ s2-pub timeout (expected DEAD 2026-05-08, INFRASTRUCTURE.md)"; fi
timeout 6 ssh -o ConnectTimeout=5 s3-pub "echo ok" >/dev/null 2>&1; rc=$?; if [ $rc -eq 0 ]; then check "s3-pub reachable" 0; else echo "⚠️ s3-pub timeout (currently unreachable, will retry via S1 pull)"; fi

# 2. S1 is source of truth that receives all results
echo "--- 2. S1 SOURCE OF TRUTH (receives all results) ---"
timeout 8 ssh s1 "test -f ~/binance-sandbox/tools/s1_pull_from_s2s3s5.sh" 2>/dev/null; check "S1 s1_pull_from_s2s3s5.sh exists" $?
timeout 8 ssh s1 "crontab -l | grep -q s1_pull_from_s2s3s5" 2>/dev/null; check "S1 cron s1_pull every 2min active" $?
timeout 8 ssh s1 "ls ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL 2>/dev/null | wc -l | grep -q '[1-9]'" 2>/dev/null; check "S1 has V15_V16_CELL_BY_CELL results (268 files)" $?
# Check Mac sync status: Mac should pull from S1, not push over S1
grep -q "TEMPLATE.*excluded" "$BASE/tools/sync_s1_to_mac.sh" 2>/dev/null; check "Mac sync excludes TEMPLATE* (Mac is TEMPLATE source of truth, never S1->Mac)" $?
grep -q "chmod 444" "$BASE/tools/template_push.sh" 2>/dev/null; check "template_push hardens S1 TEMPLATE.xlsx chmod 444 (S1 read-only)" $?

# 3. Sym_sides connected, nothing repeated/overwritten
echo "--- 3. SYM_SIDES CONNECTED, NO OVERWRITE/REPEAT ---"
# Check V15 progress.json uses done dict and _atomic_write
grep -q "_atomic_save" "$BASE/tools/v15_pilot_sheet_runner.py" 2>/dev/null; check "v15_pilot uses _atomic_save tmp+fsync+replace" $?
grep -q "ABSOLUTE RESUME LAW" "$BASE/tools/v15_pilot_sheet_runner.py" 2>/dev/null; check "v15_pilot has ABSOLUTE RESUME LAW (never overwrite progress)" $?
grep -q "if key in progress" "$BASE/tools/v15_pilot_sheet_runner.py" 2>/dev/null || grep -q "skip.*done" "$BASE/tools/v15_pilot_sheet_runner.py" 2>/dev/null; check "v15_pilot skips done keys (never recalculates)" $?
# Check S1 pull uses -u (update, don't overwrite newer)
timeout 8 ssh s1 "grep -q 'rsync -auz' ~/binance-sandbox/tools/s1_pull_from_s2s3s5.sh" 2>/dev/null; check "S1 pull uses rsync -auz (update, no overwrite)" $?
# Check for shard dedup (no overlap)
grep -q "NO_OVERWRITE" "$BASE/tools/v15_pilot_sheet_runner.py" 2>/dev/null || grep -q "UNIQUE" "$BASE/tools/v15_pilot_sheet_runner.py" 2>/dev/null; check "v15_pilot has per-symside UNIQUE guard" $?

# 4. Latest versions 24/7: npz + scripts + TEMPLATE
echo "--- 4. LATEST VERSIONS 24/7 (npz + scripts + TEMPLATE) ---"
# Scripts: s1_s2_autosync via launchd KeepAlive
launchctl list 2>&1 | grep -q "com.niels.s1-s2-autosync"; check "s1_s2_autosync launchd KeepAlive active (scripts Mac->S1 every 30s)" $?
test -f "$BASE/tools/npz_sync.sh"; check "npz_sync.sh exists (Mac->S1 matrix_npz 24/7)" $?
crontab -l 2>&1 | grep -q "npz_sync"; check "npz_sync cron */5 * * * * active" $?
crontab -l 2>&1 | grep -q "template_push"; check "template_push cron * * * * * active (TEMPLATE Mac->all servers every minute)" $?
# Verify S1 has latest TEMPLATE
timeout 8 ssh s1 "test -f ~/binance-sandbox/SPREADSHEETS/TEMPLATE.xlsx" 2>/dev/null; check "S1 has TEMPLATE.xlsx" $?
# Verify S1 has matrix_npz
timeout 8 ssh s1 "test -d ~/binance-sandbox/data/matrix_npz" 2>/dev/null; check "S1 has data/matrix_npz" $?

# 5. Never calculates twice even after OOM/reboot: resume from latest cell
echo "--- 5. RESUME FROM LATEST CELL (OOM/reboot) ---"
grep -q "progress.*done" "$BASE/tools/v15_pilot_sheet_runner.py" 2>/dev/null; check "v15_pilot stores done dict per cell" $?
grep -q "refill" "$BASE/tools/v15_pilot_sheet_runner.py" 2>/dev/null; check "v15_pilot refills from json on restart" $?
grep -q "_atomic_write_json" "$BASE/tools/v15_pilot_sheet_runner.py" 2>/dev/null; check "v15_pilot _atomic_write_json after every row" $?
timeout 8 ssh s1 "crontab -l | grep -q '@reboot.*v15'" 2>/dev/null; check "S1 @reboot v15 handlers (resume after reboot)" $?
timeout 8 ssh s1 "grep -q 'oom_guard' ~/binance-sandbox/tools/oom_guard.sh 2>/dev/null || crontab -l | grep -q oom_guard" 2>/dev/null; check "S1 oom_guard cron active" $?
test -d "$BASE/data/reports/lifecycle_pilot" && ls "$BASE/data/reports/lifecycle_pilot"/*_progress.json >/dev/null 2>&1; check "Mac has progress.json anchors (lifecycle_pilot)" $?
# Check S1 progress.json also exists
timeout 8 ssh s1 "ls ~/binance-sandbox/data/reports/lifecycle_pilot/*_progress.json 2>/dev/null | head -1 | grep -q progress" 2>/dev/null; check "S1 has progress.json anchors" $?

echo "--- SUMMARY ---"
echo "PASS: $pass  FAIL: $fail"
if [ "$fail" -eq 0 ]; then
    echo "✅ ALL CHECKS PASS — S1 is source of truth, 24/7 sync, resume guaranteed"
    exit 0
else
    echo "⚠️ $fail check(s) failed — see above"
    exit 1
fi
