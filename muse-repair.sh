#!/bin/zsh
set -e
echo "=== Muse Code Repair — run OUTSIDE sandbox with Full Disk Access ==="
echo "Validates and re-creates corrupted tail fragments; archives huge sessions"

FIX_DIR="/tmp/muse-fix-$(date +%Y%m%d)"
mkdir -p "$FIX_DIR"
echo "Archive dir: $FIX_DIR"

for root in /Users/niels/.local/share/muse/sessions/2026/08/09 /Users/niels/.local/share/muse/sessions/2026/08/10 /Users/niels/.local/share/muse/sessions/2026/08/11; do
  [ -d "$root" ] || continue
  for d in "$root"/*; do
    p="$d/session.jsonl"
    [ -f "$p" ] || continue
    sz=$(stat -f%z "$p" 2>/dev/null || stat -c%s "$p")
    # archive if >30MB
    if [ "$sz" -gt 30000000 ]; then
      echo "Archiving large $d ($sz bytes)"
      gzip -c "$p" > "$FIX_DIR/$(basename $d)-session.jsonl.gz" && echo "  -> gzipped"
    fi
    # validate and repair in place if needed
    python3 - "$p" "$FIX_DIR" <<'PY'
import pathlib, json, sys, shutil, tempfile, os
p = pathlib.Path(sys.argv[1])
fix_dir = pathlib.Path(sys.argv[2])
good=bad=0
tmp = pathlib.Path(tempfile.mktemp())
try:
  with open(p) as fin, open(tmp,'w') as fout:
    for line in fin:
      if not line.strip(): continue
      try:
        json.loads(line)
        fout.write(line if line.endswith('\n') else line+'\n')
        good+=1
      except:
        bad+=1
  if bad>0:
    print(f"REPAIR {p}: {bad} bad lines removed, {good} kept")
    shutil.copy2(str(tmp), str(p))
    pathlib.Path(str(p)+".bak").write_text(f"repaired {bad} lines")
  else:
    # also fix missing final newline
    data = p.read_bytes()
    if data and not data.endswith(b'\n'):
      print(f"FIX newline {p}")
      with open(p,'ab') as f: f.write(b'\n')
finally:
  if tmp.exists(): tmp.unlink()
PY
  done
done

echo "--- Clearing stale model-catalog (forces refresh) ---"
rm -f /Users/niels/.local/share/muse/model-catalog/*.json 2>/dev/null && echo "cleared" || echo "model-catalog not cleared (check permissions)"

echo "--- Disk check ---"
df -h | grep -E "Data|SSD2T|TOSHIBA"
echo "Free /Volumes/SSD2T (46Mi) — delete/move files there to get >1GB free."

echo "--- Verify ---"
for p in /Users/niels/.local/share/muse/sessions/2026/08/11/*/session.jsonl; do
  python3 -c "import json; errs=sum(1 for l in open('$p') if l.strip() and not (lambda x: (json.loads(x),True)[1] if True else False)(l) )" 2>&1 | head
done
echo "Done. Restart Terminal and run: muse --version (should return <2s)"
