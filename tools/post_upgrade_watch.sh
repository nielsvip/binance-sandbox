#!/bin/bash
# post_upgrade_watch.sh — USER 2026-10-17: "upgrade conda on macbook and servers and monitor any problems that
# might arise" + "be careful have easy rollbacks as this has given me nightmares before".
#
# A package upgrade does not break a RUNNING process — it has already imported. It breaks the NEXT restart, which
# can be hours later and in the middle of the night, and then the watchdog crash-loops a live trader. So the thing
# worth watching is not "is it up now" but "would it come back", plus the first sign of a crash loop.
#
# Checks, per host:
#   IMPORT   every live module imports under the interpreter that host's watchdog actually uses
#   LOGS     ModuleNotFoundError / ImportError appearing in live logs since the upgrade
#   LOOP     a watchdog restarting the same script repeatedly (the signature of a broken import)
#   CONFLICT pip's own dependency check
#
# Rollback if this ever fires:  bash tools/conda_env_rollback.sh --list
#                               bash tools/conda_env_rollback.sh --host <mac|s1-pub|s2> --snapshot <name>
#
#   bash tools/post_upgrade_watch.sh          # all hosts
#   bash tools/post_upgrade_watch.sh --quiet  # findings only (cron)
set -u
SSH="ssh -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new"
QUIET=0; [ "${1:-}" = "--quiet" ] && QUIET=1
FIND=0
say() { [ "$QUIET" = "1" ] || echo "$@"; }
bad() { echo "  !! $*"; FIND=$((FIND + 1)); }

MODULES="ez_manage tradier_manage ez_positions_quick tradier_positions ez_indicators ez_klines ez_prices tradier_indicators tradier_klines v12_quick_engine order_dedupe_guard tradier_broker_snapshot"

# ---------- Mac (the live trader) ----------
say "=== mac (live) ==="
MACPY=/opt/anaconda3/envs/binance_env/bin/python
cd /Users/niels/Documents/binance || exit 1
mf=0
for m in $MODULES; do
    "$MACPY" -c "import $m" >/dev/null 2>/tmp/.puw.err || { bad "mac cannot import $m -> $(grep -E 'Error' /tmp/.puw.err | tail -1 | cut -c1-70)"; mf=$((mf + 1)); }
done
[ "$mf" = "0" ] && say "  all $(echo $MODULES | wc -w | tr -d ' ') live modules import"
for L in /Users/niels/logs/ez_manage_*.log /Users/niels/logs/tradier_manage_*.log; do
    [ -f "$L" ] || continue
    n=$(grep -c -E "ModuleNotFoundError|ImportError" "$L" 2>/dev/null | tr -d ' ')
    [ "${n:-0}" -gt 0 ] && bad "mac $(basename "$L"): $n ModuleNotFound/ImportError line(s)"
done
say "  live procs: ez_manage=$(pgrep -f 'python.*ez_manage.py --account' | wc -l | tr -d ' ') positions_service=$(pgrep -f 'python.*ez_positions_service_main.py' | wc -l | tr -d ' ')"
c=$("$MACPY" -m pip check 2>&1 | grep -vcE "^No broken requirements|^$" || true)
[ "${c:-0}" -gt 1 ] && bad "mac has $c pip dependency conflicts (1 known-benign: web3/websockets)"

# ---------- servers ----------
for H in s1-pub s2; do
    say "=== $H ==="
    OUT=$($SSH "$H" 'P=/home/niels/.conda/envs/binance_env_mac/bin/python
cd /home/niels/binance-sandbox 2>/dev/null || exit 9
for m in '"$MODULES"'; do "$P" -c "import $m" >/dev/null 2>/tmp/.puw.err || echo "IMPORTFAIL $m $(grep -E Error /tmp/.puw.err | tail -1 | cut -c1-60)"; done
for L in /home/niels/logs/ez_indicators_watchdog.log /home/niels/logs/ez_klines_watchdog.log /home/niels/logs/ez_prices_watchdog.log; do
  [ -f "$L" ] || continue
  n=$(tail -60 "$L" 2>/dev/null | grep -c "consecutive failure")
  [ "$n" -gt 5 ] && echo "CRASHLOOP $(basename $L) $n"
done
echo "CONFLICTS $("$P" -m pip check 2>&1 | grep -vcE "^No broken requirements|^$")"' 2>&1)
    # `... | while read` runs the loop in a SUBSHELL, so every bad() increment of FIND is discarded and the script
    # reports "0 findings" while printing findings — it did exactly that on first run (2026-10-17). Process
    # substitution keeps the loop in THIS shell so the counter and the exit code are real.
    while read -r _ m rest; do [ -n "$m" ] && bad "$H cannot import $m -> $rest"; done < <(echo "$OUT" | grep IMPORTFAIL || true)
    while read -r _ f n; do [ -n "$f" ] && bad "$H $f shows $n recent consecutive-failure lines — crash loop"; done < <(echo "$OUT" | grep CRASHLOOP || true)
    cf=$(echo "$OUT" | awk '/^CONFLICTS/{print $2}')
    [ "${cf:-0}" -gt 1 ] && bad "$H has $cf pip dependency conflicts (1 known-benign: web3/websockets)"
    echo "$OUT" | grep -qE "IMPORTFAIL|CRASHLOOP" || say "  all live modules import; no crash loops"
done

say ""
if [ "$FIND" -eq 0 ]; then echo "POST-UPGRADE OK — 0 findings"; else echo "POST-UPGRADE PROBLEMS — $FIND finding(s); rollback: bash tools/conda_env_rollback.sh --list"; fi
exit $([ "$FIND" -eq 0 ] && echo 0 || echo 1)
