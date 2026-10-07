#!/bin/bash
# v15_stage2_365d — unattended Stage 2. When the 30D universe is (nearly) complete, or shortly
# before US market open, run the SANCTIONED 365D certifier (tools/confirm_365d.py --all) on each
# venue's box (s1 crypto NPZ / s2 stock NPZ), then UNION the two sanctioned confirmed_365d.json
# outputs into the Mac's data/confirmed_365d.json. This is the "pos 365D gain -> live-eligible,
# neg -> disqualified (fail-closed)" gate. It does NOT edit live config or apply per-symbol recipes.
# Runs the heavy pass ONCE (stamp), idempotent; safe to invoke from cron every 15 min.
set -u
ROOT=/Users/niels/Documents/binance
DONE=$ROOT/SPREADSHEETS/V15_MAC_DONE
STAMP=$DONE/.stage2_365d_done
LOG=$DONE/stage2_365d.log
SSH="ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"
TS(){ date -u +%Y-%m-%dT%H:%M:%SZ; }
echo "[$(TS)] stage2 check" >>"$LOG"
[ -f "$STAMP" ] && { echo "[$(TS)] already done" >>"$LOG"; exit 0; }

N=$(ls "$DONE"/*_30d_matrix.xlsx 2>/dev/null | wc -l | tr -d ' ')
NOWMIN=$(( $(date -u +%H) * 60 + $(date -u +%M) ))   # UTC minutes since midnight
OPEN=$(( 13*60 + 30 ))                                # US open 13:30 UTC
FIRE_BY=$(( OPEN - 90 ))                              # fire by 12:00 UTC latest
READY=0
if [ "$N" -ge 340 ]; then READY=1; fi                              # universe essentially complete
if [ "$NOWMIN" -ge "$FIRE_BY" ] && [ "$NOWMIN" -lt "$OPEN" ] && [ "$N" -ge 50 ]; then READY=1; fi  # time fallback
if [ "$READY" -ne 1 ]; then echo "[$(TS)] not ready: done=$N nowmin=$NOWMIN fire_by=$FIRE_BY" >>"$LOG"; exit 0; fi

echo "[$(TS)] FIRING 365D certification + parity gate (done=$N)" >>"$LOG"
# Stage 2a — 365D certification (neg gain discarded; gain>0 AND trades>=30 required). Sanctioned writer.
timeout 4800 $SSH s1-int 'cd ~/binance-sandbox && BASE_PATH=$HOME/binance-sandbox .venv/bin/python -u tools/confirm_365d.py --all --window-days 365 >/tmp/confirm365_s1.log 2>&1; tail -2 /tmp/confirm365_s1.log' >>"$LOG" 2>&1
timeout 4800 $SSH s2     'cd ~/binance-sandbox && BASE_PATH=$HOME/binance-sandbox .venv/bin/python -u tools/confirm_365d.py --all --window-days 365 >/tmp/confirm365_s2.log 2>&1; tail -2 /tmp/confirm365_s2.log' >>"$LOG" 2>&1
# Stage 2b — parity gate: backtest_v12_engine vs vector on the certified set; REVOKE parity fails
# (via sanctioned confirm_365d.py --revoke). Runs post-sweep so the servers are idle and fast.
timeout 7200 $SSH s1-int 'cd ~/binance-sandbox && BASE_PATH=$HOME/binance-sandbox .venv/bin/python -u tools/v15_parity_gate.py --window-days 30 >/tmp/paritygate_s1.log 2>&1; tail -3 /tmp/paritygate_s1.log' >>"$LOG" 2>&1
timeout 7200 $SSH s2     'cd ~/binance-sandbox && BASE_PATH=$HOME/binance-sandbox .venv/bin/python -u tools/v15_parity_gate.py --window-days 30 >/tmp/paritygate_s2.log 2>&1; tail -3 /tmp/paritygate_s2.log' >>"$LOG" 2>&1

$SSH s1-int 'cat ~/binance-sandbox/data/confirmed_365d.json' >/tmp/c365_s1.json 2>/dev/null
$SSH s2     'cat ~/binance-sandbox/data/confirmed_365d.json' >/tmp/c365_s2.json 2>/dev/null
python3 - "$ROOT" >>"$LOG" 2>&1 <<'PY'
import json,sys,pathlib,datetime
root=pathlib.Path(sys.argv[1]); mac=root/"data"/"confirmed_365d.json"
base=json.loads(mac.read_text()) if mac.exists() else {}
if mac.exists():
    bak=mac.with_name(f"confirmed_365d.json.bak_stage2_{datetime.datetime.utcnow():%Y%m%d%H%M}")
    bak.write_text(json.dumps(base,indent=2))
added=0
for f in ["/tmp/c365_s1.json","/tmp/c365_s2.json"]:
    try: d=json.loads(open(f).read())
    except Exception: continue
    for k,v in d.items():
        if isinstance(v,dict) and v.get("valid") and float(v.get("gain_365d") or 0)>0 and int(v.get("trades") or 0)>=30:
            base[k]=v; added+=1
tmp=mac.with_suffix(".json.stage2tmp"); tmp.write_text(json.dumps(base,indent=2)); tmp.replace(mac)
pos=sum(1 for v in base.values() if isinstance(v,dict) and float(v.get("gain_365d") or 0)>0)
print(f"merged confirmed_365d.json: {len(base)} entries total, {added} unioned this run, {pos} positive-gain live-eligible")
PY
touch "$STAMP"
echo "[$(TS)] stage2 365D done" >>"$LOG"
