#!/bin/bash
# Apply staged batch4_configs. DRY-RUN unless --apply. Needs the user's unlock message for config.py config_tradier.py ez_positions_quick.py ez_manage.py.
set -e; cd /Users/niels/Documents/binance; B=data/live_parity/staged/batch4_configs; TS=$(date +%Y%m%d%H%M)
python3 - <<'P'
import json,hashlib,sys
m=json.load(open("data/live_parity/staged/batch4_configs/manifest.json"))
bad=[f for f,h in m["base_md5"].items() if hashlib.md5(open(f,"rb").read()).hexdigest()!=h]
if bad: sys.exit(f"REFUSED: live file(s) changed since staging: {bad} — re-run make_batch4.py (rebuilds from current files) and re-review")
print("base md5 OK")
P
for f in config.py config_tradier.py ez_positions_quick.py ez_manage.py; do python3 -c "import py_compile;py_compile.compile('$B/$f',doraise=True)"; done
python3 -m pytest -q tests/test_batch4_configs.py | tail -2
[ "$1" = "--apply" ] || { echo "dry-run only"; exit 0; }
for f in config.py config_tradier.py ez_positions_quick.py ez_manage.py; do cp $f backups/before_batch4_${TS}_$f; cp $B/$f $f; python3 -c "import py_compile;py_compile.compile('$f',doraise=True)"; md5 -q $f; done
python3 tools/build_cat_side_defaults_4.py --stage-only | tail -2; python3 tools/verify_cat_side_defaults_4.py
echo "NEXT: rsync the 4 files + data/per_sym_settings.json to s1/s2/s5 (md5), then restart the 5 ez_manage accounts (ez_positions_quick is imported by them) — NOT before the LG-03/LG-04 decision (batch2_lg03 also edits ez_manage.py: apply that FIRST or re-run make_batch4.py on top of it)."
