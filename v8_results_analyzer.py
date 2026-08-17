#!/usr/bin/env python3
"""
V8 Results Analyzer — Compare backtest_v8_engine results vs vectorized.
Generate deployment-ready configs with alerts on anomalies.

Usage:
  python3 v8_results_analyzer.py --v8-csv v8_simple_results.csv
"""
import argparse
import csv
import json
import logging
from pathlib import Path
from typing import Dict, List, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("v8_analyzer")

class V8ResultsAnalyzer:
    def __init__(self, v8_csv: str):
        self.v8_csv = Path(v8_csv)
        self.v8_results = self._load_v8_results()
        self.vectorized = self._load_vectorized()
        self.issues = []

    def _load_v8_results(self) -> Dict[str, Dict]:
        """Load v8_engine backtest results from CSV."""
        results = {}
        if not self.v8_csv.exists():
            log.warning(f"V8 CSV not found: {self.v8_csv}")
            return results

        try:
            with open(self.v8_csv) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    symbol = row.get('symbol', '')
                    if symbol:
                        results[symbol] = {
                            'sharpe': float(row.get('sharpe', 0)) if row.get('sharpe') else None,
                            'trades': int(row.get('trades', 0)) if row.get('trades') else 0,
                            'gain': float(row.get('gain_pct', 0)) if row.get('gain_pct') else None,
                            'max_dd': float(row.get('max_dd', 0)) if row.get('max_dd') else None,
                            'status': row.get('status', 'unknown'),
                        }
        except Exception as e:
            log.error(f"Failed to load V8 results: {e}")

        log.info(f"Loaded {len(results)} V8 backtest results")
        return results

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

        log.info(f"Loaded {len(vectorized)} vectorized results")
        return vectorized

    def analyze(self):
        """Analyze all results for issues and discrepancies."""
        log.info("🔍 ANALYZING V8 BACKTEST RESULTS\n")

        zero_trades = []
        discrepancies = []
        errors = []
        successes = []

        for symbol, v8_result in sorted(self.v8_results.items()):
            status = v8_result.get('status', 'unknown')
            trades = v8_result.get('trades', 0)

            # Critical: 0 trades
            if trades == 0:
                zero_trades.append({
                    'symbol': symbol,
                    'status': status,
                    'v8_sharpe': v8_result.get('sharpe'),
                    'vec_sharpe': self.vectorized.get(symbol, {}).get('sharpe'),
                })
                self.issues.append(f"🔴 0-TRADES: {symbol}")
                continue

            # Error status
            if status not in ['success', '']:
                errors.append({
                    'symbol': symbol,
                    'status': status,
                    'error': v8_result.get('gain'),
                })
                self.issues.append(f"❌ ERROR: {symbol} ({status})")
                continue

            # Check discrepancies
            vec = self.vectorized.get(symbol, {})
            v8_sharpe = v8_result.get('sharpe') or 0
            vec_sharpe = vec.get('sharpe') or 0

            if vec_sharpe and abs(v8_sharpe - vec_sharpe) > 0.5:
                discrepancies.append({
                    'symbol': symbol,
                    'v8_sharpe': v8_sharpe,
                    'vec_sharpe': vec_sharpe,
                    'diff': abs(v8_sharpe - vec_sharpe),
                    'v8_trades': trades,
                    'vec_trades': vec.get('trades'),
                })
                self.issues.append(f"⚠️  DISCREPANCY: {symbol} (v8={v8_sharpe:.4f} vs vec={vec_sharpe:.4f})")
            else:
                successes.append(symbol)

        # Print summary
        self._print_summary(zero_trades, errors, discrepancies, successes)

        return {
            'zero_trades': zero_trades,
            'errors': errors,
            'discrepancies': discrepancies,
            'successes': successes,
            'issues': self.issues,
        }

    def _print_summary(self, zero_trades, errors, discrepancies, successes):
        """Print analysis summary."""
        total = len(self.v8_results)
        print(f"\n{'='*80}")
        print(f"V8 BACKTEST ANALYSIS SUMMARY")
        print(f"{'='*80}")
        print(f"Total results: {total}")
        print(f"✅ Successes (good): {len(successes)}")
        print(f"🔴 0-trades (CRITICAL): {len(zero_trades)}")
        print(f"❌ Errors: {len(errors)}")
        print(f"⚠️  Discrepancies (>0.5 sharpe): {len(discrepancies)}\n")

        if zero_trades:
            print(f"🔴 ZERO TRADES FAILURES ({len(zero_trades)}):")
            for item in zero_trades[:10]:
                print(f"   {item['symbol']:20} | vec_sharpe={item['vec_sharpe']}")
            if len(zero_trades) > 10:
                print(f"   ... and {len(zero_trades)-10} more")

        if discrepancies:
            print(f"\n⚠️  SHARPE DISCREPANCIES ({len(discrepancies)}):")
            for item in sorted(discrepancies, key=lambda x: x['diff'], reverse=True)[:10]:
                print(f"   {item['symbol']:20} | v8={item['v8_sharpe']:7.4f} | vec={item['vec_sharpe']:7.4f} | diff={item['diff']:7.4f}")
            if len(discrepancies) > 10:
                print(f"   ... and {len(discrepancies)-10} more")

        if errors:
            print(f"\n❌ ERRORS ({len(errors)}):")
            for item in errors[:5]:
                print(f"   {item['symbol']:20} | {item['status']}")
            if len(errors) > 5:
                print(f"   ... and {len(errors)-5} more")

        print(f"\n✅ PASS: {len(successes)} symbols ready for live\n")

    def generate_deployment_config(self, output_file: str):
        """Generate updated per_sym_final_book.json with v8 validation results."""
        # Load current config
        config_file = Path("/Users/niels/Documents/binance/data/persym_final_book.json")
        with open(config_file) as f:
            config = json.load(f)

        # Mark v8 validation status on each
        if 'tradeable' not in config:
            config['tradeable'] = {}

        v8_validated = 0
        v8_failed = 0

        for symbol, v8_result in self.v8_results.items():
            if symbol not in config['tradeable']:
                continue

            status = v8_result.get('status', '')
            trades = v8_result.get('trades', 0)

            if trades == 0 or 'error' in status:
                # Mark as unvalidated
                config['tradeable'][symbol]['v8_validation_status'] = 'FAILED'
                config['tradeable'][symbol]['v8_validation_reason'] = f"0 trades" if trades == 0 else status
                v8_failed += 1
            else:
                # Validated successfully
                config['tradeable'][symbol]['v8_validation_status'] = 'PASSED'
                config['tradeable'][symbol]['v8_sharpe'] = v8_result.get('sharpe')
                config['tradeable'][symbol]['v8_trades'] = trades
                v8_validated += 1

        # Save updated config
        with open(output_file, 'w') as f:
            json.dump(config, f, indent=2)

        log.info(f"\n✅ Generated deployment config: {output_file}")
        log.info(f"   V8 Validated: {v8_validated}")
        log.info(f"   V8 Failed: {v8_failed}")

def main():
    parser = argparse.ArgumentParser(description="V8 Results Analyzer")
    parser.add_argument("--v8-csv", required=True, help="V8 engine results CSV")
    parser.add_argument("--output", default="/tmp/v8_analysis_report.json")
    parser.add_argument("--deployment-config", default="/tmp/per_sym_final_book_v8_validated.json")

    args = parser.parse_args()

    analyzer = V8ResultsAnalyzer(args.v8_csv)
    results = analyzer.analyze()

    # Save report
    with open(args.output, 'w') as f:
        json.dump(results, f, indent=2)
    log.info(f"✅ Saved report: {args.output}")

    # Generate deployment config
    analyzer.generate_deployment_config(args.deployment_config)

if __name__ == "__main__":
    main()
