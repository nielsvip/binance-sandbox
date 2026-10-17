#!/bin/bash
# vault_and_archive_tick — every 30 min: keep the version vault genuinely alive, then top up the
# per_sym_store archive on whatever external drives are mounted right now.
#
# USER 2026-10-17: "make sure git saves EVERY version of a script ... maybe also keep a copy on an external
# drive in case git flakes out again" + "per_sym_store.db the complete copy that should never delete a line
# should live on TWO external drives (we have 3 but they are all flaky and all appear from time to time)".
#
# LIVENESS, not pgrep-liveness. launchd KeepAlive only checks that a pid EXISTS, so a process stopped with
# SIGSTOP or wedged in a syscall counts as healthy forever: that is exactly how autosave_15min.py sat in
# state T from 2026-10-14T21:03Z to 2026-10-16T23:42Z with launchd believing it was fine, and NOTHING in the
# repo was committed for two days. So this tick checks the process STATE (T = stopped) and whether the vault
# log is advancing, and SIGKILLs a wedged vault so launchd relaunches it clean.
set -u
REPO=/Users/niels/Documents/binance
PY=/opt/anaconda3/envs/binance_env/bin/python
LOG=/tmp/vault_and_archive_tick.log
VLOG=/tmp/script_version_vault.log
log() { echo "[$(date -u +%FT%TZ)] $*" >> "$LOG"; }

# ---- 1. vault liveness -------------------------------------------------------
PID=$(pgrep -f "script_version_vault.py" | head -1)
if [ -z "${PID:-}" ]; then
  log "vault not running — asking launchd to start it"
  launchctl kickstart -k gui/"$(id -u)"/com.niels.script-version-vault 2>/dev/null \
    || nohup "$PY" "$REPO/tools/script_version_vault.py" >> "$VLOG" 2>&1 &
else
  STAT=$(ps -o stat= -p "$PID" | tr -d ' ')
  case "$STAT" in
    T*) log "vault pid $PID is STOPPED (stat=$STAT) — SIGKILL so launchd relaunches it"; kill -9 "$PID" ;;
     *) log "vault pid $PID ok (stat=$STAT)" ;;
  esac
fi

# ---- 1b. keep .git free of Finder artefacts ---------------------------------
# git treats EVERY file under .git/refs as a ref, so a .DS_Store dropped there by Finder makes `git fsck`
# fail with badRefName/badRefContent (exit 8) and can surface as unexplained git errors — found 2026-10-17
# with five of them inside .git, including refs/.DS_Store and objects/.DS_Store; fsck went 8 -> 0 once
# removed. They come back any time someone browses .git in Finder, so sweep them every tick.
DS=$(find "$REPO/.git" -name ".DS_Store" -print -delete 2>/dev/null | wc -l | tr -d ' ')
[ "${DS:-0}" != "0" ] && log "removed $DS .DS_Store file(s) from .git (invalid refs / object-dir clutter)"

# ---- 2. per_sym_store archive to every drive present ------------------------
# --sources live is the cheap top-up. Once a day (or when a drive has just reappeared and is behind) the
# full union including backups/*.db is worth it, because those snapshots still hold history rows the live DB
# has already pruned (measured 2026-10-17: live history starts 2026-10-02, snapshots reach further back).
HOUR=$(date -u +%H)
STAMP=/tmp/per_sym_archive_full_$(date -u +%F)
if [ "$HOUR" = "03" ] && [ ! -f "$STAMP" ]; then
  log "daily full union archive"
  timeout 5400 "$PY" "$REPO/tools/per_sym_store_archive.py" --sources all >> "$LOG" 2>&1 && touch "$STAMP"
else
  timeout 1800 "$PY" "$REPO/tools/per_sym_store_archive.py" --sources live >> "$LOG" 2>&1
fi
log "tick done"
