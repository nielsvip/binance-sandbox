#!/usr/bin/env python3
"""sweep_classify — post-process metrics_guard sweep CSVs and route variants.

Per user mandate 2026-05-09:
  - Every switch tested at 12 syms × 4 months (cheap diagnostic tier).
  - pool_sharpe < SWEEP_DISCARD_POOL_SHARPE_FLOOR (default 0.4) → DISCARD.
  - pool_sharpe ≥ SWEEP_DEEP_TEST_POOL_SHARPE_MIN (default 0.5) AND
    gain_per_mo ≥ SWEEP_DEEP_TEST_GAIN_PER_MO_MIN_PCT (default 1.0%) → tag
    DEEP_TEST_CANDIDATE so the 4 yr × 48 sym pipeline picks it up.

Outputs:
  - data/sweep_results/sweep_classify_DISCARD_<ts>.csv         (tier=Discard rows)
  - data/sweep_results/sweep_classify_DEEP_CANDIDATES_<ts>.csv (next-stage rows)
  - data/sweep_results/sweep_classify_<ts>.log                 (audit trail)

Run cron'd or ad-hoc. NEVER auto-promotes to live config — this only routes
variants between sweep tiers.
"""
from __future__ import annotations
import csv
import sys
import os
import glob
import time
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
SWEEP_DIR = ROOT / 'data' / 'sweep_results'

try:
    sys.path.insert(0, str(ROOT))
    import config as _cfg
    cfg = _cfg.Config()
    DISCARD_FLOOR = float(getattr(cfg, 'SWEEP_DISCARD_POOL_SHARPE_FLOOR', 0.4))
    DEEP_MIN = float(getattr(cfg, 'SWEEP_DEEP_TEST_POOL_SHARPE_MIN', 0.5))
    GAIN_MO_MIN = float(getattr(cfg, 'SWEEP_DEEP_TEST_GAIN_PER_MO_MIN_PCT', 1.0))
except Exception:
    DISCARD_FLOOR = 0.4
    DEEP_MIN = 0.5
    GAIN_MO_MIN = 1.0

NOW = datetime.now(timezone.utc)
TS = NOW.strftime('%Y%m%d_%H%M%S')
RECENT_HRS = 36.0  # only scan recent CSVs

CANONICAL_FIELDS = [
    'pool_sharpe', 'sym_sharpe', 'avg_gain_trade', 'gain_per_yr', 'gain_sym_yr',
    'trades', 'max_dd_pct', 'n_syms', 'years',
]


def _safe_float(v, d=0.0):
    try: return float(v)
    except Exception: return d


def _classify_row(row: dict) -> tuple[str, dict]:
    """Return (tier_tag, augmented_row)."""
    ps = _safe_float(row.get('pool_sharpe'))
    gpy = _safe_float(row.get('gain_per_yr'))
    gain_per_mo = gpy / 12.0 if gpy else 0.0
    trades = int(_safe_float(row.get('trades')))
    n_syms = int(_safe_float(row.get('n_syms'))) or 1
    years = _safe_float(row.get('years'))
    status = (row.get('status') or '').lower()
    if status in ('error', 'timeout', 'no_result') or ps == 0.0 and trades == 0:
        return 'DISCARD_NO_RESULT', row
    if ps < DISCARD_FLOOR:
        return 'DISCARD_LOW_SHARPE', row
    # Promote to deep test only if cheap-tier signal is decent AND positive gain
    if ps >= DEEP_MIN and gain_per_mo >= GAIN_MO_MIN:
        # Per-sym-day trade rate cap check
        rate_per_sym_day = trades / max(n_syms * years * 365.25, 1.0) if years > 0 else 0.0
        if rate_per_sym_day > 8.0:
            row['_overtrade_warning'] = f'rate={rate_per_sym_day:.1f}/sym/day exceeds 8 cap'
            return 'DISCARD_OVERTRADE', row
        if rate_per_sym_day < 2.0 and rate_per_sym_day > 0:
            row['_undertrade_note'] = f'rate={rate_per_sym_day:.2f}/sym/day below 2 floor'
            return 'DEEP_CANDIDATE_LOW_RATE', row
        return 'DEEP_CANDIDATE', row
    return 'NOISE', row


def main():
    SWEEP_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - RECENT_HRS * 3600
    csvs = [Path(p) for p in glob.glob(str(SWEEP_DIR / 'backtest_v8_sweep_*.csv'))
            if Path(p).stat().st_mtime >= cutoff]
    if not csvs:
        print(f"[sweep_classify] no recent v8 sweep CSVs in last {RECENT_HRS}h")
        return 0
    discard_rows: list[dict] = []
    deep_rows: list[dict] = []
    counts = {'DISCARD_NO_RESULT': 0, 'DISCARD_LOW_SHARPE': 0, 'DISCARD_OVERTRADE': 0,
              'NOISE': 0, 'DEEP_CANDIDATE_LOW_RATE': 0, 'DEEP_CANDIDATE': 0}
    for path in sorted(csvs):
        try:
            rows = list(csv.DictReader(open(path)))
        except Exception:
            continue
        if not rows: continue
        # Skip CSVs missing canonical columns (old format / banned)
        missing = [f for f in CANONICAL_FIELDS if f not in rows[0]]
        if missing:
            print(f"[sweep_classify] SKIP {path.name} — missing {missing}")
            continue
        for r in rows:
            r['_source_csv'] = path.name
            tag, augmented = _classify_row(r)
            counts[tag] = counts.get(tag, 0) + 1
            if tag.startswith('DISCARD'):
                augmented['_tier_tag'] = tag
                discard_rows.append(augmented)
            elif tag.startswith('DEEP_CANDIDATE'):
                augmented['_tier_tag'] = tag
                deep_rows.append(augmented)
    # Write output CSVs
    log_lines = [f"[{NOW.isoformat()}] sweep_classify run",
                 f"  thresholds: DISCARD<{DISCARD_FLOOR}, DEEP≥{DEEP_MIN} & gain_per_mo≥{GAIN_MO_MIN}%",
                 f"  scanned: {len(csvs)} CSVs"]
    for k, v in counts.items():
        log_lines.append(f"  {k}: {v}")
    if discard_rows:
        out = SWEEP_DIR / f'sweep_classify_DISCARD_{TS}.csv'
        with open(out, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(discard_rows[0].keys()))
            w.writeheader()
            w.writerows(discard_rows)
        log_lines.append(f"  → {out.name} ({len(discard_rows)} rows)")
    if deep_rows:
        out = SWEEP_DIR / f'sweep_classify_DEEP_CANDIDATES_{TS}.csv'
        with open(out, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(deep_rows[0].keys()))
            w.writeheader()
            w.writerows(deep_rows)
        log_lines.append(f"  → {out.name} ({len(deep_rows)} rows)")
        # Print top deep candidates
        log_lines.append("  TOP DEEP CANDIDATES (sorted by pool_sharpe):")
        deep_rows.sort(key=lambda r: -_safe_float(r.get('pool_sharpe')))
        for r in deep_rows[:10]:
            ps = _safe_float(r.get('pool_sharpe'))
            gpy = _safe_float(r.get('gain_per_yr'))
            tr = int(_safe_float(r.get('trades')))
            label = r.get('label', '?')
            csv_src = r.get('_source_csv', '?')
            log_lines.append(f"    pool={ps:.4f} gain_per_yr={gpy:.2f}% trades={tr} | {label} ← {csv_src}")
    log = SWEEP_DIR / f'sweep_classify_{TS}.log'
    log.write_text('\n'.join(log_lines) + '\n')
    print('\n'.join(log_lines))
    return 0


if __name__ == '__main__':
    sys.exit(main())
