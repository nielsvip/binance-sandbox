#!/bin/bash
# Rebuild the s6 dispatch window from local progress files. APPEND-ONLY merge
# (2026-10-25): previously overwrote /tmp/npz_sides_s6.txt, which dropped newly
# seeded symbols that had not written progress yet. Existing lines are kept.
_MERGE_TMP=$(mktemp)
ls /home/niels/v15_run36_20261024/progress/*_SHORT_v14_progress.json 2>/dev/null | sed 's/.*progress\///;s/_v14.*//' | grep -vE 'USDT|USDC$' | sort -u > "$_MERGE_TMP"
cat /tmp/npz_sides_s6.txt 2>/dev/null >> "$_MERGE_TMP"
sort -u "$_MERGE_TMP" -o /tmp/npz_sides_s6.txt
rm -f "$_MERGE_TMP"
wc -l /tmp/npz_sides_s6.txt
head -n 4 /tmp/npz_sides_s6.txt
