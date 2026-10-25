#!/bin/bash
# USER 2026-10-25: S1 loop keeps ONLY its own cat_side (crypto exclusive set).
# Same crontab discipline: backup + asserts before install.
set -u
export TS
TS=$(date -u +%Y%m%d%H%M%S)
crontab -l > "$HOME/crontab.before_keepalive_scope_$TS.txt" || exit 1
[ -s "$HOME/crontab.before_keepalive_scope_$TS.txt" ] || { echo EMPTY-BACKUP; exit 1; }
export SYMS="1000BONKUSDC,1000FLOKIUSDT,1000LUNCUSDT,ACEUSDT,ACTUSDT,ADAUSDC,AXSUSDT,BEATUSDT,BIGTIMEUSDT,BIOUSDC,BLURUSDT,BOMEUSDC,BRETTUSDT,COTIUSDT,EGLDUSDT,ENJUSDT,FARTCOINUSDT,FLUXUSDT,GALAUSDT,ICXUSDT,INXUSDT,IOTAUSDT,JASMYUSDT,KSMUSDT,MELANIAUSDT,MINAUSDT,MOVEUSDT,MOVRUSDT,NMRUSDT,ONEUSDT,PARTIUSDT,PTBUSDT,RENDERUSDT,RIFUSDT,RVNUSDT,SANDUSDT,SCRUSDT,SKLUSDT,SPELLUSDT,STORJUSDT,SUSHIUSDT,THETAUSDT,TLMUSDT,TRUMPUSDC,TRXUSDT,VANRYUSDT,WOOUSDT,WUSDT,XPLUSDT,ZILUSDT,ZKUSDT"
python3 - "$HOME/crontab.before_keepalive_scope_$TS.txt" <<'PYEOF'
import os
import sys
src = sys.argv[1]
txt = open(src).read()
old = "flock -n /tmp/npz_loop_keepalive.lock /bin/bash /home/niels/binance-sandbox/tools/npz_loop_keepalive.sh"
assert txt.count(old) == 1, f"keepalive-lines={txt.count(old)}"
assert "NPZ_LOOP_SYMBOLS=" not in txt, "already scoped"
new = "NPZ_LOOP_MODE=crypto NPZ_LOOP_SYMBOLS=\"" + os.environ["SYMS"] + "\" " + old
open(src, "w").write(txt.replace(old, new))
print("EDIT-OK")
PYEOF
crontab "$HOME/crontab.before_keepalive_scope_$TS.txt" || exit 1
echo "installed:"
crontab -l | grep "NPZ_LOOP_KEEPALIVE" | cut -c1-120
