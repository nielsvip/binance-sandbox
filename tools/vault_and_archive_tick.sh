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
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
REPO=/Users/niels/Documents/binance
PY=/opt/anaconda3/envs/binance_env/bin/python
LOG=/tmp/vault_and_archive_tick.log
VLOG=/tmp/script_version_vault.log
log() { echo "[$(date -u +%FT%TZ)] $*" >> "$LOG"; }

# timeout(1) RESOLVED, not assumed. macOS has no /usr/bin/timeout — it exists only as
# /opt/homebrew/bin/timeout (GNU coreutils), and launchd runs this with a bare PATH of /usr/bin:/bin:...
# so every `timeout ...` line here died with "command not found": 32 silent failures across all six call
# sites between 00:0x and 06:01Z on 2026-10-17, i.e. the per_sym, klines, shadow and deepen steps did
# NOTHING while the log still said "tick done". Interactive runs worked because a login PATH has Homebrew.
# The PATH export above fixes it; this resolves the binary anyway so a moved/removed coreutils is LOUD
# rather than silent, and the steps still run (unbounded) instead of being skipped.
TO="$(command -v timeout || command -v gtimeout || true)"
if [ -z "$TO" ]; then
  log "WARNING: no timeout(1) found (looked on PATH=$PATH) — running steps WITHOUT a time limit"
  run_to() { shift; "$@"; }                 # drop the seconds argument, run unbounded
else
  run_to() { local s="$1"; shift; "$TO" "$s" "$@"; }
fi
step() {                                     # step <name> <seconds> <cmd...>
  local name="$1" secs="$2"; shift 2
  if run_to "$secs" "$@" >> "$LOG" 2>&1; then
    log "  step $name ok"
  else
    log "  step $name FAILED rc=$? — see $LOG"
  fi
}

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
  step per_sym_full 5400 "$PY" "$REPO/tools/per_sym_store_archive.py" --sources all && touch "$STAMP"
else
  step per_sym_live 1800 "$PY" "$REPO/tools/per_sym_store_archive.py" --sources live
fi
# ---- 3. klines archive: append-only, on every drive present ------------------
# USER 2026-10-17: "make sure the ext drives hold a full klines backup that only grows never shrinks".
# Cheap after the first pass: the per-drive _manifest.json skips any symbol whose sources are unchanged by
# (mtime,size), so a tick normally parses only the files that actually got new bars.
step klines_archive 1500 "$PY" "$REPO/tools/klines_ext_archive.py"

# ---- 3b. shadow/observation folders ------------------------------------------
# USER 2026-10-17: use the cleaned drive "for anything that is in the macbook shadow folder that might not be
# there". These jsonl logs are things that HAPPENED once — unlike an NPZ (rebuildable) or a .py (in git and
# the script vault, which skips data/ entirely), they cannot be recomputed. Logs grow by extension; anything
# else is kept content-addressed so no version is overwritten. NPZ excluded by default (744 MB, rebuildable).
step shadow_archive 600 "$PY" "$REPO/tools/shadow_ext_archive.py"

# ---- 4. deepen history backwards, a little every tick ------------------------
# USER 2026-10-17: "on the ex drive we need infinite ones no clipping ever". The archive never clips, but the
# live stores are shallow by design (ez_klines._trim caps 15m at 1500-2000 bars), so depth has to be pulled
# from the exchange. A 420 s budget per tick walks backwards gradually instead of hammering the API, and each
# symbol/timeframe is stamped inception_reached once its first bar ever is in, so it is never re-paged.
step klines_deepen 600 "$PY" "$REPO/tools/klines_ext_archive.py" --deepen --budget 420 --pause 0.25

log "tick done"
