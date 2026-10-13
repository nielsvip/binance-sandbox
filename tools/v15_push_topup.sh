#!/bin/bash
# v15_push_topup — USER 2026-10-13: "get 6 syms per server tested in the next hour". Keeps every worker at TARGET
# concurrent boards by dispatching through tools/v15_push_dispatch.sh (NPZ computed on s1, pushed md5-verified WITH the
# dispatch). Runs ON S1. Picks candidates best-performer-first from the cadence ledger (W tier), then any ledger side
# that is due, skipping anything already running anywhere on the fleet or already finished in this round.
# s1 keeps a smaller target: it carries the LIVE trading stack (never starve live money for a sweep).
# Usage: bash tools/v15_push_topup.sh [TARGET] [S1_TARGET]
set -u
ROOT="$HOME/binance-sandbox"
TARGET="${1:-${V15_PUSH_TARGET:-6}}"
S1_TARGET="${2:-${V15_PUSH_TARGET_S1:-2}}"
WORKERS="${V15_PUSH_WORKERS:-4}"
SSH="ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new"
cd "$ROOT" || exit 1
PDIR="$(ls -dt $HOME/v15_run3*/progress 2>/dev/null | head -1)"
[ -n "$PDIR" ] || { echo "no round progress dir"; exit 1; }

# every side running anywhere on the fleet
RUNNING=""
for IP in 127.0.0.1 10.0.0.4 10.0.0.5 10.0.0.6; do
  if [ "$IP" = "127.0.0.1" ]; then R=$(pgrep -af "v15_pilot.py --sym-side" 2>/dev/null | sed 's/.*--sym-side \([A-Z0-9_]*\).*/\1/')
  else R=$($SSH "niels@$IP" "pgrep -af 'v15_pilot.py --sym-side' 2>/dev/null | sed 's/.*--sym-side \([A-Z0-9_]*\).*/\1/'" 2>/dev/null); fi
  RUNNING="$RUNNING $R"
done
RUNNING=$(echo "$RUNNING" | tr ' ' '\n' | grep -E '_(LONG|SHORT)$' | sort -u)

# candidate queue: ledger W (best potential first), then every other ledger side that is due; minus running, minus
# boards already finished in this round (top-level final_gain), minus sides with no NPZ on s1.
CANDS=$(python3 - "$PDIR" <<'EOF'
import glob, json, os, sys, time
root = os.path.expanduser("~/binance-sandbox")
pdir = sys.argv[1]
done = set()
for f in glob.glob(os.path.join(pdir, "*_v14_progress.json")):
    try:
        j = json.load(open(f))
    except Exception:
        continue
    fg = j.get("final_gain")
    if isinstance(fg, (int, float)) and not isinstance(fg, bool) and not j.get("needs_redo"):
        done.add(os.path.basename(f)[: -len("_v14_progress.json")])
try:
    led = json.load(open(os.path.join(root, "data", "v15_symside_ledger.json")))["sym_sides"]
except Exception:
    led = {}
now = time.time()
rows = []
for ss, e in led.items():
    if not isinstance(e, dict) or ss in done:
        continue
    sym = ss.rsplit("_", 1)[0]
    if not os.path.exists(os.path.join(root, "backtest_v8", "indicators", sym + ".npz")):
        continue
    due = now >= float(e.get("next_due") or 0)
    rows.append((0 if e.get("sched_tier") == "W" else 1, -(e.get("potential") or 0.0), 0 if due else 1, ss))
rows.sort()
print("\n".join(r[3] for r in rows))
EOF
)

# OOM + load throttle (USER 2026-10-13 "make sure they get oom throttled if necessary"): the per-host budget comes from
# tools/fleet_hosts_final.json — the same mem_reserve_mb / oom_mb / pair_floor_mb the fleet scheduler admits on, so the
# dispatcher can never out-admit the scheduler's own OOM policy. A host under its oom floor gets NOTHING; otherwise the
# number of new boards is capped by (MemAvailable - reserve) / per_board_mb, and by load1/nproc.
eval "$(python3 - <<'EOF'
import json, os
try:
    cfg = json.load(open(os.path.expanduser("~/binance-sandbox/tools/fleet_hosts_final.json")))
except Exception:
    cfg = {}
floor = int(cfg.get("pair_floor_mb") or 8000)
out = []
for h in cfg.get("hosts") or []:
    ip = (h.get("ssh") or ["?"])[0]
    out.append("MEMRES_%s=%d" % (ip.replace(".", "_"), int(h.get("mem_reserve_mb") or 4000)))
    out.append("OOMFLOOR_%s=%d" % (ip.replace(".", "_"), int(h.get("oom_mb") or 1500)))
print("; ".join(out))
print("PAIR_FLOOR_MB=%d" % floor)
EOF
)"
PER_BOARD_MB="${V15_PUSH_PER_BOARD_MB:-$(( ${PAIR_FLOOR_MB:-8000} / 2 ))}"   # a board is ONE side; a scheduler "pair" is two
MAXLOAD_RATIO="${V15_PUSH_MAXLOAD_RATIO:-150}"                              # percent of nproc (150 = 1.5x cores)

host_budget() {   # $1=ip -> echoes "<allowed_new_boards> <memavail> <load1> <nproc>"
  local ip="$1" res oomf stats
  eval "res=\${MEMRES_$(echo $ip | tr . _):-4000}; oomf=\${OOMFLOOR_$(echo $ip | tr . _):-1500}"
  if [ "$ip" = "127.0.0.1" ]; then stats=$(awk '/MemAvailable/{m=$2} END{print int(m/1024)}' /proc/meminfo; cut -d' ' -f1 /proc/loadavg; nproc)
  else stats=$($SSH "niels@$ip" "awk '/MemAvailable/{m=\$2} END{print int(m/1024)}' /proc/meminfo; cut -d' ' -f1 /proc/loadavg; nproc" 2>/dev/null); fi
  local mem load np
  mem=$(echo "$stats" | sed -n 1p); load=$(echo "$stats" | sed -n 2p); np=$(echo "$stats" | sed -n 3p)
  mem="${mem:-0}"; load="${load:-999}"; np="${np:-1}"
  local allowed=0
  if [ "$mem" -le "$oomf" ] 2>/dev/null; then
    allowed=0
  else
    allowed=$(( (mem - res) / PER_BOARD_MB ))
    [ "$allowed" -lt 0 ] && allowed=0
    local lr; lr=$(python3 -c "print(int(float('$load')/max(1,int('$np'))*100))" 2>/dev/null || echo 999)
    [ "$lr" -ge "$MAXLOAD_RATIO" ] 2>/dev/null && allowed=0
  fi
  echo "$allowed $mem $load $np"
}

for SPEC in "127.0.0.1:$S1_TARGET" "10.0.0.4:$TARGET" "10.0.0.5:$TARGET" "10.0.0.6:$TARGET"; do
  IP="${SPEC%%:*}"; WANT="${SPEC##*:}"
  if [ "$IP" = "127.0.0.1" ]; then HAVE=$(pgrep -af "v15_pilot.py --sym-side" 2>/dev/null | sed 's/.*--sym-side \([A-Z0-9_]*\).*/\1/' | sort -u | wc -l)
  else HAVE=$($SSH "niels@$IP" "pgrep -af 'v15_pilot.py --sym-side' 2>/dev/null | sed 's/.*--sym-side \([A-Z0-9_]*\).*/\1/' | sort -u | wc -l" 2>/dev/null || echo 0); fi
  HAVE="${HAVE:-0}"
  NEED=$(( WANT - HAVE ))
  read -r ALLOWED MEMAV LOAD1 NPROC <<EOF
$(host_budget "$IP")
EOF
  if [ "$NEED" -gt "${ALLOWED:-0}" ] 2>/dev/null; then
    echo "[throttle] $IP need=$NEED -> $ALLOWED (MemAvailable=${MEMAV}MB load=${LOAD1}/${NPROC} per_board=${PER_BOARD_MB}MB)"
    NEED="${ALLOWED:-0}"
  fi
  echo "[topup] $IP have=$HAVE want=$WANT need=$NEED mem=${MEMAV}MB load=${LOAD1}"
  [ "$NEED" -gt 0 ] 2>/dev/null || continue
  PICK=""
  for SS in $CANDS; do
    echo "$RUNNING" | grep -qx "$SS" && continue
    echo "$PICK" | tr ' ' '\n' | grep -qx "$SS" && continue
    PICK="$PICK $SS"
    RUNNING="$RUNNING
$SS"
    [ "$(echo $PICK | wc -w)" -ge "$NEED" ] && break
  done
  [ -n "$(echo $PICK | tr -d ' ')" ] || { echo "[topup] $IP no candidates left"; continue; }
  echo "[topup] $IP dispatching:$PICK"
  timeout 900 bash tools/v15_push_dispatch.sh "$IP" "$WORKERS" $PICK 2>&1 | grep -E "^\[(launch|hold|skip|npz-push)\]" | cut -c1-170
done
