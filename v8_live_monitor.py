#!/usr/bin/env python3
"""
V8 Live Results Monitor — Real-time watch for 0-trades, discrepancies, syncing.

Monitors v8_engine results as they complete and:
1. ALERTS on 0 trades (stops processing immediately)
2. ALERTS on big discrepancies (vectorized sharpe vs v8 sharpe >0.5 diff)
3. Mirrors results CSVs to MacBook
4. Syncs results to central test_results_central.db
5. Provides live progress dashboard

Usage:
  python3 v8_live_monitor.py --s1-results-dir /home/niels/binance/data/v8_live_results/
"""
import argparse
import csv
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("v8_monitor")

class V8Monitor:
    def __init__(self, s1_results_dir: str, central_db: str = "/Users/niels/Documents/binance/data/test_results_central.db"):
        self.s1_results_dir = Path(s1_results_dir)
        self.central_db = Path(central_db)
        self.mac_results_dir = Path("/Users/niels/Documents/binance/data/v8_live_results")

        # Load vectorized results for comparison
        self.vectorized = self._load_vectorized()

        # Tracking
        self.seen_symbols = set()
        self.critical_issues = []

    def _load_vectorized(self) -> Dict[str, Dict]:
        """Load vectorized backtest results from persym_apply_audit.jsonl."""
        vectorized = {}
        audit_file = Path("/Users/niels/Documents/binance/data/persym_apply_audit.jsonl")

        if not audit_file.exists():
            log.warning(f"Vectorized audit not found: {audit_file}")
            return vectorized

        try:
            with open(audit_file) as f:
                for line in f:
                    entry = json.loads(line)
                    key = entry['key']
                    vectorized[key] = {
                        'sharpe': entry.get('new_sharpe'),
                        'trades': entry.get('new_trades'),
                        'gain': entry.get('new_pnl_pct'),
                    }
        except Exception as e:
            log.error(f"Failed to load vectorized: {e}")

        return vectorized

    def monitor(self, interval_seconds: int = 10):
        """Monitor results in real-time."""
        log.info(f"🔍 V8 LIVE MONITOR STARTED")
        log.info(f"   S1 results: {self.s1_results_dir}")
        log.info(f"   Central DB: {self.central_db}")
        log.info(f"   MacBook sync: {self.mac_results_dir}")
        log.info(f"   Check interval: {interval_seconds}s")

        last_sync = 0

        while True:
            try:
                self.check_results()

                # Periodic sync
                now = time.time()
                if now - last_sync > 60:  # Sync every minute
                    self.sync_to_mac()
                    self.sync_to_central_db()
                    last_sync = now

                # Check for critical issues
                if self.critical_issues:
                    self.alert_critical()

                time.sleep(interval_seconds)

            except KeyboardInterrupt:
                log.info("🛑 Monitor stopped")
                break
            except Exception as e:
                log.error(f"Monitor error: {e}", exc_info=True)
                time.sleep(interval_seconds)

    def check_results(self):
        """Check S1 results for issues."""
        # Read latest CSV from each supervisor
        result_files = [
            self.s1_results_dir / "v8_live_results.csv",
            self.s1_results_dir / "v8_part1_results.csv",
            self.s1_results_dir / "v8_part2_results.csv",
        ]

        for csv_file in result_files:
            if not csv_file.exists():
                continue

            try:
                with open(csv_file) as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        symbol_side = row.get('symbol_side', '')
                        if not symbol_side or symbol_side in self.seen_symbols:
                            continue

                        self.seen_symbols.add(symbol_side)
                        status = row.get('status', '')

                        # Check for completion
                        if status == 'success':
                            self._check_success_result(symbol_side, row)
                        elif status == 'error':
                            self._check_error_result(symbol_side, row)

            except Exception as e:
                log.warning(f"Could not read {csv_file}: {e}")

    def _check_success_result(self, symbol_side: str, row: Dict):
        """Check a successful result for issues."""
        trades = row.get('trades', '')
        sharpe = row.get('sharpe', '')

        # CRITICAL: 0 trades
        if trades == '0':
            issue = f"🔴 0 TRADES: {symbol_side} | vectorized={self.vectorized.get(symbol_side, {}).get('trades')} | v8=0"
            log.error(issue)
            self.critical_issues.append(issue)
            return

        # WARNING: Discrepancy
        if sharpe and symbol_side in self.vectorized:
            vec_sharpe = self.vectorized[symbol_side].get('sharpe') or 0
            v8_sharpe = float(sharpe) if sharpe else 0
            discrepancy = abs(vec_sharpe - v8_sharpe)

            if discrepancy > 0.5:  # Threshold: 0.5 sharpe difference
                issue = f"⚠️  DISCREPANCY: {symbol_side} | vectorized_sharpe={vec_sharpe:.4f} | v8_sharpe={v8_sharpe:.4f} | diff={discrepancy:.4f}"
                log.warning(issue)
                self.critical_issues.append(issue)
            else:
                # Good match
                log.info(f"✅ {symbol_side} | sharpe={v8_sharpe:.4f} | trades={trades} | discrepancy={discrepancy:.4f}")

    def _check_error_result(self, symbol_side: str, row: Dict):
        """Check an error result."""
        error_msg = row.get('error_msg', 'unknown')
        log.error(f"❌ {symbol_side} ERROR: {error_msg}")

    def alert_critical(self):
        """Alert on critical issues and possibly stop."""
        if not self.critical_issues:
            return

        log.critical(f"\n{'='*80}")
        log.critical(f"🚨 CRITICAL ISSUES DETECTED - MANUAL INTERVENTION REQUIRED:")
        log.critical(f"{'='*80}")
        for issue in self.critical_issues[-10:]:  # Show last 10
            log.critical(issue)
        log.critical(f"{'='*80}\n")

        # For 0-trades: stop immediately
        zero_trades = [i for i in self.critical_issues if '0 TRADES' in i]
        if zero_trades:
            log.critical(f"\n🛑 STOPPING MONITOR - 0 TRADES DETECTED:\n" + '\n'.join(zero_trades))
            sys.exit(1)

    def sync_to_mac(self):
        """Mirror result CSVs from S1 to MacBook."""
        s1_host = "localhost"
        s1_port = 2201
        s1_user = "niels"

        result_files = [
            "v8_live_results.csv",
            "v8_part1_results.csv",
            "v8_part2_results.csv",
        ]

        for fname in result_files:
            s1_path = f"{s1_user}@{s1_host}:{self.s1_results_dir / fname}"
            mac_path = str(self.mac_results_dir / fname)

            try:
                cmd = ["scp", "-P", str(s1_port), "-q", s1_path, mac_path]
                subprocess.run(cmd, timeout=10, check=True, capture_output=True)
                log.debug(f"✅ Synced {fname} to Mac")
            except Exception as e:
                log.warning(f"Failed to sync {fname}: {e}")

    def sync_to_central_db(self):
        """Sync results to central test_results_central.db."""
        if not self.central_db.exists():
            log.warning(f"Central DB not found: {self.central_db}")
            return

        # Read each result CSV
        result_files = [
            self.s1_results_dir / "v8_live_results.csv",
            self.s1_results_dir / "v8_part1_results.csv",
            self.s1_results_dir / "v8_part2_results.csv",
        ]

        synced_count = 0

        for csv_file in result_files:
            if not csv_file.exists():
                continue

            try:
                db = sqlite3.connect(str(self.central_db))

                # Ensure v8_live_tests table exists (for reference)
                db.execute("""
                    CREATE TABLE IF NOT EXISTS v8_live_tests (
                        id INTEGER PRIMARY KEY,
                        symbol TEXT,
                        mode TEXT,
                        account TEXT,
                        status TEXT,
                        sharpe REAL,
                        gain_pct REAL,
                        trades INTEGER,
                        max_dd_pct REAL,
                        error_msg TEXT,
                        started_at TEXT,
                        completed_at TEXT,
                        UNIQUE(symbol, mode, account)
                    )
                """)

                with open(csv_file) as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        if row.get('status') == 'success':
                            db.execute("""
                                INSERT OR REPLACE INTO v8_live_tests
                                (symbol, mode, account, status, sharpe, gain_pct, trades, max_dd_pct, started_at, completed_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                row.get('symbol_side'),
                                'tradier',
                                'trb',
                                row.get('status'),
                                float(row.get('sharpe', 0)) if row.get('sharpe') else None,
                                float(row.get('gain_pct', 0)) if row.get('gain_pct') else None,
                                int(row.get('trades', 0)) if row.get('trades') else None,
                                float(row.get('max_dd_pct', 0)) if row.get('max_dd_pct') else None,
                                row.get('started_at'),
                                row.get('completed_at'),
                            ))
                            synced_count += 1

                db.commit()
                db.close()

            except Exception as e:
                log.warning(f"Failed to sync {csv_file} to central DB: {e}")

        if synced_count > 0:
            log.debug(f"✅ Synced {synced_count} results to central DB")

def main():
    parser = argparse.ArgumentParser(description="V8 Live Results Monitor")
    parser.add_argument("--s1-results-dir", required=True, help="S1 results directory")
    parser.add_argument("--interval", type=int, default=10, help="Check interval (seconds)")

    args = parser.parse_args()

    monitor = V8Monitor(args.s1_results_dir)
    monitor.monitor(args.interval)

if __name__ == "__main__":
    main()
