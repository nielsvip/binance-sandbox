#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""rate_filter_promoter.py — continuous watcher over autonomous_search output dirs.

Reads every iter row written to autonomous_<mode>.csv files, computes per-sym trade rate,
tags as RATE_LOW / RATE_OK / RATE_HIGH per the 2026-05-07 mandate, and writes RATE_OK
winners (pool_sharpe >= floor) into canonical_winners.csv + per_sym_active_config.json
for live pickup.

Bands (per `feedback_trade_rate_floor_2day_deadline_20260507`):
  Crypto: 2-5 trades/sym/day
  Stocks: 2-8 trades/sym/week

Also emits a heartbeat to stderr every 60s with last-winner-age — flags if no winner in 60min.
"""
from __future__ import annotations
import argparse, csv, json, os, sys, time
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent

CRYPTO_OUT_BASE = ROOT / 'data' / 'autonomous' / 'canonical_crypto_50sym_post_lockdown'
TRADIER_OUT_BASE = ROOT / 'data' / 'autonomous' / 'canonical_tradier_100sym_post_lockdown'
WINNERS_DIR = ROOT / 'data' / 'rate_filter_winners'
ACTIVE_CFG_PATH = ROOT / 'data' / 'hourly_reconfig' / 'per_sym_active_config.json'
# 2026-07-06: crypto and stocks (tradier) MUST NOT share the live per-sym config
# file — writing a 'tradier' mode winner into ACTIVE_CFG_PATH contaminated the
# crypto-only file. Tradier winners now go to the stocks-only sibling file.
ACTIVE_CFG_PATH_TRADIER = ROOT / 'data' / 'hourly_reconfig' / 'per_sym_active_config_stocks.json'


def _active_cfg_path_for_mode(mode: str) -> Path:
    return ACTIVE_CFG_PATH_TRADIER if mode == 'tradier' else ACTIVE_CFG_PATH

# 2026-05-09 USER MANDATE: 2-8 trades per sym per day MAX for BOTH crypto and stocks.
# Earlier crypto cap was 5/day; tradier was 2-8 per WEEK. Both unified to 2-8/day.
# Anything outside this band is REJECTED for promotion (overtrading or undertrading).
CRYPTO_RATE_LOW = 2.0
CRYPTO_RATE_HIGH = 8.0
TRADIER_RATE_LOW = 2.0
TRADIER_RATE_HIGH = 8.0
# Legacy aliases kept for any consumers that reference per-week values
TRADIER_RATE_LOW_PER_WEEK = TRADIER_RATE_LOW * 7.0
TRADIER_RATE_HIGH_PER_WEEK = TRADIER_RATE_HIGH * 7.0

# 2026-05-09 USER MANDATE: pool_sharpe must be ≥ 0.5 — never promote below.
PROMOTE_POOL_SHARPE_MIN = 0.5
MAX_ALLOWED_DRAWDOWN_PCT = 50.0

# 2026-05-07 22:35: physical bounds — autonomous_search mutates without bounds and produced BTC_PER_TRADE_NOTIONAL_USD_MAX=22.5 (Binance min trade ~$130 — physically impossible). Reject configs that override these knobs OUTSIDE the realistic range. This is non-negotiable for live promotion.
PHYSICAL_BOUNDS = {
    'BTC_PER_TRADE_NOTIONAL_USD_MAX': (130.0, 5000.0),
    'BTC_TREND_NOTIONAL_USD_MAX': (130.0, 10000.0),
    'BTC_TOTAL_NOTIONAL_USD_MAX': (260.0, 50000.0),
    'BTC_HARD_LOSS_USD_PER_TRADE': (1.0, 100.0),
    'BTC_BREAKOUT_HARD_LOSS_USD_PER_TRADE': (1.0, 100.0),
    'BTC_TREND_HARD_LOSS_USD_PER_TRADE': (1.0, 100.0),
    'BTC_HF_HEDGE_NOTIONAL_PCT_OF_LOSER': (0.1, 2.0),
    'MIN_POSITION_SIZE': (10.0, 1000.0),
    'START_POSITION_SIZE': (50.0, 5000.0),
    'STOP_LOSS_PCT': (0.1, 10.0),
    'BTC_TREND_HARD_LOSS_PCT': (0.1, 10.0),
    'BTC_HEDGE_MIN_HOLD_BARS': (0, 200),
    'BTC_MIN_HOLD_BARS': (0, 200),
}


def violates_physical_bounds(overrides_json: str) -> tuple[bool, list[str]]:
    """Returns (violates, list-of-violation-tags)."""
    try:
        ovr = json.loads(overrides_json or '{}')
    except Exception:
        return False, []
    bad = []
    for k, (mn, mx) in PHYSICAL_BOUNDS.items():
        if k not in ovr: continue
        try:
            v = float(ovr[k])
            if v < mn or v > mx:
                bad.append(f'{k}={v}|bounds=[{mn},{mx}]')
        except (TypeError, ValueError):
            continue
    return len(bad) > 0, bad

CSV_FIELDS = [
    'ts_utc', 'mode', 'worker', 'iter', 'pool_sharpe', 'sym_sharpe', 'acc_gain_pct',
    'avg_gain_trade', 'gain_per_yr', 'gain_sym_yr', 'max_dd_pct', 'trades', 'wr',
    'n_syms', 'n_years', 'rate_per_sym_per_day', 'rate_band', 'promotable',
    'physical_bounds_violations', 'overrides_count', 'overrides_json',
]


def rate_band(rate_per_sym_per_day: float, mode: str) -> str:
    if mode == 'crypto':
        if rate_per_sym_per_day < CRYPTO_RATE_LOW: return 'RATE_LOW'
        if rate_per_sym_per_day > CRYPTO_RATE_HIGH: return 'RATE_HIGH'
        return 'RATE_OK'
    else:  # tradier
        if rate_per_sym_per_day < TRADIER_RATE_LOW: return 'RATE_LOW'
        if rate_per_sym_per_day > TRADIER_RATE_HIGH: return 'RATE_HIGH'
        return 'RATE_OK'


def is_promotable(row: dict, mode: str) -> bool:
    if row['rate_band'] != 'RATE_OK':
        return False
    if row['pool_sharpe'] < PROMOTE_POOL_SHARPE_MIN:
        return False
    if row['n_syms'] * row['n_years'] * 30 > row['trades']:
        return False  # below sample-floor (≥30 trades/sym)
    if row['max_dd_pct'] >= MAX_ALLOWED_DRAWDOWN_PCT:
        return False  # excessive DD — don't promote risk
    # Physical-bounds violation — config has impossible values like BTC_PER_TRADE_NOTIONAL_USD_MAX=22.5 (below Binance min trade)
    violates, _bad = violates_physical_bounds(row.get('overrides_json', '{}'))
    if violates:
        return False
    return True


def parse_iter_row(row: dict, mode: str, worker: str) -> dict:
    try:
        n_syms = int(row.get('n_syms', 0) or 0)
        n_years = float(row.get('n_years', 0) or 0)
        trades = int(row.get('trades', 0) or 0)
        pool_sharpe = float(row.get('pool_sharpe', 0) or 0)
        sym_sharpe = float(row.get('sym_sharpe', 0) or 0)
        gain = float(row.get('acc_gain_pct', 0) or 0)
        dd = float(row.get('max_dd_pct', 0) or 0)
        wr = float(row.get('wr', 0) or 0)
        ovr = row.get('overrides_json', '{}') or '{}'
        ovr_count = int(row.get('overrides_count', 0) or 0)
        avg_gain = float(row.get('avg_gain_trade', 0) or 0)
        gain_yr = float(row.get('gain_per_yr', 0) or 0)
        gain_sym_yr = float(row.get('gain_sym_yr', 0) or 0)
        if n_syms <= 0 or n_years <= 0:
            return None
        rate_psd = trades / n_syms / n_years / 365.25
        band = rate_band(rate_psd, mode)
        violates_pb, bad_pb = violates_physical_bounds(ovr)
        out = dict(
            ts_utc=datetime.now(timezone.utc).isoformat(timespec='seconds'),
            mode=mode, worker=worker, iter=int(row.get('iter', 0) or 0),
            pool_sharpe=round(pool_sharpe, 4), sym_sharpe=round(sym_sharpe, 4),
            acc_gain_pct=round(gain, 2), avg_gain_trade=round(avg_gain, 4),
            gain_per_yr=round(gain_yr, 2), gain_sym_yr=round(gain_sym_yr, 4),
            max_dd_pct=round(dd, 2), trades=trades, wr=round(wr, 1),
            n_syms=n_syms, n_years=round(n_years, 2),
            rate_per_sym_per_day=round(rate_psd, 4), rate_band=band,
            promotable=False,
            physical_bounds_violations='|'.join(bad_pb) if bad_pb else '',
            overrides_count=ovr_count, overrides_json=ovr,
        )
        out['promotable'] = is_promotable(out, mode)
        return out
    except Exception as e:
        return None


def load_active_config(mode: str = 'crypto') -> dict:
    path = _active_cfg_path_for_mode(mode)
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def save_active_config(cfg: dict, mode: str = 'crypto') -> None:
    path = _active_cfg_path_for_mode(mode)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(cfg, indent=2, default=str))
    tmp.replace(path)


def promote_to_active(parsed: dict) -> int:
    # 2026-05-07: per-sym overrides scope is the whole pool config (multi-sym), so
    # we apply to ALL syms in the universe rather than per-sym keys. Live readers
    # can also read at per-sym key level (sym_LONG / sym_SHORT) for legacy.
    mode = parsed['mode']
    cfg = load_active_config(mode)
    meta_key = f'_meta_pool_winner_{mode}'
    current_meta = cfg.get(meta_key, cfg.get('_meta_pool_winner', {}))
    current_sharpe = float(current_meta.get('pool_sharpe', current_meta.get('wsharpe', 0.0)) or 0.0)
    if parsed['pool_sharpe'] <= current_sharpe:
        return -1  # Don't downgrade — current is already better
    tag = f"rate_promoter_{mode}_w{parsed['worker']}_iter{parsed['iter']}_{int(time.time())}"
    overrides = json.loads(parsed['overrides_json']) if parsed['overrides_json'] else {}
    if not overrides:
        return 0
    cfg[meta_key] = {
        'winning_tag': tag,
        'wsharpe': parsed['pool_sharpe'],
        'pool_sharpe': parsed['pool_sharpe'],
        'sym_sharpe': parsed['sym_sharpe'],
        'trades': parsed['trades'],
        'rate_per_sym_per_day': parsed['rate_per_sym_per_day'],
        'wr_pct': parsed['wr'],
        'max_dd_pct': parsed['max_dd_pct'],
        'gain_per_yr': parsed['gain_per_yr'],
        'gain_sym_yr': parsed['gain_sym_yr'],
        'avg_gain_trade': parsed['avg_gain_trade'],
        'years': parsed['n_years'],
        'n_syms': parsed['n_syms'],
        'sample_tag': f'AUTONOMOUS_RATE_OK_{mode.upper()}',
        'mode': mode,
        'overrides': overrides,
        'overrides_count': parsed['overrides_count'],
        'promoted_at_utc': parsed['ts_utc'],
    }
    save_active_config(cfg, mode)
    return len(overrides)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', choices=['crypto', 'tradier', 'both'], default='both')
    ap.add_argument('--poll-s', type=float, default=30.0)
    ap.add_argument('--no-promote', action='store_true', help='dry-run: parse + log only, no per_sym_active_config.json writes')
    args = ap.parse_args()
    WINNERS_DIR.mkdir(parents=True, exist_ok=True)
    canonical_csv = WINNERS_DIR / 'canonical_winners.csv'
    new_csv = not canonical_csv.exists()
    seen: dict[str, int] = {}  # path -> last-row-count read
    last_winner_ts = time.time()
    promote_count = 0
    parse_count = 0
    print(f"[rate_filter] mode={args.mode} poll={args.poll_s}s "
          f"crypto_band=[{CRYPTO_RATE_LOW},{CRYPTO_RATE_HIGH}]/sym/day "
          f"tradier_band=[{TRADIER_RATE_LOW_PER_WEEK},{TRADIER_RATE_HIGH_PER_WEEK}]/sym/wk "
          f"sharpe_floor={PROMOTE_POOL_SHARPE_MIN}",
          flush=True)
    fh = canonical_csv.open('a', newline='')
    w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
    if new_csv: w.writeheader()
    while True:
        for mode, base in (('crypto', CRYPTO_OUT_BASE), ('tradier', TRADIER_OUT_BASE)):
            if args.mode != 'both' and args.mode != mode: continue
            if not base.exists(): continue
            for csv_path in base.rglob(f'autonomous_{mode}.csv'):
                worker = csv_path.parent.name
                try:
                    if not csv_path.stat().st_size: continue
                    rows = list(csv.DictReader(csv_path.open()))
                except Exception: continue
                last_count = seen.get(str(csv_path), 0)
                if len(rows) <= last_count: continue
                new_rows = rows[last_count:]
                seen[str(csv_path)] = len(rows)
                for row in new_rows:
                    parsed = parse_iter_row(row, mode, worker)
                    if not parsed: continue
                    parse_count += 1
                    w.writerow(parsed)
                    fh.flush()
                    if parsed['promotable']:
                        if not args.no_promote:
                            n_ovr = promote_to_active(parsed)
                            if n_ovr == -1:
                                print(f"[rate_filter] SKIPPED_DOWNGRADE mode={mode} worker={worker} "
                                      f"iter={parsed['iter']} pool_sharpe={parsed['pool_sharpe']} "
                                      f"(not better than current)",
                                      flush=True)
                                continue
                            promote_count += 1
                            last_winner_ts = time.time()
                            print(f"[rate_filter] PROMOTED mode={mode} worker={worker} "
                                  f"iter={parsed['iter']} pool_sharpe={parsed['pool_sharpe']} "
                                  f"sym_sharpe={parsed['sym_sharpe']} "
                                  f"rate={parsed['rate_per_sym_per_day']:.3f}/sym/day "
                                  f"trades={parsed['trades']} dd={parsed['max_dd_pct']}% "
                                  f"ovr={n_ovr}",
                                  flush=True)
                        else:
                            print(f"[rate_filter] PROMOTABLE_DRY mode={mode} iter={parsed['iter']} "
                                  f"pool_sharpe={parsed['pool_sharpe']} "
                                  f"rate={parsed['rate_per_sym_per_day']:.3f}/sym/day",
                                  flush=True)
                    elif parsed['rate_band'] == 'RATE_OK' and parsed['pool_sharpe'] >= PROMOTE_POOL_SHARPE_MIN:
                        # Was eligible on rate+sharpe but blocked by another check
                        violates, bad = violates_physical_bounds(parsed.get('overrides_json', '{}'))
                        if violates:
                            print(f"[rate_filter] PHYSICAL_BLOCK mode={mode} iter={parsed['iter']} "
                                  f"pool_sharpe={parsed['pool_sharpe']} "
                                  f"rate={parsed['rate_per_sym_per_day']:.3f}/sym/day "
                                  f"violations={bad}",
                                  flush=True)
                            continue
                    if not parsed['promotable']:
                        if parsed['rate_band'] == 'RATE_OK' and parsed['pool_sharpe'] >= 0.3:
                            # near-promote - log for visibility
                            print(f"[rate_filter] near-promote mode={mode} iter={parsed['iter']} "
                                  f"pool_sharpe={parsed['pool_sharpe']} "
                                  f"rate={parsed['rate_per_sym_per_day']:.3f}/sym/day "
                                  f"band={parsed['rate_band']}",
                                  flush=True)
        # Heartbeat: how long since last winner?
        age_min = (time.time() - last_winner_ts) / 60.0
        if age_min > 60:
            print(f"[rate_filter] STALE_WINNER age={age_min:.1f}min "
                  f"parse_count={parse_count} promote_count={promote_count} — search may need wider mutation",
                  file=sys.stderr, flush=True)
        time.sleep(args.poll_s)


if __name__ == '__main__':
    main()
