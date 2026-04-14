#!/usr/bin/env python3
"""Sequential sweep — loads data ONCE, runs all configs in-process. No fork issues."""
import json, sys, time, os
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, "/home/niels/binance-sandbox")
sys.path.insert(0, "/home/niels/binance")

from backtest_v3_simulate import simulate, DEFAULT_PARAMS
from backtest_v3_sweep import BASELINE_CONFIG, SWEEP_GRID, PARAM_ORDER, config_short_str, config_hash

SYMBOLS = json.load(open("/home/niels/binance-sandbox/backtest_48_symbols.json"))


def generate_focused():
    configs = [dict(BASELINE_CONFIG)]
    for param in PARAM_ORDER:
        for val in SWEEP_GRID[param]:
            cfg = dict(BASELINE_CONFIG)
            cfg[param] = val
            if config_hash(cfg) != config_hash(BASELINE_CONFIG):
                configs.append(cfg)
    seen = set()
    return [c for c in configs if not (config_hash(c) in seen or seen.add(config_hash(c)))]


def main():
    configs = generate_focused()
    print(f"=== SEQUENTIAL SWEEP: {len(configs)} configs ===\n")
    results = []
    t0 = time.time()
    for i, cfg in enumerate(configs):
        full = {**DEFAULT_PARAMS, **cfg}
        try:
            r, trades = simulate(SYMBOLS, full)
            stats = r.get("stats", r)
            stats["config"] = cfg
            results.append(stats)
            sharpe = stats.get("sharpe", -999)
            wr = stats.get("win_rate", 0)
            pnl = stats.get("total_return_pct", 0)
            trades_n = stats.get("n_entries", 0)
            elapsed = time.time() - t0
            eta = (elapsed / (i + 1)) * (len(configs) - i - 1)
            marker = " <<< BASELINE" if config_hash(cfg) == config_hash(BASELINE_CONFIG) else ""
            print(f"  [{i+1:>3}/{len(configs)}] Sharpe={sharpe:>7.2f}  WR={wr:>5.1f}%  PnL={pnl:>+8.1f}%  trades={trades_n:>5}  {config_short_str(cfg)}{marker}  ({elapsed:.0f}s, ETA {eta:.0f}s)")
        except Exception as e:
            print(f"  [{i+1:>3}/{len(configs)}] ERROR: {e}  {config_short_str(cfg)}")
            results.append({"config": cfg, "sharpe": -999, "error": str(e)})
    elapsed = time.time() - t0
    valid = [r for r in results if r.get("sharpe", -999) > -999]
    valid.sort(key=lambda r: r.get("sharpe", 0), reverse=True)
    bl_hash = config_hash(BASELINE_CONFIG)
    bl = next((r for r in valid if config_hash(r["config"]) == bl_hash), None)
    print(f"\n{'='*90}")
    print(f" RESULTS: {len(valid)}/{len(configs)} valid in {elapsed:.0f}s")
    print(f"{'='*90}")
    if bl:
        print(f"\n  BASELINE: Sharpe={bl.get('sharpe',0):.2f}  WR={bl.get('win_rate',0):.1f}%  PnL={bl.get('total_return_pct',0):+.1f}%")
    print(f"\n  Top 10:")
    for i, r in enumerate(valid[:10], 1):
        s = r.get("sharpe", 0)
        delta = s - bl.get("sharpe", 0) if bl else 0
        marker = " <<< BASELINE" if config_hash(r["config"]) == bl_hash else ""
        print(f"  #{i:<3} Sharpe={s:>7.2f} ({delta:>+6.2f})  WR={r.get('win_rate',0):>5.1f}%  PnL={r.get('total_return_pct',0):>+8.1f}%  {config_short_str(r['config'])}{marker}")
    print(f"\n  Bottom 5:")
    for r in valid[-5:]:
        s = r.get("sharpe", 0)
        delta = s - bl.get("sharpe", 0) if bl else 0
        print(f"       Sharpe={s:>7.2f} ({delta:>+6.2f})  {config_short_str(r['config'])}")
    print(f"\n  Parameter Sensitivity (avg Sharpe by value):")
    for param in PARAM_ORDER:
        groups = defaultdict(list)
        for r in valid:
            groups[r["config"][param]].append(r.get("sharpe", 0))
        parts = [f"{v}={sum(ss)/len(ss):.2f}" for v, ss in sorted(groups.items(), key=lambda x: x[0] if not isinstance(x[0], bool) else int(x[0]))]
        print(f"    {param:22s} {' | '.join(parts)}")
    out = Path("/home/niels/binance/results/sweep_sequential.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    json.dump({"results": results, "baseline": bl, "elapsed": elapsed}, open(out, "w"), default=str)
    print(f"\n  Saved: {out}")


if __name__ == "__main__":
    main()
