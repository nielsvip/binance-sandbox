#!/bin/bash
# failover_readiness_audit.sh — READ-ONLY. Answers one question: if the MacBook died or slept right now, would S1
# take over correctly, and would it hand back when the Mac returns?
#
# USER 2026-10-17: "make sure the latest versions of all trading scripts are in the /binance/ folder as well as the
# market data/indicators/klines/long-short positions for all accounts are always completely in sync with mac and that
# s1 takes over immediately if no market data comes in or no trades are made or any other error appears on macbook
# (it also sleeps hours a day so after 3min ez_ and tradier_ systems should take over seamlessly and stop immediately
# when macbook comes back)."
#
# Five checks, each one a thing that has actually failed before:
#   1 CODE      every live trading script md5-identical on S1 ~/binance  (a stale config = the standby trades other settings)
#   2 STATE     long/short positions staged for ALL 8 accounts            (the takeover source is mac_state/, not S1's live dirs)
#   3 DATA      S1 klines moving + an indicator snapshot it can refresh   (arming a blind host is worse than no takeover)
#   4 ARMED     enable file, no pin, agent alive, all three units present (the 2026-10-15 failover was gagged, not broken)
#   5 TIMING    takeover <=3 min and failback fast                        (the operator's contract, in the agent's own env)
#
#   bash tools/failover_readiness_audit.sh          # table
#   bash tools/failover_readiness_audit.sh --quiet  # findings only
# Exit 0 = ready. Exit 1 = at least one finding. Nothing is started, stopped or written.
set -u
SSH="ssh -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new"
HOST="${S1_HOST:-s1-pub}"
REPO="/Users/niels/Documents/binance"
QUIET=0; [ "${1:-}" = "--quiet" ] && QUIET=1
FIND=0
say()  { [ "$QUIET" = "1" ] || echo "$@"; }
bad()  { echo "  !! $*"; FIND=$((FIND + 1)); }

LIVE_SCRIPTS="ez_manage.py ez_positions_service_main.py ez_positions_quick.py ez_positions_realtime.py ez_indicators.py
ez_klines.py ez_prices.py ez_mark_prices.py ez_rankings.py ez_reentry_daemon.py ez_positions_watchdog.py
tradier_manage.py tradier_positions.py tradier_indicators.py tradier_klines.py tradier_prices.py tradier_api.py
tradier_broker_snapshot.py order_dedupe_guard.py positions_truth.py exit_guarantee.py position_writer_guard.py
per_sym_store.py switch_parity.py metrics_guard.py config.py config_tradier.py utils.py run_with_watchdog.sh"

say "=== 1. CODE — live trading scripts on S1 ~/binance ==="
MAN=$(mktemp); for f in $LIVE_SCRIPTS; do [ -f "$REPO/$f" ] && echo "$f $(md5 -q "$REPO/$f")"; done > "$MAN"
TOT=$(wc -l < "$MAN" | tr -d ' ')
OUT=$($SSH "$HOST" 'cd /home/niels/binance 2>/dev/null || exit 9; while read f m; do if [ ! -f "$f" ]; then echo "MISSING $f"; else s=$(md5sum "$f" | cut -d" " -f1); [ "$s" = "$m" ] || echo "DIFFERS $f s1=${s:0:8} mac=${m:0:8}"; fi; done' < "$MAN" 2>&1)
rm -f "$MAN"
if [ -n "$OUT" ]; then while IFS= read -r l; do bad "$l"; done <<< "$OUT"; else say "  all $TOT live scripts md5-identical"; fi

say "=== 2. STATE — positions staged for every account ==="
$SSH "$HOST" 'now=$(date -u +%s); LF=/home/niels/binance/data/live_failover/mac_state
for a in ang inf flz men fin tra trb trc; do for s in long short; do f=$LF/$a/${s}_positions.json
  if [ -f "$f" ]; then echo "OK $a/$s $((now-$(stat -c %Y $f)))"; else echo "GONE $a/$s"; fi; done; done' 2>/dev/null | while read st key age; do
    if [ "$st" = "GONE" ]; then bad "positions NOT staged for $key"; else
      case "$key" in tra/*|trb/*|trc/*) lim=90000 ;; *) lim=300 ;; esac   # stocks legitimately stop writing at the close
      [ "$age" -gt "$lim" ] && bad "staged $key is ${age}s old (limit ${lim}s)" || say "  $key staged ${age}s ago"
    fi; done
[ "$($SSH "$HOST" 'ls /home/niels/binance/data/live_failover/mac_state 2>/dev/null | wc -l')" -ge 8 ] || bad "mac_state has fewer than 8 account dirs"

say "=== 3. DATA — S1 can see and rebuild the market ==="
$SSH "$HOST" 'now=$(date -u +%s)
k=$(ls -t /home/niels/binance/klines_cache/* 2>/dev/null | head -1); [ -n "$k" ] && echo "KL $((now-$(stat -c %Y $k)))" || echo "KL -1"
i=/home/niels/binance/data/tradier/tradier_indicators_latest.json; [ -f "$i" ] && echo "IND $((now-$(stat -c %Y $i)))" || echo "IND -1"' 2>/dev/null | while read k v; do
  case "$k" in
    KL)  [ "$v" -lt 0 ] && bad "S1 has no klines_cache files" || { [ "$v" -gt 900 ] && bad "S1 newest kline is ${v}s old" || say "  S1 klines newest ${v}s"; } ;;
    IND) [ "$v" -lt 0 ] && bad "S1 has no tradier indicator snapshot (it cannot seed a stocks takeover)" || say "  S1 tradier indicators ${v}s (on-demand unit refreshes within 90s)" ;;
  esac; done

say "=== 4. ARMED — the standby is allowed to act ==="
$SSH "$HOST" 'LF=/home/niels/binance/data/live_failover
[ -e "$LF/TRADING_FAILOVER_ENABLED" ] && echo "EN 1" || echo "EN 0"
python3 - <<PY
import json,os
p="$LF/lease.json"
d=json.load(open(p)) if os.path.exists(p) else {}
print("PIN", d.get("pin") or "none")
print("HOLDER", d.get("holder") or "?")
PY
echo "AGENT $(pgrep -cf "[p]ython[^ ]* -u [^ ]*live_failover_s1\.py --loop")"
for u in live-failover-crypto-producers live-failover-stocks-producers live-failover-trading live-failover-watchdog; do
  systemctl list-unit-files $u.service >/dev/null 2>&1 && echo "UNIT $u $(systemctl is-active $u)" || echo "UNIT $u ABSENT"; done' 2>/dev/null | while read a b c; do
  case "$a" in
    EN)     [ "$b" = "1" ] && say "  TRADING_FAILOVER_ENABLED present" || bad "TRADING_FAILOVER_ENABLED missing — S1 will never trade" ;;
    PIN)    [ "$b" = "none" ] && say "  no pin" || bad "lease pinned to '$b' — takeover is BLOCKED" ;;
    HOLDER) say "  lease holder: $b" ;;
    AGENT)  [ "$b" = "1" ] && say "  failover agent running (1)" || bad "failover agent count is $b (want 1)" ;;
    UNIT)   case "$c" in ABSENT) bad "systemd unit $b is ABSENT" ;; failed) bad "systemd unit $b is FAILED — reads as 'no standby'" ;; *) say "  unit $b: $c" ;; esac ;;
  esac; done

say "=== 5. TIMING — the operator's contract ==="
$SSH "$HOST" 'pid=$(systemctl show -p MainPID --value live-failover-watchdog 2>/dev/null); tr "\0" "\n" < /proc/$pid/environ 2>/dev/null | grep "^LF_" | sort' 2>/dev/null > /tmp/.lfenv.$$
getv() { grep -E "^$1=" /tmp/.lfenv.$$ 2>/dev/null | cut -d= -f2 || true; }
TICKC=$(( 15 * 4 ))   # LF_TICK_S x LF_TAKEOVER_CONFIRM, both left at source defaults
for pair in "LF_NT_ORDER_S:120:no trades made" "LF_TRADING_DEAD_S:120:engines gone"; do
  k=${pair%%:*}; rest=${pair#*:}; want=${rest%%:*}; lbl=${rest#*:}
  v=$(getv "$k"); v=${v:-UNSET}
  if [ "$v" = "UNSET" ]; then bad "$k not set — source default gives $(( (${k/LF_NT_ORDER_S/300} == 300 ? 300 : 600) + TICKC ))s for '$lbl'"
  elif [ "$v" -le "$want" ]; then say "  $lbl: ${v}s + ${TICKC}s confirm = $(( v + TICKC ))s (<= 180s)"
  else bad "$k=${v}s -> $(( v + TICKC ))s for '$lbl', over the 180s the operator asked for"; fi
done
FB=$(getv LF_FAILBACK_S); FB=${FB:-600}; BO=$(getv LF_NT_BACKOFF_MAX); BO=${BO:-3}
[ "$FB" -le 120 ] && say "  failback dwell ${FB}s (hand back when the Mac returns)" || bad "LF_FAILBACK_S=${FB}s — S1 keeps trading long after the Mac is back"
MAXFB=$(( FB * (1 << BO) ))
[ "$MAXFB" -le 300 ] && say "  worst-case failback ${MAXFB}s (backoff cap x$(( 1 << BO )))" || bad "worst-case failback ${MAXFB}s with LF_NT_BACKOFF_MAX=$BO"
rm -f /tmp/.lfenv.$$

say ""
if [ "$FIND" -eq 0 ]; then echo "FAILOVER READY — 0 findings"; else echo "FAILOVER NOT READY — $FIND finding(s)"; fi
exit $([ "$FIND" -eq 0 ] && echo 0 || echo 1)
