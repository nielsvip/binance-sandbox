#!/usr/bin/env python3
"""
Hourly log triage — pure Python, NO Claude tokens.
Tails recent logs, finds errors/warnings, groups by pattern, reports.
No fixing — just reporting for user review.
"""
import os
import re
import sys
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

LOG_DIR = Path("/Users/niels/logs")
OUT_LOG = Path("/Users/niels/logs/hourly_triage.log")
OUT_LOG.parent.mkdir(parents=True, exist_ok=True)

def tail_log(logfile, lines=500):
    """Tail last N lines of a log file."""
    try:
        if not logfile.exists():
            return []
        with open(logfile, 'r', errors='ignore') as f:
            all_lines = f.readlines()
            return all_lines[-lines:] if len(all_lines) > lines else all_lines
    except Exception as e:
        return [f"ERROR reading {logfile}: {e}\n"]

def extract_errors(lines):
    """Find lines with ERROR, CRITICAL, EXCEPTION, Traceback."""
    errors = []
    for line in lines:
        if any(kw in line.upper() for kw in ['ERROR', 'CRITICAL', 'EXCEPTION', 'TRACEBACK']):
            errors.append(line.rstrip())
    return errors

def group_errors(errors):
    """Group errors by signature (first ~50 chars after timestamp)."""
    groups = defaultdict(list)
    for error in errors:
        sig = error[:100] if len(error) > 100 else error
        groups[sig].append(error)
    return groups

def triage_logs():
    """Analyze each log group."""
    ts = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

    sections = {
        'Tradier live trading': [
            'tradier_manage_trb.log',
            'tradier_manage_trc.log',
            'tradier_manage_wait_trb.log',
            'tradier_manage_wait_trc.log',
        ],
        'Tradier API + rankings': [
            'tradier_api.log',
            'tradier_rankings.log',
            'tradier_options_analyzer.log',
        ],
        'Crypto orders execution': [
            'ez_manage_ang.log',
            'ez_manage_flz.log',
            'ez_manage_fin.log',
            'ez_manage_men.log',
        ],
        'Price feed health': [
            'ez_market_data.log',
            'ez_klines.log',
            'ez_prices.log',
        ],
        'Position state + reentry': [
            'ez_positions_quick_ang.log',
            'ez_positions_quick_flz.log',
            'ez_positions_quick_fin.log',
            'ez_positions_quick_men.log',
            'ez_reentry_daemon.log',
        ],
    }

    report = []
    report.append("")
    report.append("════════════════════════════════════════════════════════════════════")
    report.append(f"[{ts}] Hourly Log Triage (Pure Python, NO CLAUDE TOKENS)")
    report.append("════════════════════════════════════════════════════════════════════")

    total_errors = 0
    for section, logfiles in sections.items():
        report.append(f"\n### {section}")
        section_errors = []

        for logname in logfiles:
            logfile = LOG_DIR / logname
            lines = tail_log(logfile, lines=1000 if 'market_data' in logname or 'klines' in logname or 'prices' in logname else 500)
            errors = extract_errors(lines)

            if errors:
                section_errors.extend(errors)
                report.append(f"\n  **{logname}**: {len(errors)} errors")
                groups = group_errors(errors)
                for sig, group in sorted(groups.items(), key=lambda x: -len(x[1]))[:3]:
                    report.append(f"    - [{len(group)}x] {sig[:80]}")

        if section_errors:
            total_errors += len(section_errors)
            report.append(f"  **TOTAL: {len(section_errors)} errors this hour**")
        else:
            report.append("  ✓ No errors found")

    report.append(f"\n\n════ SUMMARY ════")
    report.append(f"Total errors found: {total_errors}")
    report.append(f"Status: {'🟢 HEALTHY' if total_errors == 0 else '🔴 ISSUES FOUND — review logs above'}")
    report.append(f"Next triage: {(datetime.utcnow() + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    report.append(f"[{ts}] Hourly triage finished")
    report.append("")

    return '\n'.join(report)

if __name__ == '__main__':
    output = triage_logs()
    print(output)
    with open(OUT_LOG, 'a') as f:
        f.write(output)
