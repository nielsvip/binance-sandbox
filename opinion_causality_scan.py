#!/opt/anaconda3/envs/binance_env/bin/python
# pylint: disable=W,C,R,I
"""opinion_causality_scan.py — causality test: do Bitget trader entries lead price moves?

For each (symbol, side, entry_time) in data/bitget_traders/merged_all_trades.csv,
look up local 15m klines and measure forward returns at +1h/+4h/+24h in trader's direction.
Aggregate per trader, per symbol, overall.

Output: data/opinion_causality_report.json
   Reading-order: overall_stats -> per_trader (sorted by 4h_edge) -> per_symbol (sorted by hits)

Usage: python opinion_causality_scan.py [--days N]   default last 60 days
"""
import argparse
import csv
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median, stdev

BASE = Path("/Users/niels/Documents/binance")
TRADES_CSV = BASE / "data/bitget_traders/merged_all_trades.csv"
KLINES_DIR = BASE / "klines_cache"
OUT_PATH = BASE / "data/opinion_causality_report.json"
LOG_PATH = Path.home() / "logs" / "opinion_causality_scan.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
HORIZONS = {"1h": 4, "4h": 16, "24h": 96}
MIN_TRADES_FOR_TRADER_STATS = 8

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()])
log = logging.getLogger("opinion_causality")


def _parse_dt(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


_klines_cache = {}


def _load_klines(symbol):
    if symbol in _klines_cache:
        return _klines_cache[symbol]
    f = KLINES_DIR / f"{symbol}_15m.json"
    if not f.exists():
        _klines_cache[symbol] = None
        return None
    try:
        raw = json.loads(f.read_text())
    except Exception as e:
        log.warning("klines read %s failed: %s", symbol, e)
        _klines_cache[symbol] = None
        return None
    bars = []
    for r in raw:
        try:
            if isinstance(r, dict):
                ts_str = r.get("timestamp")
                if not ts_str:
                    continue
                dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                ts = int(dt.timestamp())
                close = float(r["close"])
            else:
                ts = int(r[0]) // 1000
                close = float(r[4])
            bars.append((ts, close))
        except Exception:
            continue
    bars.sort(key=lambda x: x[0])
    _klines_cache[symbol] = bars
    return bars


def _bar_index_at(bars, target_ts):
    lo, hi = 0, len(bars) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if bars[mid][0] < target_ts:
            lo = mid + 1
        else:
            hi = mid - 1
    return lo


def _signed_return(entry_close, future_close, side):
    if entry_close <= 0 or future_close <= 0:
        return None
    pct = (future_close - entry_close) / entry_close * 100.0
    return pct if side == "LONG" else -pct


def _scan(rows):
    per_trade = []
    skipped = {"no_klines": 0, "out_of_range": 0, "bad_time": 0, "bad_side": 0}
    for r in rows:
        side = (r.get("side") or "").upper()
        if side not in ("LONG", "SHORT"):
            skipped["bad_side"] += 1
            continue
        entry_dt = _parse_dt(r.get("entry_time"))
        if entry_dt is None:
            skipped["bad_time"] += 1
            continue
        symbol = r.get("symbol")
        bars = _load_klines(symbol)
        if not bars:
            skipped["no_klines"] += 1
            continue
        target = int(entry_dt.timestamp())
        idx = _bar_index_at(bars, target)
        if idx >= len(bars) - max(HORIZONS.values()) - 1:
            skipped["out_of_range"] += 1
            continue
        entry_close = bars[idx][1]
        forward = {}
        for label, n in HORIZONS.items():
            fclose = bars[idx + n][1]
            ret = _signed_return(entry_close, fclose, side)
            if ret is None:
                continue
            forward[label] = ret
        if not forward:
            skipped["out_of_range"] += 1
            continue
        per_trade.append({
            "trader_id": r.get("trader_id"),
            "symbol": symbol,
            "side": side,
            "entry_time": r.get("entry_time"),
            "entry_close": entry_close,
            "forward": forward,
        })
    return per_trade, skipped


def _stats(values):
    if not values:
        return None
    m = mean(values)
    md = median(values)
    sd = stdev(values) if len(values) >= 2 else 0.0
    hit = sum(1 for v in values if v > 0) / len(values) * 100.0
    return {"n": len(values), "mean_pct": round(m, 4), "median_pct": round(md, 4), "std_pct": round(sd, 4), "hit_rate_pct": round(hit, 2)}


def _aggregate(per_trade):
    by_trader = {}
    by_symbol = {}
    overall = {h: [] for h in HORIZONS}
    for t in per_trade:
        for h, ret in t["forward"].items():
            overall[h].append(ret)
            by_trader.setdefault(t["trader_id"], {h2: [] for h2 in HORIZONS})[h].append(ret)
            key = f"{t['symbol']}_{t['side']}"
            by_symbol.setdefault(key, {h2: [] for h2 in HORIZONS})[h].append(ret)
    overall_stats = {h: _stats(v) for h, v in overall.items()}
    trader_stats = []
    for trader_id, hzns in by_trader.items():
        rec = {"trader_id": trader_id, "stats_per_horizon": {h: _stats(v) for h, v in hzns.items()}}
        rec["total_trades"] = len(hzns["1h"]) if hzns.get("1h") else 0
        trader_stats.append(rec)
    trader_stats.sort(key=lambda r: (r["stats_per_horizon"].get("4h") or {}).get("mean_pct", 0), reverse=True)
    sym_stats = []
    for sym_side, hzns in by_symbol.items():
        rec = {"key": sym_side, "stats_per_horizon": {h: _stats(v) for h, v in hzns.items()}, "total_trades": len(hzns["1h"]) if hzns.get("1h") else 0}
        sym_stats.append(rec)
    sym_stats.sort(key=lambda r: (r["total_trades"], (r["stats_per_horizon"].get("4h") or {}).get("mean_pct", 0)), reverse=True)
    return overall_stats, trader_stats, sym_stats


def _verdict(overall_stats, trader_stats, threshold_4h_pct=0.5, threshold_hit_pct=55.0):
    """Identify reliable vs noise traders, and overall edge verdict."""
    reliable = []
    noise = []
    for r in trader_stats:
        s4 = r["stats_per_horizon"].get("4h") or {}
        if r["total_trades"] < MIN_TRADES_FOR_TRADER_STATS:
            continue
        mean_4h = s4.get("mean_pct", 0)
        hit_4h = s4.get("hit_rate_pct", 0)
        if mean_4h >= threshold_4h_pct and hit_4h >= threshold_hit_pct:
            reliable.append({"trader_id": r["trader_id"], "n": r["total_trades"], "mean_4h_pct": mean_4h, "hit_4h_pct": hit_4h})
        elif mean_4h <= -threshold_4h_pct or hit_4h < (100 - threshold_hit_pct):
            noise.append({"trader_id": r["trader_id"], "n": r["total_trades"], "mean_4h_pct": mean_4h, "hit_4h_pct": hit_4h})
    o4 = overall_stats.get("4h") or {}
    overall_edge = "POSITIVE" if (o4.get("mean_pct", 0) >= 0.2 and o4.get("hit_rate_pct", 0) >= 52) else ("NEUTRAL" if abs(o4.get("mean_pct", 0)) < 0.2 else "NEGATIVE")
    return {
        "overall_4h_edge_verdict": overall_edge,
        "overall_4h_mean_pct": o4.get("mean_pct"),
        "overall_4h_hit_rate_pct": o4.get("hit_rate_pct"),
        "overall_4h_n": o4.get("n"),
        "reliable_traders": reliable,
        "noise_traders": noise,
        "min_trades_for_trader_eval": MIN_TRADES_FOR_TRADER_STATS,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=60, help="filter entries to last N days")
    args = p.parse_args()
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    if not TRADES_CSV.exists():
        log.error("trades csv not found: %s", TRADES_CSV)
        return
    rows = []
    with TRADES_CSV.open() as f:
        reader = csv.DictReader(f)
        for r in reader:
            dt = _parse_dt(r.get("entry_time"))
            if dt is None or dt < cutoff:
                continue
            rows.append(r)
    log.info("loaded %d rows after %d-day filter", len(rows), args.days)
    per_trade, skipped = _scan(rows)
    log.info("scored %d trades, skipped: %s", len(per_trade), skipped)
    try:
        from test_rate_guard import RateGuard
        # Conceptual: causality scan is "1 account" of historical Bitget trader copy data over args.days days.
        # 50 trades/day × N days is the floor for a meaningful sample.
        RateGuard(n_accts=1, label="opinion_causality_scan").final_check(len(per_trade), test_window_days=args.days)
    except SystemExit:
        raise
    except Exception as _g_err:
        log.warning("rate guard failed: %s", _g_err)
    overall, trader_stats, sym_stats = _aggregate(per_trade)
    verdict = _verdict(overall, trader_stats)
    out = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "filter_days": args.days,
        "rows_loaded": len(rows),
        "rows_scored": len(per_trade),
        "skipped": skipped,
        "horizons": HORIZONS,
        "verdict": verdict,
        "overall_stats_per_horizon": overall,
        "per_trader_top20": trader_stats[:20],
        "per_trader_bottom10": trader_stats[-10:],
        "per_symbol_top30_by_volume": sym_stats[:30],
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2, default=str))
    log.info("wrote %s (%d bytes)", OUT_PATH, OUT_PATH.stat().st_size)
    print()
    print(f"=== OPINION CAUSALITY VERDICT (last {args.days}d, n={len(per_trade)} scored trades) ===")
    print(f"Overall 4h edge:  {verdict['overall_4h_edge_verdict']}  mean={verdict['overall_4h_mean_pct']}%  hit_rate={verdict['overall_4h_hit_rate_pct']}%  n={verdict['overall_4h_n']}")
    print(f"Reliable traders ({len(verdict['reliable_traders'])}):")
    for r in verdict["reliable_traders"][:8]:
        print(f"  {r['trader_id'][:24]:24s} n={r['n']:>3} mean_4h={r['mean_4h_pct']:+.3f}%  hit={r['hit_4h_pct']:.1f}%")
    print(f"Noise traders ({len(verdict['noise_traders'])}):")
    for r in verdict["noise_traders"][:8]:
        print(f"  {r['trader_id'][:24]:24s} n={r['n']:>3} mean_4h={r['mean_4h_pct']:+.3f}%  hit={r['hit_4h_pct']:.1f}%")
    print(f"\nFull report: {OUT_PATH}")


if __name__ == "__main__":
    main()
