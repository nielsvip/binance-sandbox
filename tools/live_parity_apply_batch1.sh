#!/bin/bash
# Apply staged live-parity batch1 (Agent D). DRY-RUN unless --apply. Requires the user's explicit "unlock ez_indicators.py tradier_indicators.py" (LOCKED_FILES.md) before --apply.
set -e
cd /Users/niels/Documents/binance
B=data/live_parity/staged/batch1
TS=$(date +%Y%m%d%H%M)
python3 - <<'P'
import json,hashlib,sys
m=json.load(open("data/live_parity/staged/batch1/manifest.json"))
bad=[f for f,h in m["base_live_md5"].items() if hashlib.md5(open(f,"rb").read()).hexdigest()!=h]
if bad: sys.exit(f"REFUSED: live file(s) changed since staging: {bad} — re-stage (diff against current) first")
print("base md5 OK")
P
for f in ez_indicators.py tradier_indicators.py; do python3 -c "import py_compile;py_compile.compile('$B/files/$f',doraise=True)" && echo "staged $f compiles"; done
python3 -m pytest -q tests/test_live_parity_keys.py
if [ "$1" != "--apply" ]; then echo "dry-run only (pass --apply after the user said: unlock ez_indicators.py tradier_indicators.py)"; exit 0; fi
for f in ez_indicators.py tradier_indicators.py; do cp $f backups/before_live_parity_batch1_${TS}_$f; done
cp $B/files/ez_indicators.py ez_indicators.py; cp $B/files/tradier_indicators.py tradier_indicators.py
for f in live_parity_keys.py ez_indicators.py tradier_indicators.py; do python3 -c "import py_compile;py_compile.compile('$f',doraise=True)"; md5 -q $f; done
echo "NEXT: sync live_parity_keys.py + both indicator files to servers/sandboxes (rsync + md5), then restart the indicator services (ez_indicators crypto, tradier_indicators stocks) in a controlled window — see data/live_parity/report.md 'Restart plan'."
