#!/bin/bash
# Apply staged batch5_live_gates (needs user go). DRY-RUN unless --apply. Master LIVE_ENTRY_GATES_ENABLED stays False => zero live change until the user flips it.
set -e; cd /Users/niels/Documents/binance; B=data/live_parity/staged/batch5_live_gates; TS=$(date +%Y%m%d%H%M)
python3 - <<'P'
import json,hashlib,sys
m=json.load(open("data/live_parity/staged/batch5_live_gates/md5.json"))
bad=[f for f,h in m["base_md5"].items() if hashlib.md5(open(f,"rb").read()).hexdigest()!=h]
if bad: sys.exit(f"REFUSED: changed since staging {bad} — rerun make_batch5.py and re-review")
print("base md5 OK")
P
for f in tradier_manage.py ez_manage.py config_tradier.py; do python3 -c "import py_compile;py_compile.compile('$B/$f',doraise=True)"; done
python3 -m pytest -q tests/test_batch5_live_gates.py | tail -1
[ "$1" = "--apply" ] || { echo dry-run only; exit 0; }
for f in tradier_manage.py ez_manage.py config_tradier.py; do cp $f backups/before_batch5_${TS}_$f; cp $B/$f $f; python3 -c "import py_compile;py_compile.compile('$f',doraise=True)"; md5 -q $f; done
python3 tools/build_cat_side_defaults_4.py --stage-only | tail -1; python3 tools/verify_cat_side_defaults_4.py
echo "NEXT: rsync live_entry_gates.py + the 3 files + data/cat_side_defaults_4.json to s1/s2/s5 (md5); restart tradier managers AFTER the US close (tradier_manage/config_tradier import-cached) and the 5 ez_manage accounts (min-gain helper) one at a time."
