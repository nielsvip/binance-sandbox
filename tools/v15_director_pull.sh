#!/bin/bash
# v15_director_pull — Mac side of the fleet director (USER 2026-10-08): every 15 min pull DIRECTOR_STATUS.{json,md} from S1 into
# data/daily_chain/ and append the Mac-evaluated go-live verdict (persym_golive_<date>.json, step5b) so the status is complete.
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin
ROOT=/Users/niels/Documents/binance; cd $ROOT || exit 1
# the go-live ledger is written on the Mac (step5b); push it to S1 so the director's "applied live 24h" counts real applies
rsync -az -e "ssh -o BatchMode=yes -o ConnectTimeout=10" data/parity_promotions.jsonl "s1-int:binance-sandbox/data/parity_promotions.jsonl" 2>/dev/null
rsync -az -e "ssh -o BatchMode=yes -o ConnectTimeout=10" "s1-int:binance-sandbox/data/daily_chain/DIRECTOR_STATUS.json" "s1-int:binance-sandbox/data/daily_chain/DIRECTOR_STATUS.md" data/daily_chain/ 2>/dev/null || exit 0
G=$(ls -t data/daily_chain/persym_golive_*.json 2>/dev/null | head -1)
if [ -n "$G" ]; then
  .venv/bin/python - "$G" <<'PY' >> data/daily_chain/DIRECTOR_STATUS.md
import json,sys; d=json.load(open(sys.argv[1]))
print("\n## Go-live (Mac, step5b)\n")
print(f"- last run: {sys.argv[1]} mode={d.get('mode')} qualified={d.get('pass_qualified')} unqualified={d.get('pass_unqualified')} negbook_blocked={d.get('pass_negative_gain_negbook_blocked_live')} applied={d.get('applied', d.get('n_applied'))}")
PY
fi
