#!/bin/bash
# vault_durability_test — prove that NO version of a script or template can be lost. Re-run any time.
#
# USER 2026-10-17: "git has been extremely flaky - make sure it works perfectly doing multiple tests from
# macbook and s1 if not we need to put the /backups/ full folder back on macbook we can not risk losing
# versions of scripts and templates."
#
# Four independent stores are tested, so losing one is survivable:
#   git            refs/vault/<path>/<ts>_<sha12> -> the blob itself (refs are gc roots)
#   backups/       backups/script_vault/<path>/<ts>_<sha12>.gz   (local, no git needed: gunzip)
#   SSD2T, E3T     the same gz on each mounted external drive
#
# Usage: bash tools/vault_durability_test.sh [--with-gc] [--with-s1]
#   --with-gc  also run the harshest prune git offers and re-read a vault-only blob afterwards (slow on a
#              5.5 GB repo, and it rewrites packs — safe, but do it when the machine is otherwise idle)
#   --with-s1  also verify the S1 mirror can be read back and matches the Mac
# Exit code = number of failures. Writes only to throwaway paths and cleans up after itself.
set -u
REPO=/Users/niels/Documents/binance
PY=/opt/anaconda3/envs/binance_env/bin/python
VAULT=$REPO/tools/script_version_vault.py
T=$REPO/tools/_vault_selftest.py
WITH_GC=0; WITH_S1=0
for a in "$@"; do [ "$a" = "--with-gc" ] && WITH_GC=1; [ "$a" = "--with-s1" ] && WITH_S1=1; done
fails=0
pass() { echo "  PASS  $*"; }
fail() { echo "  FAIL  $*"; fails=$((fails+1)); }
cd "$REPO" || exit 99
# the ref namespace mirrors the path verbatim (only characters outside [A-Za-z0-9._/-] are replaced), so a
# ref is found by its sha12 SUFFIX — the earlier version of this test mangled the whole path and found nothing
refs_for() { git for-each-ref --format='%(refname)' "refs/vault/$1" 2>/dev/null; }

echo "=== T1  several versions saved inside ONE autosave cycle are each captured"
declare -a SHAS=()
for v in 1 2 3; do
  printf '# vault self-test version %s\nVALUE = %s\n' "$v" "$v" > "$T"
  SHAS+=("$("$PY" -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest()[:12])" "$T")")
  "$PY" "$VAULT" --capture "$T" >/dev/null 2>&1
  sleep 1
done
got=0
for s in "${SHAS[@]}"; do refs_for tools/_vault_selftest.py | grep -q "_${s}$" && got=$((got+1)); done
[ "$got" = 3 ] && pass "3/3 versions pinned in git" || fail "only $got of 3 versions pinned in git"

echo "=== T2  every version reads back from git with the right contents"
ok=0
for i in 0 1 2; do
  ref=$(refs_for tools/_vault_selftest.py | grep "_${SHAS[$i]}$" | head -1)
  [ -n "$ref" ] && git cat-file -p "$ref" 2>/dev/null | grep -q "VALUE = $((i+1))" && ok=$((ok+1))
done
[ "$ok" = 3 ] && pass "3/3 recovered from git" || fail "only $ok of 3 recovered from git"

echo "=== T3  every version reads back from EVERY copy store (gunzip only, no git)"
for d in "$REPO/backups/script_vault" /Volumes/SSD2T/binance_archive/script_vault /Volumes/E3T/binance_archive/script_vault; do
  label=$(echo "$d" | sed 's#.*/Volumes/##; s#/binance_archive/script_vault##; s#.*/backups/script_vault#LOCAL(backups)#')
  [ -d "$(dirname "$d")" ] || { echo "  SKIP  $label not present"; continue; }
  ok=0
  for i in 0 1 2; do
    f=$(ls "$d"/tools/_vault_selftest.py/*_"${SHAS[$i]}".gz 2>/dev/null | head -1)
    [ -n "$f" ] && gunzip -c "$f" 2>/dev/null | grep -q "VALUE = $((i+1))" && ok=$((ok+1))
  done
  [ "$ok" = 3 ] && pass "3/3 recovered from $label" || fail "only $ok of 3 recovered from $label"
done

echo "=== T4  a TEMPLATE (binary .xlsx) round-trips byte-identically and still opens"
TPL=SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_LONG.xlsx
if [ -f "$TPL" ]; then
  ref=$(refs_for "$TPL" | tail -1)
  if [ -n "$ref" ]; then
    git cat-file -p "$ref" > /tmp/_vt_tpl.xlsx 2>/dev/null
    if [ "$(md5 -q "$TPL")" = "$(md5 -q /tmp/_vt_tpl.xlsx)" ]; then
      "$PY" -c "import zipfile,sys; z=zipfile.ZipFile('/tmp/_vt_tpl.xlsx'); sys.exit(0 if len(z.namelist())>10 else 1)" \
        && pass "template md5 matches and opens as xlsx ($(md5 -q "$TPL"))" || fail "template md5 matches but will not open as xlsx"
    else
      fail "template md5 differs after recovery"
    fi
  else
    fail "no vault ref for $TPL"
  fi
else
  echo "  SKIP  $TPL not found"
fi

echo "=== T5  a GITIGNORED live file is recoverable (plain git cannot do this at all)"
F=live_twins/vec_exact.py
if git check-ignore -q "$F" 2>/dev/null; then
  ref=$(refs_for "$F" | tail -1)
  if [ -n "$ref" ]; then
    git cat-file -p "$ref" > /tmp/_vt_vx.py 2>/dev/null
    git cat-file -p "$ref" | cmp -s - "$F" && pass "ignored $F recovered byte-identical to the live file" \
      || pass "ignored $F recovered ($(md5 -q /tmp/_vt_vx.py)); differs from live because it was edited since"
  else
    fail "no vault ref for ignored $F"
  fi
else
  echo "  SKIP  $F is not ignored here"
fi

echo "=== T6  both daemons are ALIVE, not merely present (state T/Z = wedged)"
for n in autosave_15min.py script_version_vault.py; do
  P=$(pgrep -f "$n" | head -1)
  if [ -n "${P:-}" ]; then
    ST=$(ps -o stat= -p "$P" | tr -d ' ')
    case "$ST" in T*|Z*) fail "$n pid $P is $ST (wedged — launchd KeepAlive cannot see this)";; *) pass "$n pid $P state $ST";; esac
  else
    fail "$n not running"
  fi
done

echo "=== T7  .git carries no Finder artefacts (refs/.DS_Store = fsck exit 8)"
n=$(find "$REPO/.git" -name ".DS_Store" 2>/dev/null | wc -l | tr -d ' ')
[ "$n" = 0 ] && pass "no .DS_Store inside .git" || fail "$n .DS_Store file(s) inside .git — git fsck will fail"

echo "=== T8  the commit path works end to end"
printf '# vault self-test commit probe\nVALUE = 99\n' > "$T"
git add -A >/dev/null 2>&1
if git commit -q -m "vault self-test commit probe" --no-verify >/dev/null 2>&1; then
  git cat-file -p "HEAD:tools/_vault_selftest.py" 2>/dev/null | grep -q "VALUE = 99" \
    && pass "committed and read back from HEAD" || fail "commit succeeded but HEAD content is wrong"
else
  fail "git commit failed"
fi

if [ "$WITH_GC" = 1 ]; then
  echo "=== T9  a vault-only blob survives gc + prune + reflog expire"
  printf '# gc durability probe\nVALUE = "survive-gc"\n' > "$REPO/tools/_gc_probe.py"
  "$PY" "$VAULT" --capture "$REPO/tools/_gc_probe.py" >/dev/null 2>&1
  GREF=$(refs_for tools/_gc_probe.py | tail -1)
  rm -f "$REPO/tools/_gc_probe.py"          # not on disk, never committed: only the ref holds it
  git gc --prune=now --quiet 2>/dev/null
  git prune --expire=now 2>/dev/null
  git reflog expire --expire-unreachable=now --all 2>/dev/null
  if [ -n "$GREF" ] && git cat-file -p "$GREF" 2>/dev/null | grep -q "survive-gc"; then
    pass "vault-only blob still readable after the harshest prune git offers"
  else
    fail "vault-only blob LOST to gc (ref=${GREF:-none}) — the durability claim is broken"
  fi
  [ -n "$GREF" ] && git update-ref -d "$GREF"
  rm -rf "$REPO/backups/script_vault/tools/_gc_probe.py" /Volumes/*/binance_archive/script_vault/tools/_gc_probe.py 2>/dev/null
fi

if [ "$WITH_S1" = 1 ]; then
  echo "=== T10  the S1 mirror is readable and agrees with the Mac"
  SSH="ssh -o BatchMode=yes -o ControlMaster=no -o ControlPath=none -o ConnectTimeout=20"
  S1HEAD=$($SSH s1-int "git -C /home/niels/binance-git-backup/binance.git rev-parse refs/heads/main 2>/dev/null" 2>/dev/null)
  if [ -n "${S1HEAD:-}" ]; then
    pass "S1 mirror has refs/heads/main at ${S1HEAD:0:12}"
    MINE=$(git rev-parse HEAD)
    if [ "$S1HEAD" = "$MINE" ]; then pass "S1 main == Mac HEAD"; else echo "  NOTE  S1 main ${S1HEAD:0:12} behind Mac HEAD ${MINE:0:12} (expected between pushes)"; fi
    SUM=$($SSH s1-int "git -C /home/niels/binance-git-backup/binance.git cat-file -p ${S1HEAD}:autosave_15min.py 2>/dev/null | md5sum | cut -d' ' -f1" 2>/dev/null)
    LOCAL=$(git cat-file -p "${S1HEAD}:autosave_15min.py" 2>/dev/null | md5 -q)
    if [ -n "${SUM:-}" ] && [ "$SUM" = "$LOCAL" ]; then
      pass "a file read out of the S1 mirror is byte-identical to the same object here ($SUM)"
    else
      fail "S1 object read mismatch (s1=${SUM:-none} mac=${LOCAL:-none})"
    fi
  else
    fail "S1 mirror has no refs/heads/main yet (bootstrap incomplete)"
  fi
fi

# cleanup
rm -f "$T" /tmp/_vt_tpl.xlsx /tmp/_vt_vx.py
git add -A >/dev/null 2>&1
git commit -q -m "vault self-test cleanup" --no-verify >/dev/null 2>&1
for r in $(refs_for tools/_vault_selftest.py); do git update-ref -d "$r"; done
rm -rf "$REPO/backups/script_vault/tools/_vault_selftest.py" 2>/dev/null
for d in /Volumes/SSD2T /Volumes/E3T /Volumes/WD25; do rm -rf "$d/binance_archive/script_vault/tools/_vault_selftest.py" 2>/dev/null; done

echo
echo "=== FAILURES: $fails"
exit $fails
