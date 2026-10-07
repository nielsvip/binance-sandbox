#!/bin/bash
# MacBook /SPREADSHEETS/ has to constantly pull all xls and charts from S2/S1
set -e
# NEW-ONLY 2026-10-06 (USER: ONLY new files may sync S1->Mac, never ancient):
# -u on every pull + remote find -mmin recency gate (V15_SYNC_MAX_AGE_MIN,
# default 4320 = 72h) feeding --files-from for SPREADSHEETS bulk. ssh failure
# yields an empty list (fail-closed: that pull transfers nothing).
# lifecycle_pilot JSON pulls are update-only (-u, no gate): resume needs old
# anchors and they are KB, not the GB flood.
MAX_AGE_MIN="${V15_SYNC_MAX_AGE_MIN:-4320}"
LIST_IMP="$(mktemp /tmp/mac_pull_imp.XXXXXX)"
trap 'rm -f "$LIST_IMP"' EXIT
while true; do
  echo "[$(date)] Pulling xls and charts from S1 and S2 to Mac"
#OFF_20261004_singlewriter   # S1 FINAL winning files only: 1 per sym_side, bh+gain, no timestamped interim/pilot (was drowning Mac at 1GB/min with 1737 files 2.1GB -> 99 files 54M)
#OFF_20261004_singlewriter   # FINAL dir on S1 is maintained by publish_final_winners.py (99 files 54M vs 12458 total). Use direct 10.0.0.3 private net, fallback localhost tunnel
#OFF_20261004_singlewriter   rsync -av --delete --exclude="*.html" -e "ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5" niels@10.0.0.3:binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL_FINAL/ /Users/niels/Documents/binance/SPREADSHEETS/V15_V16_CELL_BY_CELL/ 2>&1 | tail -20 || true
#OFF_20261004_singlewriter   rsync -av --delete --exclude="*.html" -e "ssh -p 2201 -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/dev/null -i ~/.ssh/id_ed25519" niels@localhost:binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL_FINAL/ /Users/niels/Documents/binance/SPREADSHEETS/V15_V16_CELL_BY_CELL/ 2>&1 | tail -20 || true
#OFF_20261004_singlewriter   # Coherent charts from FINAL (pilot-owned, same set as xlsx) — same-name lies overwritten, nothing deleted
#OFF_20261004_singlewriter   rsync -av --include="*.html" --exclude="*" -e "ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5" niels@10.0.0.3:binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL_FINAL/ /Users/niels/Documents/binance/SPREADSHEETS/V15_V16_CELL_BY_CELL/ 2>&1 | tail -20 || true
#OFF_20261004_singlewriter   rsync -av --include="*.html" --exclude="*" -e "ssh -p 2201 -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/dev/null -i ~/.ssh/id_ed25519" niels@localhost:binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL_FINAL/ /Users/niels/Documents/binance/SPREADSHEETS/V15_V16_CELL_BY_CELL/ 2>&1 | tail -20 || true
  # IMPOSSIBLE quarantine dirs (additive — manual-revision evidence, never deleted locally)
  : > "$LIST_IMP"
  ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5 niels@10.0.0.3 "find binance-sandbox/SPREADSHEETS/V15_V16_IMPOSSIBLE -maxdepth 1 -type f -mmin -$MAX_AGE_MIN -printf '%f\n'" >> "$LIST_IMP" 2>/dev/null || ssh -p 2201 -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/dev/null -i ~/.ssh/id_ed25519 niels@localhost "find binance-sandbox/SPREADSHEETS/V15_V16_IMPOSSIBLE -maxdepth 1 -type f -mmin -$MAX_AGE_MIN -printf '%f\n'" >> "$LIST_IMP" 2>/dev/null || true
  rsync -auv --files-from="$LIST_IMP" -e "ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5" niels@10.0.0.3:binance-sandbox/SPREADSHEETS/V15_V16_IMPOSSIBLE/ /Users/niels/Documents/binance/SPREADSHEETS/V15_V16_IMPOSSIBLE/ 2>&1 | tail -5 || true
  rsync -auv --files-from="$LIST_IMP" -e "ssh -p 2201 -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/dev/null -i ~/.ssh/id_ed25519" niels@localhost:binance-sandbox/SPREADSHEETS/V15_V16_IMPOSSIBLE/ /Users/niels/Documents/binance/SPREADSHEETS/V15_V16_IMPOSSIBLE/ 2>&1 | tail -5 || true
  rsync -auv -e "ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=5" niels@10.0.0.3:binance-sandbox/data/reports/lifecycle_pilot/*.json /Users/niels/Documents/binance/data/reports/lifecycle_pilot/ 2>&1 | tail -10 || true
  rsync -auv -e "ssh -p 2201 -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/dev/null -i ~/.ssh/id_ed25519" niels@localhost:binance-sandbox/data/reports/lifecycle_pilot/*.json /Users/niels/Documents/binance/data/reports/lifecycle_pilot/ 2>&1 | tail -10 || true
#OFF_20261004_singlewriter   # Fallback S2 FINAL if S1 tunnel down (also 1 per sym_side)
#OFF_20261004_singlewriter   rsync -av --delete --exclude="*.html" -e "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i ~/.ssh/id_ed25519" root@2.28.67.180:/root/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL_FINAL/ /Users/niels/Documents/binance/SPREADSHEETS/V15_V16_CELL_BY_CELL/ 2>&1 | tail -20 || true
  echo "[$(date)] Pull done (FINAL 1 per sym_side bh+gain only, --delete cleans interim, was 1737 2.1GB now 99 54M), sleeping 60s"
  sleep 60
done

# BADZIP FIX 2026-09-29: xlsx sources are atomic-writers only (see tools/xlsx_atomic.py).
# Belt-and-braces: quarantine any invalid xlsx that still arrives, never leave it in place.
python3 - <<'PYEOF'
import zipfile, os, pathlib
dst = pathlib.Path("/Users/niels/Documents/binance/SPREADSHEETS/V15_V16_CELL_BY_CELL")
q = pathlib.Path("/Users/niels/Documents/binance/backups/corrupt_xlsx_quarantine")
n = 0
for p in dst.glob("*.xlsx"):
    try:
        with zipfile.ZipFile(p) as z:
            if len(z.namelist()) >= 10 and z.testzip() is None:
                continue
    except Exception:
        pass
    q.mkdir(parents=True, exist_ok=True)
    os.replace(p, q / p.name)
    n += 1
if n:
    print(f"[badzip-quarantine] moved {n} invalid xlsx arrivals to {q}")
PYEOF
