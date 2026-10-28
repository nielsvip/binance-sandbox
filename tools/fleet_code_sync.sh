#!/bin/bash
# tools/fleet_code_sync.sh — enforce identical backtest script set on s1/s2/s5/s6.
# USER 2026-10-21: every backtest emitted from v15_fleet_scheduler on s1 must run
# the same scripts on every worker. S1 ~/binance-sandbox is canonical (fleet
# doctrine: workers are images of S1). NPZ data is OUT of scope here (JIT-pushed
# + md5-verified per dispatch by v15_dispatch_job.py); this covers CODE only.
# Usage: fleet_code_sync.sh [--check-only]
#   --check-only: report diffs, change nothing. Default: push S1 -> workers + re-verify.
set -u
# 2026-10-27: the four template paths were the LEGACY top-level workbooks, so this guard proved the fleet
# agreed on files no board sweeps while the TEMPLATE_FINAL_NORM set the pilot clones went unchecked -- the exact
# skew class behind the 2026-10-27 stall. This syncs S1 -> workers (S1 canonical), so FINAL_NORM is correct here.
MANIFEST_REL="/tmp/fleet_manifest.sh"
HOSTS="s2 s5 s6"
FILES="v15_pilot.py v12_quick_engine.py backtest_v12_engine.py config.py config_tradier.py min_decision_tf_guard.py positions_truth.py cat_side_defaults.py tools/v15_365_cycle.py tools/v15_graph_search.py tools/v15_dispatch_job.py tools/opt/v12_pilot_sheet_runner.py tools/opt/lifecycle_pilot_v2_fixed.py tools/opt/evaluate_v12.py ez_manage.py ez_positions_quick.py tradier_manage.py SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_LONG.xlsx SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_SHORT.xlsx SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_LONG.xlsx SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_SHORT.xlsx"
CHECK_ONLY="${1:-}"
EVEN_S6_VIA_MAC=1  # s6 unresolvable from s1; stage via Mac (scp fallback for big files)

cat > /tmp/fleet_manifest_gen.sh <<'EOF'
cd ~/binance-sandbox || exit 1
for f in v15_pilot.py v12_quick_engine.py backtest_v12_engine.py config.py config_tradier.py min_decision_tf_guard.py positions_truth.py cat_side_defaults.py tools/v15_365_cycle.py tools/v15_graph_search.py tools/v15_dispatch_job.py tools/opt/v12_pilot_sheet_runner.py tools/opt/lifecycle_pilot_v2_fixed.py tools/opt/evaluate_v12.py ez_manage.py ez_positions_quick.py tradier_manage.py SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_LONG.xlsx SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_SHORT.xlsx SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_LONG.xlsx SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_SHORT.xlsx vec_decisions/*.py live_twins/*.py; do
  if [ -f "$f" ]; then md5sum "$f"; else echo "MISSING $f"; fi
done
EOF
for h in s1-int $HOSTS; do
  scp -o StrictHostKeyChecking=accept-new /tmp/fleet_manifest_gen.sh $h:/tmp/fleet_manifest.sh >/dev/null 2>&1
  ssh $h "bash /tmp/fleet_manifest.sh" > /tmp/manifest_$h.txt 2>/dev/null
done
python3 - "$CHECK_ONLY" <<'EOF'
import subprocess, sys
check_only = len(sys.argv) > 1 and sys.argv[1] == "--check-only"
def load(p):
    d = {}
    for l in open(p):
        l = l.strip()
        if l.startswith("MISSING"): d[l.split(None, 1)[1]] = "MISSING"
        else:
            h, f = l.split(None, 1); d[f] = h
    return d
s1 = load("/tmp/manifest_s1-int.txt")
rc = 0
for h in ["s2", "s5", "s6"]:
    o = load(f"/tmp/manifest_{h}.txt")
    diff = sorted(set(s1) | set(o))
    diff = [f for f in diff if s1.get(f) != o.get(f)]
    print(f"{h}: {len(diff)} diffs")
    for f in diff:
        print(f"  {f}")
        if not check_only:
            if h == "s6":
                r1 = subprocess.run(["rsync", "-az", "-e", "ssh -o StrictHostKeyChecking=accept-new", f"s1-int:~/binance-sandbox/{f}", f"/tmp/sync_{h}_{f.replace('/', '_')}"]).returncode
                r2 = subprocess.run(["scp", "-o", "StrictHostKeyChecking=accept-new", f"/tmp/sync_{h}_{f.replace('/', '_')}", f"s6:~/binance-sandbox/{f}"]).returncode
                if r1 or r2: print(f"  FAIL {f}"); rc = 1
            else:
                r = subprocess.run(["ssh", "s1-int", f"rsync -az -e 'ssh -o ConnectTimeout=10 -o BatchMode=yes' ~/binance-sandbox/{f} {h}:~/binance-sandbox/{f}"])
                if r.returncode: print(f"  FAIL {f}"); rc = 1
sys.exit(rc)
EOF
echo "push rc=$? (s6 note: s1 cannot resolve s6; staged via Mac, scp fallback for big files)"
# USER 2026-10-24 (16h split-brain: v12's line-104 vec_decisions import reached
# workers ~11 h before the module did; every pilot died at import until 18:29Z
# while md5s looked "fine" per-file). md5-identical is NOT importable: after any
# push — and on --check-only — smoke-import the engine on every host with the
# pilots' own interpreter. Fail-closed like push failures (rc=1). Read-only.
SMOKE_RC=0
for h in s1-int s2 s5 s6; do
  if ssh -o BatchMode=yes -o ConnectTimeout=10 "$h" \
    "cd ~/binance-sandbox && /home/niels/.conda/envs/binance_env_mac/bin/python -c 'import v12_quick_engine; print(\"IMPORT_SMOKE_OK\")'" 2>/dev/null | grep -q IMPORT_SMOKE_OK; then
    echo "smoke $h: IMPORTS_OK"
  else
    echo "smoke $h: IMPORT_FAIL (v12_quick_engine or its vec_decisions imports broken — DO NOT dispatch here)"
    SMOKE_RC=1
  fi
done
[ "$SMOKE_RC" = "0" ] || exit 1
