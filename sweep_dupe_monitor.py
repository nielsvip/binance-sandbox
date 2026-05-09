#!/usr/bin/env python3
"""sweep_dupe_monitor — flag duplicate / suspicious sweep result rows.

Scans data/sweep_results/*.csv and per_sym_*.log on S1 for:
1. Identical (pool_sharpe, trades, max_dd_pct) across DIFFERENT variants in the
   same CSV — suggests the knob isn't actually being applied (engine cache /
   override loader bug).
2. All-zero rows clustered together — engine producing 0 trades for many syms /
   variants in a row (likely sample window too short, or override broken).

Run from cron (e.g. */15 * * * *) or ad-hoc. Exits 0 always; emits ALERTs to
stderr + appends to data/sweep_results/duplicate_alerts.log.
"""
from __future__ import annotations
import csv
import sys
import os
import glob
import json
import time
from collections import defaultdict
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parent
SWEEP_DIR = ROOT / 'data' / 'sweep_results'
ALERT_LOG = SWEEP_DIR / 'duplicate_alerts.log'
NOW = datetime.now(timezone.utc)
RECENT_HRS = 24.0  # only scan CSVs modified within this window
MIN_DUPS_TO_ALERT = 3  # require at least this many identical rows to flag

def _alert(msg: str):
    line = f"[{NOW.isoformat()}] {msg}"
    print(line, file=sys.stderr)
    try:
        with open(ALERT_LOG, 'a') as f:
            f.write(line + '\n')
    except Exception:
        pass

def _scan_csv(path: Path):
    try:
        rows = list(csv.DictReader(open(path)))
    except Exception as e:
        return None
    if not rows: return None
    # Bucket by canonical metric tuple (pool_sharpe, trades, max_dd_pct)
    sig_to_labels = defaultdict(set)
    zero_count = 0
    for r in rows:
        try:
            ps = round(float(r.get('pool_sharpe', 0) or 0), 4)
            tr = int(float(r.get('trades', 0) or 0))
            dd = round(float(r.get('max_dd_pct', 0) or 0), 4)
        except Exception:
            continue
        label = r.get('label') or r.get('variant') or r.get('iter', '?')
        sig_to_labels[(ps, tr, dd)].add(label)
        if tr == 0 and ps == 0.0:
            zero_count += 1
    return {'path': str(path), 'n_rows': len(rows), 'zero_rows': zero_count,
            'sig_to_labels': sig_to_labels}

def _scan_logs():
    """Scan per_sym profiler logs for repeated 0-trade patterns."""
    log_dir = Path('/Users/niels/logs') if Path('/Users/niels/logs').exists() else Path.home() / 'logs'
    if not log_dir.exists(): return []
    cutoff = time.time() - RECENT_HRS * 3600
    findings = []
    for p in log_dir.glob('per_sym_*.log'):
        try:
            if p.stat().st_mtime < cutoff: continue
            text = p.read_text(errors='ignore')
        except Exception: continue
        # Count consecutive "no_jsonl" or "tr=     0" lines
        lines = text.splitlines()
        zero_streak = 0
        max_zero_streak = 0
        for ln in lines:
            if 'no_jsonl' in ln or 'tr=     0' in ln or 'tr=0 ' in ln:
                zero_streak += 1
                max_zero_streak = max(max_zero_streak, zero_streak)
            else:
                zero_streak = 0
        if max_zero_streak >= 10:
            findings.append((p.name, max_zero_streak))
    return findings

def main():
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - RECENT_HRS * 3600
    csv_files = [Path(p) for p in glob.glob(str(SWEEP_DIR / '*.csv'))
                 if Path(p).stat().st_mtime >= cutoff]
    if not csv_files:
        print(f"[sweep_dupe_monitor] no CSVs modified in last {RECENT_HRS}h")
        return 0
    print(f"[sweep_dupe_monitor] scanning {len(csv_files)} recent CSVs")
    flagged = 0
    for path in csv_files:
        res = _scan_csv(path)
        if not res: continue
        # Look for tuples shared by ≥ MIN_DUPS_TO_ALERT distinct labels
        for sig, labels in res['sig_to_labels'].items():
            ps, tr, dd = sig
            # Skip the all-zero bucket — handled separately
            if tr == 0 and ps == 0.0: continue
            if len(labels) >= MIN_DUPS_TO_ALERT:
                _alert(f"DUPE_SIG path={path.name} n_labels={len(labels)} "
                       f"pool_sharpe={ps} trades={tr} dd={dd:.2f}% "
                       f"first_labels={list(labels)[:5]}")
                flagged += 1
        # Zero-row cluster
        if res['zero_rows'] >= 10 and res['zero_rows'] >= 0.5 * res['n_rows']:
            _alert(f"ZERO_TRADE_CLUSTER path={path.name} zero={res['zero_rows']}/{res['n_rows']} rows — "
                   f"engine likely producing no trades (window too short / broken override)")
            flagged += 1
    # Logs
    log_findings = _scan_logs()
    for name, streak in log_findings:
        _alert(f"LOG_ZERO_STREAK file={name} consecutive_zero_or_no_jsonl={streak}")
        flagged += 1
    if flagged == 0:
        print(f"[sweep_dupe_monitor] OK — no duplicates / zero-clusters detected")
    else:
        print(f"[sweep_dupe_monitor] {flagged} alert(s) — see {ALERT_LOG}")
    return 0

if __name__ == '__main__':
    sys.exit(main())
