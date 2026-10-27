#!/bin/bash
# ONE SWITCH between Mac PAPER trading (S1/S5 vector_direct execute) and Mac REAL trading.
# USER 2026-10-27: "keep it traidng without execution power ... make sure all can be reverted to real trading with one switch."
#   tools/live_mode.sh paper   S1 (crypto) / S5 (stocks) pinned to own every account, THEN the Mac's orders are refused at the
#                              broker layer (data/MAC_PAPER_TRADING -> mac_paper_gate.py); Mac engines keep running + logging VEC_.
#   tools/live_mode.sh real    S1/S5 release every account and confirm it, THEN the Mac flag is removed: the Mac executes again.
#                              S1/S5 still take an account over the moment the Mac has a data/trading fault (vector_direct_trader).
#   tools/live_mode.sh status
# Order matters in both directions so there is never a moment with two executors or none.
set -u
BASE="$(cd "$(dirname "$0")/.." && pwd)"
FLAG="$BASE/data/MAC_PAPER_TRADING"
S1="${LIVE_MODE_S1:-s1}"
S5="${LIVE_MODE_S5:-s5}"
VD='~/binance/data/vector_direct'
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=10)

remote() { "${SSH[@]}" "$1" "$2"; }

active_of() {  # host venue -> active account list from the trader's own status (fresh < 60 s), or "STALE"
  remote "$1" "python3 -c 'import json,time,os;p=os.path.expanduser(\"$VD/status_$2.json\");d=json.load(open(p));print(\"STALE\" if time.time()-d[\"epoch\"]>60 or not d.get(\"armed\") else \",\".join(d.get(\"active_accounts\") or []))'" 2>/dev/null
}

pin() {  # host venue accounts
  remote "$1" "python3 -c 'import json,time,os;d=os.path.expanduser(\"$VD\");os.path.exists(d+\"/release_$2.json\") and os.remove(d+\"/release_$2.json\");json.dump({\"until_epoch\":time.time()+30*86400,\"until_iso\":\"UNTIL_REVOKED (tools/live_mode.sh real)\",\"accounts\":\"$3\".split(\",\"),\"why\":\"tools/live_mode.sh paper\"},open(d+\"/pin_$2.json\",\"w\"),indent=1)'"
}

release() {  # host venue
  remote "$1" "python3 -c 'import json,time,os;d=os.path.expanduser(\"$VD\");os.path.exists(d+\"/pin_$2.json\") and os.rename(d+\"/pin_$2.json\",d+\"/pin_$2.json.revoked_%d\"%time.time());json.dump({\"epoch\":time.time(),\"why\":\"tools/live_mode.sh real\"},open(d+\"/release_$2.json\",\"w\"))'"
}

wait_for() {  # host venue predicate(all|none) timeout
  local end=$((SECONDS + $4)) a
  while [ $SECONDS -lt $end ]; do
    a="$(active_of "$1" "$2")"
    if [ "$3" = none ] && [ -z "$a" ]; then return 0; fi
    if [ "$3" = all ] && [ -n "$a" ] && [ "$a" != STALE ]; then return 0; fi
    sleep 5
  done
  echo "  $1 $2 active=[$a] did not reach '$3' in $4 s" >&2
  return 1
}

case "${1:-status}" in
  paper)
    pin "$S1" crypto ang,fin,flz,inf,men || { echo "REFUSED: cannot pin S1 — Mac stays REAL"; exit 2; }
    pin "$S5" stocks tra,trb,trc || echo "WARNING: cannot pin S5 stocks (S5 unreachable) — stocks stay with the Mac until S5 is pinned"
    wait_for "$S1" crypto all 90 || { echo "REFUSED: S1 did not take crypto — Mac stays REAL (no flag written)"; exit 2; }
    echo "PAPER $(date -u +%FT%TZ) by tools/live_mode.sh — S1/S5 execute; Mac orders refused by mac_paper_gate.py" > "$FLAG"
    echo "Mac = PAPER (flag $FLAG). S1 crypto active: $(active_of "$S1" crypto). S5 stocks active: $(active_of "$S5" stocks) (empty while the market is closed; S5 takes them at the open)."
    ;;
  real)
    release "$S1" crypto || { echo "REFUSED: cannot reach S1 to release — Mac stays PAPER"; exit 2; }
    release "$S5" stocks || echo "WARNING: cannot reach S5 to release stocks"
    wait_for "$S1" crypto none 90 || { echo "REFUSED: S1 still owns crypto — Mac stays PAPER (run again)"; exit 2; }
    wait_for "$S5" stocks none 90 || { echo "REFUSED: S5 still owns stocks — Mac stays PAPER (run again)"; exit 2; }
    rm -f "$FLAG"
    echo "Mac = REAL. S1/S5 back to backstop + takeover-on-fault."
    ;;
  status)
    if [ -f "$FLAG" ]; then echo "Mac: PAPER — $(cat "$FLAG")"; else echo "Mac: REAL"; fi
    echo "S1 crypto active: [$(active_of "$S1" crypto)]  pin: $(remote "$S1" "cat $VD/pin_crypto.json 2>/dev/null | tr -d '\n' | cut -c1-90")"
    echo "S5 stocks active: [$(active_of "$S5" stocks)]  pin: $(remote "$S5" "cat $VD/pin_stocks.json 2>/dev/null | tr -d '\n' | cut -c1-90")"
    for v in crypto stocks; do f="$BASE/data/mac_paper/orders_${v}_$(date -u +%F).jsonl"; echo "Mac paper-refused $v orders today: $( [ -f "$f" ] && wc -l < "$f" || echo 0)"; done
    ;;
  *) echo "usage: $0 {paper|real|status}"; exit 1 ;;
esac
