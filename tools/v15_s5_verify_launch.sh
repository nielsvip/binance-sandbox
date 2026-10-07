#!/bin/bash
# S5 live-verification fan-out. Resume-safe: existing results/*.json are skipped.
# Usage: bash tools/v15_s5_verify_launch.sh <manifest.json> [workers]
set -u
ROOT="$HOME/binance-sandbox"
RUN="$HOME/v15_s5verify_20261003"
MAN="${1:?manifest required}"
N="${2:-12}"
mkdir -p "$RUN/entries" "$RUN/results"
cd "$ROOT" || exit 1
python3 - "$MAN" "$RUN/entries" <<'PYEOF'
import json, os, sys
man = json.load(open(sys.argv[1]))
d = sys.argv[2]
for e in man["entries"]:
    p = os.path.join(d, e["symside"] + ".json")
    if not os.path.exists(p):
        open(p + ".tmp", "w").write(json.dumps(e))
        os.replace(p + ".tmp", p)
print(f"entries ready: {len(man['entries'])}")
PYEOF
python3 - "$MAN" <<'PYEOF' | xargs -P "$N" -I{} bash -c '
  out="'"$RUN"'/results/{}.json"
  [ -f "$out" ] && exit 0
  timeout 3600 "'"$ROOT"'/.venv/bin/python" "'"$ROOT"'/tools/v15_s5_verify_worker.py" --entry "'"$RUN"'/entries/{}.json" --out "$out" >>"'"$RUN"'/verify.log" 2>&1
  rc=$?
  if [ ! -f "$out" ]; then
    echo "{\"symside\":\"{}\",\"launcher_timeout_or_crash\":true,\"rc\":$rc}" > "$out"
    echo "[launcher] {} no result rc=$rc" >>"'"$RUN"'/verify.log"
  fi
'
import json, sys
man = json.load(open(sys.argv[1]))
for e in man["entries"]:
    print(e["symside"])
PYEOF
echo "launcher pass done: $(ls $RUN/results/*.json 2>/dev/null | wc -l) results"
