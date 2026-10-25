#!/bin/bash
ls /home/niels/v15_run36_20261024/progress/*_SHORT_v14_progress.json 2>/dev/null | sed 's/.*progress\///;s/_v14.*//' | grep -vE 'USDT_|USDC_' | sort -u > /tmp/npz_sides_s6.txt
wc -l /tmp/npz_sides_s6.txt
grep -cE 'USDT_|USDC_' /tmp/npz_sides_s6.txt || true
