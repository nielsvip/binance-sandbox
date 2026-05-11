#!/usr/bin/env python3
"""rank_knob_impact.py — read canonical_audit_full CSV → rank knobs by impact.

After `backtest_v8_sweep.py --tier canonical_audit_full` completes, run this to
produce `data/knob_impact_<ts>.json` ranking every knob by |delta_pool_sharpe|
and |delta_gain_pct| vs the baseline row.

Output groups:
- LIVE knobs (|delta_sharpe| >= 0.02 OR |delta_gain| >= 0.1%) — sorted by impact
- DEAD knobs (variant essentially identical to baseline) — flagged for FIX per CLAUDE.md
- POSITIVE_LIFT (delta_sharpe > 0) — promote candidates
- NEGATIVE_LIFT — knobs to keep at current default (or in their original state)

Usage:
    python3 rank_knob_impact.py <audit_csv_path>
"""
import csv, json, sys, math, os
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

def read_csv(p):
    with open(p) as f:
        return list(csv.DictReader(f))

def f(v, d=0.0):
    try: return float(v)
    except: return d

def main():
    if len(sys.argv) < 2:
        sys.exit("usage: rank_knob_impact.py <csv>")
    path = sys.argv[1]
    rows = read_csv(path)
    if not rows:
        sys.exit("empty CSV")
    print(f"Loaded {len(rows)} rows from {path}")
    baseline = next((r for r in rows if r.get('label') == 'baseline'), None)
    if not baseline:
        sys.exit("no 'baseline' row")
    b_sharpe = f(baseline['pool_sharpe'])
    b_gain = f(baseline.get('gain_pct') or baseline.get('total_gain_pct') or 0)
    b_trades = f(baseline['trades'])
    b_wr = f(baseline.get('win_rate') or baseline.get('wr_pct') or 0)
    print(f"Baseline: pool_sharpe={b_sharpe:.4f} trades={b_trades} wr={b_wr:.1f}% gain={b_gain:.2f}%")
    print()
    impacts = []
    for r in rows:
        label = r.get('label', '')
        if label == 'baseline':
            continue
        if int(f(r.get('rc') or 0)) != 0:
            continue
        sh = f(r['pool_sharpe'])
        gp = f(r.get('gain_pct') or r.get('total_gain_pct') or 0)
        tr = f(r['trades'])
        wr = f(r.get('win_rate') or r.get('wr_pct') or 0)
        d_sh = sh - b_sharpe
        d_gp = gp - b_gain
        d_tr = tr - b_trades
        d_wr = wr - b_wr
        impacts.append({
            'label': label,
            'pool_sharpe': round(sh, 4),
            'd_sharpe': round(d_sh, 4),
            'trades': int(tr),
            'd_trades': int(d_tr),
            'wr_pct': round(wr, 1),
            'd_wr': round(d_wr, 1),
            'gain_pct': round(gp, 2),
            'd_gain': round(d_gp, 2),
            'overrides_json': r.get('overrides_json', ''),
        })
    DEAD_THRESHOLD = 0.005  # |delta_sharpe| < this AND |delta_gain| < 0.05 = dead
    GAIN_DEAD = 0.05
    live = [i for i in impacts if abs(i['d_sharpe']) >= DEAD_THRESHOLD or abs(i['d_gain']) >= GAIN_DEAD]
    dead = [i for i in impacts if i not in live]
    live.sort(key=lambda x: abs(x['d_sharpe']) + abs(x['d_gain']) / 5.0, reverse=True)
    positive = [i for i in live if i['d_sharpe'] > 0]
    negative = [i for i in live if i['d_sharpe'] < 0]
    out = {
        '_meta': {
            'audit_csv': str(path),
            'generated_at_utc': datetime.now(timezone.utc).isoformat(),
            'baseline': {
                'pool_sharpe': b_sharpe, 'trades': int(b_trades),
                'wr_pct': b_wr, 'gain_pct': b_gain,
            },
            'thresholds': {'dead_d_sharpe_abs': DEAD_THRESHOLD, 'dead_d_gain_abs': GAIN_DEAD},
            'total_variants': len(impacts),
        },
        'live_count': len(live),
        'dead_count': len(dead),
        'positive_lift_count': len(positive),
        'top_promote_candidates': positive[:20],
        'live_knobs_sorted_by_impact': live,
        'dead_knobs_FIX_REQUIRED_per_CLAUDE_md': [d['label'] for d in dead],
        'negative_lift_keep_default': negative[:20],
    }
    ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
    out_path = Path(f'/Users/niels/Documents/binance/data/knob_impact_{ts}.json')
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w') as f_:
        json.dump(out, f_, indent=2)
    print(f"Written: {out_path}")
    print()
    print(f"=== TOP 10 LIVE KNOBS by impact ===")
    for i, k in enumerate(live[:10], 1):
        print(f"  {i:2d}. d_sharpe={k['d_sharpe']:+.4f} | d_gain={k['d_gain']:+.2f}% | tr={k['trades']:>5d} ({k['d_trades']:+d}) | wr={k['wr_pct']:5.1f}% | {k['label']}")
    print()
    print(f"=== DEAD KNOB COUNT: {len(dead)} (need FIX per CLAUDE.md) ===")
    if dead:
        for d in dead[:10]:
            print(f"  - {d['label']}")
        if len(dead) > 10:
            print(f"  ... and {len(dead)-10} more")
    return 0

if __name__ == '__main__':
    sys.exit(main())
