#!/bin/bash
PDIR=$(head -n 1 "$HOME/v15_current_progress_dir.txt" 2>/dev/null)
[[ "$PDIR" == /home/niels/v15_run*/progress ]] || exit 2
ls "$PDIR"/*_SHORT_v14_progress.json 2>/dev/null | sed 's/.*progress\///;s/_v14.*//' | grep -vE 'USDT_|USDC_' | sort -u > /tmp/npz_sides_s6.txt
wc -l /tmp/npz_sides_s6.txt
grep -cE 'USDT_|USDC_' /tmp/npz_sides_s6.txt || true
