#!/bin/bash
# USER 2026-10-17 "fix the stock vector-decided path first, then enforce it": the stocks VEC_EXACT twin
# (tradier_manage._vec_exact_process_twin -> live_twins/vec_exact.py, source npz) decides on the stock NPZ — the same
# file the boards measure. S2 keeps the stock NPZs bar-fresh (stocks refresh host); this pulls the traded stocks to the Mac.
# README_DO_NOT_TEST_ON_MAC: NPZs are never regenerated on the Mac, only copied from the servers — that is what this does.
# rsync --delay-updates: every file lands atomically after the transfer, never half-written under a reader.
set -u
BASE=/Users/niels/Documents/binance
LOG=/tmp/stock_npz_pull.log
LOCK=/tmp/stock_npz_pull.lock
mkdir "$LOCK" 2>/dev/null || { echo "$(date -u +%FT%TZ) SKIP lock held" >>"$LOG"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT
cd "$BASE" || exit 1
LIST=$(mktemp /tmp/stock_npz_list.XXXXXX)
/Users/niels/Documents/binance/.venv/bin/python - "$LIST" <<'EOF'
import glob, json, sys
syms = set()
for f in glob.glob("symbols_tr[abc]_*.json"):
    try:
        d = json.load(open(f))
        l = d if isinstance(d, list) else (d.get("symbols") or list(d.keys()))
        syms |= {str(x).upper() for x in l if str(x).strip()}
    except Exception:
        pass
open(sys.argv[1], "w").write("".join(f"{s}.npz\n" for s in sorted(syms)))
EOF
n=$(wc -l < "$LIST" | tr -d ' ')
t0=$(date +%s)
rsync -az --delay-updates --ignore-missing-args --timeout=120 --files-from="$LIST" -e "ssh -o ConnectTimeout=15 -o BatchMode=yes" \
    s2:binance-sandbox/backtest_v8/indicators/ "$BASE/backtest_v8/indicators/" 2>>"$LOG"
rc=$?
echo "$(date -u +%FT%TZ) PULL rc=$rc symbols=$n secs=$(( $(date +%s) - t0 ))" >>"$LOG"
rm -f "$LIST"
exit $rc
