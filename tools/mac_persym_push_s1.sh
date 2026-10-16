#!/bin/bash
# mac_persym_push_s1.sh — keep S1 holding the SETTINGS live trading actually runs on, so a takeover trades the same.
#
# USER 2026-10-15: "the positions folders should also be rsynced to s1 constantly as they also have the databases for
# the exit and reentry levels etcetera that are needed for a flawless start on s1 (although trades are now overridden
# temporarily by simply copytrading the results of trading the settings from the per_sym best settings vectorized
# backtests)."
#
# Measured 2026-10-15 20:3xZ, during the live takeover test — S1 was trading WITHOUT the per_sym settings:
#     per_sym_store.db        Mac 1153.5 MB age 31 min   |  S1 0.065 MB age 7.7 DAYS   (a stub)
#     per_sym_settings.json   Mac 536 KB                 |  S1 527 KB age 44.5 h
#     persym_size_tiers.json  Mac 179 KB                 |  S1 MISSING
# So the 19:27Z takeover ran on class/cat_side defaults, not on the per_sym best sets it is supposed to copytrade.
#
# per_sym_store.db is 1.15 GB because per_sym_history holds 11,339 rows of self-improvement data that live NEVER
# reads. Only per_sym_active (740 rows, the applied sets) and kv_json are needed to trade, so this exports just those:
# 209 MB, 1.2 s. The export is written to a temp file and rsynced (delta + compressed), and rsync renames into place,
# so S1 never reads a half-written db. Only ~/binance (the LIVE tree) is touched; ~/binance-sandbox keeps its own
# full db with history for the orchestrator.
#
# Lease-independent on purpose: these are Mac-produced research outputs, never account state, so S1 should always hold
# the newest regardless of who is trading. Account folders are a different thing and stay on the agent's staged
# mac_state push (every 30 s while the Mac holds the lease; S1 merges them at takeover).
set -u
BASE=/Users/niels/Documents/binance
SCRATCH="${MAC_PERSYM_TMP:-/tmp}"
DB_SRC="$BASE/data/hourly_reconfig/per_sym_store.db"
DB_EXPORT="$SCRATCH/persym_live_export.db"
STAMP="$SCRATCH/.persym_db_pushed_at"
DB_EVERY="${MAC_PERSYM_DB_EVERY_S:-600}"
LOCK=/tmp/.mac_persym_push_s1.lock
/usr/bin/shlock -f "$LOCK" -p $$ || exit 0
trap 'rm -f "$LOCK"' EXIT INT TERM
cd "$BASE" || exit 1

SSH='ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new -o ControlPath=none'
pick_host() {
  for H in s1-pub s1-int; do
    if $SSH "$H" true 2>/dev/null; then echo "$H"; return 0; fi
  done
  return 1
}
HOST=$(pick_host) || { echo "$(date -u +%FT%TZ) S1 unreachable (s1-pub and s1-int)"; exit 0; }

# ── small files every run ──────────────────────────────────────────────────────
SMALL=""
for f in data/per_sym_settings.json data/persym_size_tiers.json data/cat_side_defaults_4.json \
         tradeable_keys.json managed_stops.json data/confirmed_365d.json data/excluded_tokenized_stocks.json; do
  [ -f "$BASE/$f" ] && SMALL="$SMALL $f"
done
if [ -n "$SMALL" ]; then
  if rsync -az --relative --timeout=30 -e "$SSH" $(for f in $SMALL; do echo "$BASE/./$f"; done) "$HOST:~/binance/" 2>/dev/null; then
    echo "$(date -u +%FT%TZ) small settings pushed to $HOST:$(echo $SMALL | wc -w | tr -d ' ') files"
  else
    echo "$(date -u +%FT%TZ) SMALL PUSH FAILED to $HOST"
  fi
fi

# ── live per_sym db, throttled ─────────────────────────────────────────────────
last=0
[ -f "$STAMP" ] && last=$(cat "$STAMP" 2>/dev/null || echo 0)
now=$(date +%s)
[ "$(( now - last ))" -lt "$DB_EVERY" ] && exit 0
[ -f "$DB_SRC" ] || { echo "$(date -u +%FT%TZ) no $DB_SRC"; exit 0; }

/opt/anaconda3/envs/binance_env/bin/python - "$DB_SRC" "$DB_EXPORT" <<'PY' || exit 0
import os, sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
tmp = dst + ".building"
for p in (tmp, dst + ".new"):
    if os.path.exists(p):
        os.remove(p)
con = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=60)
con.execute("ATTACH DATABASE ? AS out", (tmp,))
for tbl in ("per_sym_active", "kv_json"):
    row = con.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (tbl,)).fetchone()
    if not row:
        continue
    ddl = row[0]
    con.execute(ddl.replace(f'"{tbl}"', f'out."{tbl}"', 1).replace(f"CREATE TABLE {tbl}", f"CREATE TABLE out.{tbl}", 1))
    con.execute(f'INSERT INTO out."{tbl}" SELECT * FROM main."{tbl}"')
con.commit()
con.execute("DETACH DATABASE out")
con.close()
os.replace(tmp, dst)
print(f"export {os.path.getsize(dst)/1e6:.1f} MB")
PY

# the per-sym full-set JSON (USER 2026-10-15: "another json file that has the full dict ... for every single sym_side
# that has ever been tested"). Rebuilt only when the db changed, so the rsync no-ops otherwise.
/opt/anaconda3/envs/binance_env/bin/python "$BASE/tools/build_per_sym_full_sets.py" --quiet 2>/dev/null
for f in data/per_sym_full_sets.json data/per_sym_full_sets.json.gz; do
  [ -f "$BASE/$f" ] || continue
  rsync -az --timeout=600 -e "$SSH" "$BASE/$f" "$HOST:~/binance/$f" 2>/dev/null \
    && echo "$(date -u +%FT%TZ) $f -> $HOST" || echo "$(date -u +%FT%TZ) PUSH FAILED $f"
done

if rsync -az --timeout=600 -e "$SSH" "$DB_EXPORT" "$HOST:~/binance/data/hourly_reconfig/per_sym_store.db" 2>/dev/null; then
  echo "$now" > "$STAMP"
  echo "$(date -u +%FT%TZ) per_sym live db pushed to $HOST ($(du -m "$DB_EXPORT" | awk '{print $1}') MB)"
else
  echo "$(date -u +%FT%TZ) PER_SYM DB PUSH FAILED to $HOST — S1 would trade on defaults, not per_sym sets"
fi
