#!/usr/bin/env python3
"""
v8_micro_experiments_tradier.py — 28 targeted experiments for tradier/stocks.

Mirrors v8_micro_experiments.py but for tradier mode (stocks).
Tradier auto-enables: K_ZONE, MFI_ENTRY, VWAP, FH_MOMENTUM, DC_DAYTRADE,
STOCH_CROSS_1H_EXIT, MFI_FLIP_EXIT, WT_CROSSUNDER_FINAL, MI_EXIT
SRS TF: bb_1h (stocks use Bollinger, not Donchian)
"""
import sys, time, json
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
sys.path.insert(0, str(Path(__file__).resolve().parent))
from v8_quick_engine import QuickConfig, load_npz, simulate, FAST_SYMBOLS_TRADIER

BASE = Path(__file__).resolve().parent
FAST_ALL = FAST_SYMBOLS_TRADIER.split(",")
# Sharpe-sorted per-symbol assumption (to validate with run) — adjust after first run
TOP5_TRADIER = ["NVDA","SPY","QQQ","AAPL","MSFT"]
MID_TRADIER = ["AMZN","META","JPM","XOM","TSLA"]
BOTTOM_TRADIER = ["BA","ABBV","GLD"]


def winner_cfg():
    c = QuickConfig()
    c.apply_tradier_defaults()  # K_ZONE/MFI/VWAP/FH_MOMENTUM/etc all on
    c.STRENGTH_FILTER_ENABLED = True
    c.STRENGTH_MIN_SCORE = 5.0
    c.HTF_MIN_ALIGNED = 1
    c.D_TREND_REQUIRED = True
    c.MIN_HOLD_BARS = 10
    c.WT_EXIT_MIN_TFS = 2
    c.COOLDOWN_BARS = 3
    c.PROFIT_TARGET_ENABLED = True
    c.PROFIT_TARGET_PCT = 1.6
    return c


def run_exp(args):
    label, cfg_overrides, symbols = args
    stores = load_npz('tradier', symbols, '2024-01-01')
    if not stores:
        return {"label": label, "sharpe": 0, "trades": 0, "error": "no_data"}
    cfg = winner_cfg()
    for k, v in cfg_overrides.items():
        if hasattr(cfg, k):
            cur = getattr(cfg, k)
            if isinstance(cur, bool): setattr(cfg, k, bool(v))
            elif isinstance(cur, int): setattr(cfg, k, int(v))
            elif isinstance(cur, float): setattr(cfg, k, float(v))
            else: setattr(cfg, k, v)
    t0 = time.time()
    r = simulate(stores, cfg, 10000.0)
    r["label"] = label
    r["cfg"] = cfg_overrides
    r["symbols"] = len(symbols)
    r["elapsed"] = round(time.time() - t0, 1)
    return r


def main():
    experiments = []

    # H1: Score variation
    for score in [3, 4, 5, 6, 7, 8]:
        experiments.append((f"H1_SCORE_{score}", {"STRENGTH_MIN_SCORE": float(score)}, FAST_ALL))

    # H2: Profit target variation
    for pt in [0.5, 1.0, 1.5, 2.0, 3.0]:
        experiments.append((f"H2_PT_{pt}", {"PROFIT_TARGET_PCT": float(pt)}, FAST_ALL))

    # H3: Stop loss overlay (with PT=2.0)
    for sl in [0.3, 0.5, 0.8, 1.2]:
        experiments.append((f"H3_SL_{sl}", {"PROFIT_TARGET_PCT": 2.0, "STOP_LOSS_ENABLED": True, "STOP_LOSS_PCT": float(sl)}, FAST_ALL))

    # H4: Min hold variation
    for hold in [3, 10, 20, 40, 80]:
        experiments.append((f"H4_HOLD_{hold}", {"MIN_HOLD_BARS": hold}, FAST_ALL))

    # H5: Symbol subset — need to discover per-symbol Sharpe first
    experiments.append(("H5_TOP5_GUESS", {}, TOP5_TRADIER))
    experiments.append(("H5_MID", {}, MID_TRADIER))
    experiments.append(("H5_BOTTOM", {}, BOTTOM_TRADIER))
    experiments.append(("H5_ALL12", {}, FAST_ALL))

    # H6: HTF strictness on guessed top5
    experiments.append(("H6_HTF_STRICT", {"HTF_MIN_ALIGNED": 3, "D_TREND_REQUIRED": True}, TOP5_TRADIER))
    experiments.append(("H6_HTF_LOOSE", {"HTF_MIN_ALIGNED": 0, "D_TREND_REQUIRED": False}, TOP5_TRADIER))

    # H7: Confluence mode (alt to strength filter)
    for n in [2, 3, 4]:
        experiments.append((f"H7_CONFLUENCE_{n}", {"CONFLUENCE_MODE_ENABLED": True, "CONFLUENCE_MIN_BLOCKS": n, "STRENGTH_FILTER_ENABLED": False}, FAST_ALL))

    # Tradier-specific: disable each stock gate individually
    experiments.append(("H8_NO_KZONE", {"K_ZONE_ENTRY_ENABLED": False}, FAST_ALL))
    experiments.append(("H8_NO_MFI_ENTRY", {"MFI_ENTRY_ENABLED": False}, FAST_ALL))
    experiments.append(("H8_NO_VWAP", {"VWAP_FILTER_ENABLED": False}, FAST_ALL))
    experiments.append(("H8_NO_FH", {"FH_MOMENTUM_ENABLED": False}, FAST_ALL))
    experiments.append(("H8_NO_DC_DAY", {"DC_DAYTRADE_ENABLED": False}, FAST_ALL))
    experiments.append(("H8_NO_STOCH1H_EXIT", {"STOCH_CROSS_1H_EXIT_ENABLED": False}, FAST_ALL))
    experiments.append(("H8_NO_MFI_FLIP", {"MFI_FLIP_EXIT_ENABLED": False}, FAST_ALL))

    # Baseline
    experiments.append(("BASELINE_WINNER", {}, FAST_ALL))

    print(f"Running {len(experiments)} tradier micro-experiments in parallel (4 workers)...")
    results = []
    with ProcessPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(run_exp, e): e for e in experiments}
        for fut in as_completed(futures):
            try:
                r = fut.result()
                results.append(r)
                print(f"  [{len(results):2d}/{len(experiments)}] {r['label']:<22} Sharpe={r['sharpe']:.3f} trades={r['trades']:>5} wr={r.get('wr', 0):.1f}% avg={r.get('avg_pnl_pct', 0):.3f}% syms={r['symbols']} ({r['elapsed']}s)")
            except Exception as e:
                print(f"  ERROR: {e}")

    valid = [r for r in results if r.get('trades', 0) >= 30]
    valid.sort(key=lambda r: r['sharpe'], reverse=True)
    print(f"\n{'='*100}")
    print(f"  TOP 15 TRADIER by Sharpe (trades>=30):")
    print(f"{'='*100}")
    for r in valid[:15]:
        print(f"  {r['label']:<22} Sharpe={r['sharpe']:.4f} trades={r['trades']:>5} wr={r.get('wr', 0):.1f}% avg={r.get('avg_pnl_pct', 0):.3f}%  cfg={r['cfg']}")

    out_path = BASE / "data" / "sweep_results" / f"v8_micro_experiments_tradier_{int(time.time())}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
