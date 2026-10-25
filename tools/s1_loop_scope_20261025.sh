#!/bin/bash
# USER 2026-10-25: S1 refreshes ONLY its own cat_side refresh set (CRYPTO_SHORT-
# exclusive symbols, row-append per 15m close, never a year recalc). s2 owns the
# 23 overlap symbols via its quarter lane; s5/s6 own all stocks symbols.
# Crontab discipline (bible section 11): backup + non-empty assert + count
# asserts before install; the loop daemon is restarted after so the scope bites.
set -u
export TS
TS=$(date -u +%Y%m%d%H%M%S)
crontab -l > "$HOME/crontab.before_loop_scope_$TS.txt" || exit 1
[ -s "$HOME/crontab.before_loop_scope_$TS.txt" ] || { echo EMPTY-BACKUP; exit 1; }
export SYMS="1000BONKUSDC,1000FLOKIUSDT,1000LUNCUSDT,ACEUSDT,ACTUSDT,ADAUSDC,AXSUSDT,BEATUSDT,BIGTIMEUSDT,BIOUSDC,BLURUSDT,BOMEUSDC,BRETTUSDT,COTIUSDT,EGLDUSDT,ENJUSDT,FARTCOINUSDT,FLUXUSDT,GALAUSDT,ICXUSDT,INXUSDT,IOTAUSDT,JASMYUSDT,KSMUSDT,MELANIAUSDT,MINAUSDT,MOVEUSDT,MOVRUSDT,NMRUSDT,ONEUSDT,PARTIUSDT,PTBUSDT,RENDERUSDT,RIFUSDT,RVNUSDT,SANDUSDT,SCRUSDT,SKLUSDT,SPELLUSDT,STORJUSDT,SUSHIUSDT,THETAUSDT,TLMUSDT,TRUMPUSDC,TRXUSDT,VANRYUSDT,WOOUSDT,WUSDT,XPLUSDT,ZILUSDT,ZKUSDT"
python3 - "$HOME/crontab.before_loop_scope_$TS.txt" <<'PYEOF'
import os
import sys
src = sys.argv[1]
txt = open(src).read()
n0 = txt.count("npz_bar_refresh.py --loop")
assert n0 == 2, f"loop-lines={n0}"
old = "tools/npz_bar_refresh.py --loop --mode both --workers 4"
assert txt.count(old) == 2, f"old-cmd={txt.count(old)}"
new = "tools/npz_bar_refresh.py --loop --mode crypto --symbols " + os.environ["SYMS"] + " --workers 4"
open(src, "w").write(txt.replace(old, new))
print("EDIT-OK")
PYEOF
crontab "$HOME/crontab.before_loop_scope_$TS.txt" || exit 1
echo "installed:"
crontab -l | grep "NPZ_LOOP" | cut -c1-200
