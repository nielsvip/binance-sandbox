#!/bin/bash
# venv_usage_tripwire.sh — USER 2026-10-17: "leave the venv there until proven unused and unnecessary for a couple
# of weeks - we can use the space!"
#
# Records EVERY process observed running on a .venv interpreter, so "nothing depends on it any more" becomes a
# measured fact with a date range instead of an opinion. Purely observational: it never kills, moves or edits
# anything. Runs on S1 and S2 from cron every minute.
#
# Two distinct venvs exist and they are NOT the same thing (2026-10-17):
#   /home/niels/binance/.venv          74M  py3.12  INCOMPLETE — missing aiofiles/aiohttp/pandas; 9 of 10 live
#                                      scripts cannot import under it. This is what crash-looped ez_indicators
#                                      every 32 s from 2026-10-14 and made S1 unable to take over. No longer
#                                      preferred by run_with_watchdog.sh.
#   /home/niels/binance-sandbox/.venv  316M py3.12  COMPLETE — the backtest workhorse, ~21 cron lines.
#
# Review with:  sort -u /home/niels/logs/venv_usage_tripwire.log | awk '{$1="";print}' | sort | uniq -c | sort -rn
# A venv with no NEW lines for a couple of weeks is the evidence needed to retire it.
set -u
LOG=/home/niels/logs/venv_usage_tripwire.log
mkdir -p "$(dirname "$LOG")"
TS=$(date -u +%FT%TZ)
# Detection note (2026-10-17, first version of this script got it wrong and reported ZERO users while
# ez_share_ind.py was demonstrably running on a .venv): /proc/<pid>/exe is NOT usable here. A venv's bin/python is
# a symlink to the base interpreter, so readlink resolves THROUGH the venv to e.g. /usr/bin/python3.12 and the
# .venv path disappears. The two things that do survive are argv[0] — the path as actually invoked — and the
# VIRTUAL_ENV environment variable. Check both, count the process once.
HOST=$(hostname)
for p in /proc/[0-9]*; do
    [ -r "$p/cmdline" ] || continue
    cmd=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null) || continue
    [ -z "$cmd" ] && continue
    argv0=${cmd%% *}
    venv=""
    case "$argv0" in */.venv/bin/*) venv="${argv0%/bin/*}" ;; esac
    # `[ -r ]` first: /proc/<pid>/environ of another user's process is unreadable, and the SHELL reports that on the
    # REDIRECTION before the command runs, so a `2>/dev/null` on the command cannot suppress it — the first version
    # emitted hundreds of "Permission denied" lines per pass and would have flooded cron mail every minute.
    if [ -z "$venv" ] && [ -r "$p/environ" ]; then
        venv=$(tr '\0' '\n' < "$p/environ" 2>/dev/null | sed -n 's/^VIRTUAL_ENV=//p' | head -1)
        case "$venv" in */.venv) : ;; *) venv="" ;; esac
    fi
    [ -n "$venv" ] && echo "$TS $HOST $venv :: $(echo "$cmd" | cut -c1-160)" >> "$LOG"
done
# keep the log bounded without losing the distinct-user evidence (dedupe identical observations, keep newest 20k)
if [ -f "$LOG" ] && [ "$(wc -l < "$LOG" 2>/dev/null || echo 0)" -gt 20000 ]; then
    tail -20000 "$LOG" > "$LOG.trim" && mv "$LOG.trim" "$LOG"
fi
exit 0
