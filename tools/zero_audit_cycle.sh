#!/bin/bash
# one audit cycle over NEW-CODE sheets only (pilot >= 47794652): collect per host -> merge on the Mac -> append a line to data/zero_audit/LOG.md
cd /Users/niels/Documents/binance || exit 1
S=$(mktemp -d); parts=""
for h in s1-pub s2 s5; do
  rsync -az -e "ssh -S none -o StrictHostKeyChecking=accept-new" tools/v15_zero_audit.py tools/_zero_ast.py $h:~/binance-sandbox/tools/ >/dev/null 2>&1
  ssh -o StrictHostKeyChecking=accept-new $h 'cd ~/binance-sandbox && python3 tools/v15_zero_audit.py --new-code-only --emit-partial /tmp/zero_partial_new.json' >/dev/null 2>&1 && scp -q $h:/tmp/zero_partial_new.json $S/$h.json && parts="$parts,$S/$h.json"
done
/opt/anaconda3/envs/binance_env/bin/python tools/v15_zero_audit.py --merge "${parts#,}" --out-dir data/zero_audit/cycle_$(date -u +%Y%m%d_%H%M) --log data/zero_audit/LOG.md
