# pylint: disable=W,C,R,I
"""ez_symbol_performance — Rolling per-symbol win rate, avg gain, and order multiplier.
Reads JSONL trade history, computes 14-day rolling stats, outputs:
  - data/symbol_performance.json (full stats + order_multiplier per symbol)
  - get_performance_multiplier(symbol) for ez_manage.py integration
  - get_symbol_tier(symbol) for ez_rankings.py tiering (A/B/C)
Runs as background task or standalone (--refresh). Cached in memory, refreshed every 5 min."""
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple
from config import Config
from utils import setup_logger

config = Config()
logger = setup_logger('ez_symbol_performance', str(config.LOG_DIR / 'ez_symbol_performance.log'), logging.INFO)
BASE_PATH = Path(os.getenv('EZ_BASE_PATH', str(config.BASE_PATH)))
DATA_DIR = BASE_PATH / 'data'
HISTORY_DIR = DATA_DIR / 'history'
PERF_FILE = DATA_DIR / 'symbol_performance.json'
OUTLIER_FILE = DATA_DIR / 'outlier_alerts.json'
_perf_cache: Dict[str, dict] = {}
_cache_ts: float = 0.0
ENTRY_TYPES = frozenset({'AUGMENT', 'OPEN', 'QUICK_OPEN', 'REENTRY'})
EXIT_TYPES = frozenset({'REDUCE', 'CLOSE', 'QUICK_CLOSE'})

def _parse_history_file(filepath: Path, cutoff: datetime) -> list:
    """Parse a single JSONL history file. Returns list of (ts, type, qty, price, value) tuples after cutoff."""
    records = []
    seen = set()
    try:
        with open(filepath, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts_str = rec.get('ts', '')
                rtype = rec.get('type', '')
                qty = rec.get('qty', 0.0)
                price = rec.get('price', 0.0)
                value = rec.get('value', 0.0)
                if not ts_str or not rtype:
                    continue
                dedup_key = (ts_str[:19], rtype, str(qty))
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                try:
                    ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                except Exception:
                    continue
                if ts < cutoff:
                    continue
                records.append((ts, rtype, float(qty), float(price), float(value)))
    except Exception as e:
        logger.debug(f"Error parsing {filepath.name}: {e}")
    return records

def _reconstruct_trades(records: list) -> list:
    """From sorted (ts, type, qty, price, value) records, reconstruct trade cycles.
    A cycle = one or more entries followed by one or more exits.
    Returns list of {'entry_avg', 'exit_avg', 'qty', 'pnl_pct', 'pnl_usd', 'hold_seconds', 'side'}."""
    if not records:
        return []
    records.sort(key=lambda r: r[0])
    trades = []
    entry_cost = 0.0
    entry_qty = 0.0
    entry_time = None
    for ts, rtype, qty, price, value in records:
        if rtype in ENTRY_TYPES:
            if entry_time is None:
                entry_time = ts
            entry_cost += value if value > 0 else qty * price
            entry_qty += qty
        elif rtype in EXIT_TYPES and entry_qty > 0:
            exit_price = price
            entry_avg = entry_cost / entry_qty if entry_qty > 0 else price
            hold_sec = (ts - entry_time).total_seconds() if entry_time else 0
            pnl_pct = (exit_price - entry_avg) / entry_avg * 100 if entry_avg > 0 else 0.0
            trades.append({'entry_avg': entry_avg, 'exit_avg': exit_price, 'qty': min(qty, entry_qty), 'pnl_pct': pnl_pct, 'hold_seconds': hold_sec})
            closed_qty = min(qty, entry_qty)
            entry_qty -= closed_qty
            if entry_qty > 0:
                entry_cost = entry_avg * entry_qty
            else:
                entry_cost = 0.0
                entry_qty = 0.0
                entry_time = None
    return trades

def compute_all_stats(window_days: int = None) -> Dict[str, dict]:
    """Scan all history JSONL files, compute per-symbol rolling stats."""
    if window_days is None:
        window_days = config.SYMBOL_PERF_WINDOW_DAYS
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    stats: Dict[str, dict] = {}
    if not HISTORY_DIR.exists():
        logger.warning(f"[PERF] History dir not found: {HISTORY_DIR}")
        return stats
    for acct_dir in HISTORY_DIR.iterdir():
        if not acct_dir.is_dir():
            continue
        for jfile in acct_dir.glob('*.jsonl'):
            fname = jfile.stem
            if '_LONG' in fname:
                symbol = fname.replace('_LONG', '')
                side = 'LONG'
            elif '_SHORT' in fname:
                symbol = fname.replace('_SHORT', '')
                side = 'SHORT'
            else:
                continue
            records = _parse_history_file(jfile, cutoff)
            if not records:
                continue
            trades = _reconstruct_trades(records)
            if not trades:
                continue
            key = f"{symbol}_{side}"
            if key not in stats:
                stats[key] = {'symbol': symbol, 'side': side, 'trades': [], 'accounts': set()}
            stats[key]['trades'].extend(trades)
            stats[key]['accounts'].add(acct_dir.name)
    result = {}
    for key, data in stats.items():
        symbol = data['symbol']
        all_trades = data['trades']
        n_trades = len(all_trades)
        if n_trades == 0:
            continue
        wins = sum(1 for t in all_trades if t['pnl_pct'] > 0)
        win_rate = wins / n_trades
        avg_gain = sum(t['pnl_pct'] for t in all_trades) / n_trades
        avg_hold_sec = sum(t['hold_seconds'] for t in all_trades) / n_trades
        total_pnl_pct = sum(t['pnl_pct'] for t in all_trades)
        best_trade = max(t['pnl_pct'] for t in all_trades)
        worst_trade = min(t['pnl_pct'] for t in all_trades)
        if symbol not in result or result[symbol].get('trade_count', 0) < n_trades:
            multiplier = _compute_multiplier(win_rate, avg_gain, n_trades)
            tier = _compute_tier(win_rate, avg_gain, n_trades)
            now_iso = datetime.now(timezone.utc).isoformat()
            result[symbol] = {'symbol': symbol, 'trade_count': n_trades, 'win_rate': round(win_rate, 4), 'avg_gain_pct': round(avg_gain, 4), 'total_pnl_pct': round(total_pnl_pct, 2), 'best_trade_pct': round(best_trade, 2), 'worst_trade_pct': round(worst_trade, 2), 'avg_hold_hours': round(avg_hold_sec / 3600, 2), 'accounts': list(data['accounts']), 'raw_multiplier': round(multiplier, 4), 'order_multiplier': round(multiplier, 3), 'tier': tier, 'set_at': now_iso, 'updated_at': now_iso}
    return result

def _compute_multiplier(win_rate: float, avg_gain: float, n_trades: int) -> float:
    """Compute raw order sizing multiplier from performance stats. Range: 0.1-10x.
    Outperformers (high WR + positive avg gain) get up to 10x.
    Underperformers (low WR + negative avg gain) get down to 0.1x."""
    if n_trades < config.SYMBOL_PERF_MIN_TRADES:
        return 1.0
    win_score = (win_rate - 0.5) * 4.0
    gain_score = max(-2.0, min(2.0, avg_gain * 1.5))
    confidence = min(1.0, n_trades / 20.0)
    raw_score = (win_score + gain_score) * confidence
    if raw_score > 0:
        raw = 1.0 + raw_score * 4.5
    else:
        raw = max(0.1, 1.0 / (1.0 - raw_score * 2.0))
    outlier_penalty = _get_outlier_penalty(None)
    raw *= outlier_penalty
    return max(config.SYMBOL_PERF_MIN_MULT, min(config.SYMBOL_PERF_MAX_MULT, raw))

def _compute_tier(win_rate: float, avg_gain: float, n_trades: int) -> str:
    """Classify symbol into A/B/C tier based on performance."""
    if n_trades < config.TIER_B_MIN_TRADES:
        return 'B'
    if win_rate >= config.TIER_A_WIN_RATE and avg_gain >= config.TIER_A_MIN_GAIN and n_trades >= config.TIER_A_MIN_TRADES:
        return 'A'
    if win_rate >= config.TIER_B_WIN_RATE or (avg_gain > 0 and n_trades >= config.TIER_B_MIN_TRADES):
        return 'B'
    return 'C'

def _get_outlier_penalty(symbol: Optional[str]) -> float:
    """Check outlier_alerts.json for active CRITICAL alerts. Returns 0.8 if found, 1.0 otherwise."""
    try:
        if OUTLIER_FILE.exists():
            with open(OUTLIER_FILE, 'r') as f:
                alerts = json.load(f)
            if symbol:
                for a in alerts.get('active', []):
                    if a.get('symbol') == symbol and a.get('severity') == 'CRITICAL':
                        return 0.8
    except Exception:
        pass
    return 1.0

def refresh_cache() -> Dict[str, dict]:
    """Recompute stats and update cache + disk file."""
    global _perf_cache, _cache_ts
    if not config.SYMBOL_PERF_ENABLED:
        return {}
    try:
        stats = compute_all_stats()
        _perf_cache = stats
        _cache_ts = time.time()
        try:
            with open(PERF_FILE, 'w') as f:
                json.dump(stats, f, indent=1)
            outperformers = sum(1 for s in stats.values() if s.get('raw_multiplier', 1.0) > 2.0)
            underperformers = sum(1 for s in stats.values() if s.get('raw_multiplier', 1.0) < 0.5)
            logger.info(f"[PERF] Refreshed: {len(stats)} symbols, A={sum(1 for s in stats.values() if s['tier']=='A')}, B={sum(1 for s in stats.values() if s['tier']=='B')}, C={sum(1 for s in stats.values() if s['tier']=='C')} | outperf(>2x)={outperformers} underperf(<0.5x)={underperformers}")
        except Exception as e:
            logger.warning(f"[PERF] Failed to write {PERF_FILE.name}: {e}")
        return stats
    except Exception as e:
        logger.error(f"[PERF] Refresh error: {e}", exc_info=True)
        return _perf_cache

def _ensure_cache() -> Dict[str, dict]:
    """Load cache from disk if memory cache is empty or stale."""
    global _perf_cache, _cache_ts
    if _perf_cache and (time.time() - _cache_ts) < config.SYMBOL_PERF_REFRESH_SECONDS:
        return _perf_cache
    if PERF_FILE.exists():
        try:
            with open(PERF_FILE, 'r') as f:
                _perf_cache = json.load(f)
            _cache_ts = time.time()
            return _perf_cache
        except Exception:
            pass
    return refresh_cache()

def _apply_decay(raw_mult: float, set_at_str: str) -> float:
    """Decay multiplier toward 1.0 exponentially. Half-life = SYMBOL_PERF_DECAY_HOURS.
    After ~60h the multiplier is effectively 1.0 (within 3% of neutral)."""
    try:
        set_at = datetime.fromisoformat(set_at_str.replace('Z', '+00:00'))
    except Exception:
        return 1.0
    elapsed_hours = (datetime.now(timezone.utc) - set_at).total_seconds() / 3600.0
    if elapsed_hours <= 0:
        return raw_mult
    half_life = getattr(config, 'SYMBOL_PERF_DECAY_HOURS', 60.0)
    decay = 0.5 ** (elapsed_hours / half_life)
    return 1.0 + (raw_mult - 1.0) * decay

def get_performance_multiplier(symbol: str, default: float = 1.0) -> float:
    """Get order sizing multiplier for a symbol with time decay applied.
    Raw multiplier decays toward 1.0 over SYMBOL_PERF_DECAY_HOURS."""
    if not config.SYMBOL_PERF_ENABLED:
        return default
    cache = _ensure_cache()
    entry = cache.get(symbol)
    if not entry:
        return default
    raw = entry.get('raw_multiplier', entry.get('order_multiplier', default))
    set_at = entry.get('set_at', entry.get('updated_at', ''))
    if not set_at:
        return raw
    return max(config.SYMBOL_PERF_MIN_MULT, min(config.SYMBOL_PERF_MAX_MULT, _apply_decay(raw, set_at)))

def get_symbol_tier(symbol: str, default: str = 'B') -> str:
    """Get A/B/C tier for a symbol. Returns 'B' (neutral) if no data."""
    if not config.TIER_ENABLED:
        return default
    cache = _ensure_cache()
    entry = cache.get(symbol)
    if not entry:
        return default
    return entry.get('tier', default)

def get_tier_multiplier(symbol: str) -> float:
    """Get tier-based ranking multiplier adjustment. A=1.2, B=1.0, C=0.7."""
    tier = get_symbol_tier(symbol)
    if tier == 'A':
        return config.TIER_A_MULTIPLIER
    if tier == 'C':
        return config.TIER_C_MULTIPLIER
    return 1.0

def get_full_stats(symbol: str) -> Optional[dict]:
    """Get full performance stats for a symbol."""
    cache = _ensure_cache()
    return cache.get(symbol)

if __name__ == '__main__':
    import sys
    stats = refresh_cache()
    if '--json' in sys.argv:
        print(json.dumps(stats, indent=2))
    else:
        print(f"{'=' * 80}")
        print(f"SYMBOL PERFORMANCE — {len(stats)} symbols ({config.SYMBOL_PERF_WINDOW_DAYS}-day window)")
        print(f"{'=' * 80}")
        tiers = {'A': [], 'B': [], 'C': []}
        for sym, s in sorted(stats.items(), key=lambda x: x[1].get('total_pnl_pct', 0), reverse=True):
            tiers[s['tier']].append(s)
        for tier_name in ('A', 'B', 'C'):
            items = tiers[tier_name]
            print(f"\n--- TIER {tier_name} ({len(items)} symbols) ---")
            for s in items[:15]:
                raw = s.get('raw_multiplier', s.get('order_multiplier', 1.0))
                decayed = get_performance_multiplier(s['symbol'], 1.0)
                print(f"  {s['symbol']:>15s}  WR={s['win_rate']:.0%}  avg={s['avg_gain_pct']:+.2f}%  total={s['total_pnl_pct']:+.1f}%  trades={s['trade_count']:3d}  raw={raw:.2f}x  now={decayed:.2f}x  hold={s['avg_hold_hours']:.1f}h")
            if len(items) > 15:
                print(f"  ... +{len(items) - 15} more")
        print(f"\n{'=' * 80}")
