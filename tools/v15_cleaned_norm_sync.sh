#!/bin/bash
# normalise the user's FINAL SPREADSHEETS/TEMPLATE_*.xlsx (is_default markers only, rows untouched) into SPREADSHEETS/TEMPLATE_FINAL_NORM and push it to s1/s2/s5 (the sweep reads it). Idempotent, lockdir singleton.
L=/tmp/v15_cleaned_norm_sync.lockdir; if ! mkdir "$L" 2>/dev/null; then [ -n "$(find "$L" -mmin +10 2>/dev/null)" ] && rmdir "$L"; exit 0; fi; trap 'rmdir "$L"' EXIT
cd /Users/niels/Documents/binance || exit 1
/opt/anaconda3/envs/binance_env/bin/python tools/v15_template_normalize_defaults.py --src SPREADSHEETS --out SPREADSHEETS/TEMPLATE_FINAL_NORM >> /tmp/v15_cleaned_norm_sync.log 2>&1
for h in s1-pub s2 s5; do
  rsync -az -e "ssh -S none -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10" SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_*.xlsx $h:~/binance-sandbox/SPREADSHEETS/TEMPLATE_FINAL_NORM/ >> /tmp/v15_cleaned_norm_sync.log 2>&1
done
