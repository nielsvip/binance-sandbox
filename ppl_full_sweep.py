#!/usr/bin/env python3
"""
ppl_full_sweep.py — full-scale pool-sharpe sweep (2026-04-21).

Runs a JSONL of config overrides on a large symbol set (50 crypto / 128 stock),
reports POOL sharpe (per user rule 2026-04-21: sharpe = mean/std of ALL trades pooled).

Worker-sharding via multiprocessing.Pool (default 12 sym per worker). Each worker
returns its raw per-symbol trade PnL arrays; master concatenates and computes true
pool sharpe across the combined stream. Trades-weighted gain / worst-case DD.

Outputs:
  data/full_sweep_{mode}_{ts}/
    - configs.jsonl       (the grid tested)
    - results.csv         (pool_sharpe, accumulated_gain_pct, max_dd_pct, trades, score)
    - worker_breakdown.csv (pool_sharpe per 12-sym shard)
    - live.log            (streaming progress for monitor)

Usage:
  python3 ppl_full_sweep.py --mode crypto --start 2022-01-01 \
    --symbols 50 --worker-size 12 --configs /tmp/winners.jsonl
"""
import argparse
import csv
import json
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v8_quick_engine import QuickConfig, load_npz, simulate


CRYPTO_50 = [
    "BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","ADAUSDT","AVAXUSDT","DOTUSDT",
    "LINKUSDT","LTCUSDT","UNIUSDT","ATOMUSDT","BCHUSDT","ETCUSDT","FILUSDT","TRXUSDT",
    "NEARUSDT","AAVEUSDT","ALGOUSDT","APTUSDT","ARBUSDT","AXSUSDT","BANDUSDT","BATUSDT",
    "CHZUSDT","COMPUSDT","CRVUSDT","DOGEUSDT","EGLDUSDT","ENJUSDT","EOSUSDT","FLOWUSDT",
    "GALAUSDT","GMTUSDT","GRTUSDT","ICPUSDT","INJUSDT","KAVAUSDT","KSMUSDT","MANAUSDT",
    "MKRUSDT","NEOUSDT","ONTUSDT","OPUSDT","QTUMUSDT","ROSEUSDT","RUNEUSDT","SANDUSDT",
    "SUSHIUSDT","VETUSDT",
]
# 128 stocks across sectors
STOCKS_128 = [
    # Mega-cap tech
    "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","AVGO","ORCL","ADBE","CRM","CSCO",
    # Semis + hardware
    "AMD","INTC","QCOM","TXN","AMAT","MU","LRCX","KLAC","MRVL","TSM","ASML","NXPI",
    # Enterprise/software
    "NOW","INTU","IBM","PANW","SNPS","CDNS","FTNT","WDAY","TEAM","SNOW","DDOG","NET",
    # Financials
    "JPM","BAC","WFC","GS","MS","C","USB","PNC","AXP","BLK","SCHW","BX",
    # Healthcare
    "UNH","JNJ","LLY","PFE","ABBV","MRK","TMO","DHR","ABT","BMY","AMGN","GILD",
    # Energy
    "XOM","CVX","COP","SLB","OXY","EOG","MPC","PSX","HAL","BKR","DVN","FANG",
    # Consumer
    "HD","PG","KO","PEP","COST","WMT","MCD","NKE","SBUX","DIS","TJX","LOW",
    # Industrials
    "CAT","GE","BA","UPS","HON","DE","RTX","LMT","NOC","UNP","CSX","NSC",
    # Materials/staples
    "LIN","FCX","APD","ECL","NEM","SHW","DD","EMR",
    # Comms/media
    "T","VZ","NFLX","CMCSA","TMUS","CHTR",
    # ETFs + commodities
    "SPY","QQQ","IWM","DIA","GLD","SLV","USO","TLT","HYG","LQD","XLF","XLE",
]


def worker_run(args):
    """One worker process: load subset, run simulate, return raw per-symbol pnl."""
    sub_symbols, npz_dir, mode, start, capital, cfg_dict, worker_id = args
    os.environ["V8_RETURN_RAW_PNL"] = "1"
    cfg = QuickConfig()
    if mode == "tradier":
        cfg.apply_tradier_defaults()
    for k, v in cfg_dict.items():
        if not hasattr(cfg, k): continue
        cur = getattr(cfg, k)
        try:
            if isinstance(cur, bool): setattr(cfg, k, bool(v))
            elif isinstance(cur, int): setattr(cfg, k, int(v))
            elif isinstance(cur, float): setattr(cfg, k, float(v))
            else: setattr(cfg, k, v)
        except Exception: pass
    stores = load_npz(mode, sub_symbols, start, npz_dir)
    t0 = time.time()
    r = simulate(stores, cfg, capital)
    elapsed = time.time() - t0
    return {
        "worker_id": worker_id,
        "sub_symbols": sub_symbols,
        "n_loaded": len(stores),
        "per_symbol_pnl": r.get("per_symbol_pnl", {}),
        "all_pnl": r.get("all_pnl", []),
        "worker_pool_sharpe": r.get("pool_sharpe", 0),
        "worker_gain_pct": r.get("accumulated_gain_pct", 0),
        "worker_max_dd_pct": r.get("max_dd_pct", 0),
        "worker_trades": r.get("trades", 0),
        "worker_elapsed_s": elapsed,
    }


def aggregate(worker_results: List[Dict], start_size: float = 10000.0) -> Dict[str, Any]:
    """Concatenate all per-symbol pnl across workers, compute TRUE pool sharpe + max_dd + gain."""
    import numpy as np
    all_pnl = []
    per_sym_dds = []
    for wr in worker_results:
        for sym, pl in wr.get("per_symbol_pnl", {}).items():
            if pl:
                all_pnl.extend(pl)
                # Per-symbol max DD from cumsum
                cum = np.cumsum(pl)
                run_max = np.maximum.accumulate(cum)
                dd = (run_max - cum).max() if len(cum) > 0 else 0.0
                per_sym_dds.append(float(dd))
    if len(all_pnl) < 30:
        return {"pool_sharpe": 0, "accumulated_gain_pct": 0, "max_dd_pct": 0,
                "avg_dd_pct": 0, "trades": len(all_pnl), "symbols_used": sum(wr["n_loaded"] for wr in worker_results),
                "wins": 0, "losses": 0}
    p = np.array(all_pnl)
    pool_sharpe = float(p.mean() / p.std()) if p.std() > 1e-9 else 0.0
    pool_sharpe = max(min(pool_sharpe, 20.0), -20.0)
    return {
        "pool_sharpe": round(pool_sharpe, 4),
        "accumulated_gain_pct": round(float(p.sum()), 4),
        "max_dd_pct": round(max(per_sym_dds) if per_sym_dds else 0.0, 4),
        "avg_dd_pct": round(sum(per_sym_dds) / len(per_sym_dds) if per_sym_dds else 0.0, 4),
        "trades": int(len(all_pnl)),
        "wins": int((p > 0).sum()),
        "losses": int((p <= 0).sum()),
        "wr": round(float((p > 0).sum()) / len(all_pnl) * 100, 2),
        "avg_pnl_pct": round(float(p.mean()), 4),
        "symbols_used": sum(wr["n_loaded"] for wr in worker_results),
    }


def score(agg: Dict) -> float:
    s = float(agg.get("pool_sharpe", 0) or 0)
    g = float(agg.get("accumulated_gain_pct", 0) or 0)
    d = float(agg.get("max_dd_pct", 0) or 0)
    t = int(agg.get("trades", 0) or 0)
    if t < 30 or s <= 0 or g <= 0: return 0.0
    return round(s * g / max(d, 1.0), 4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["crypto", "tradier"], required=True)
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--symbols", default="", help="comma-sep list OR preset: '50' / '128'")
    ap.add_argument("--worker-size", type=int, default=12)
    ap.add_argument("--configs", required=True, help="JSONL file — one config override per line")
    ap.add_argument("--baseline-config-py", default="", help="Path to baseline config.py to start from")
    ap.add_argument("--capital", type=float, default=10000.0)
    ap.add_argument("--npz-dir", default="")
    ap.add_argument("--out-dir", default="")
    ap.add_argument("--max-workers", type=int, default=0, help="Limit parallel workers (0=auto)")
    args = ap.parse_args()

    # Resolve symbol list
    if args.symbols == "50" or (not args.symbols and args.mode == "crypto"):
        symbols = CRYPTO_50[:50]
    elif args.symbols == "128" or (not args.symbols and args.mode == "tradier"):
        symbols = STOCKS_128[:128]
    else:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    out_dir = Path(args.out_dir) if args.out_dir else Path("data") / f"full_sweep_{args.mode}_{time.strftime('%Y%m%d_%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    live_log = out_dir / "live.log"
    def log(msg):
        print(msg, flush=True)
        try:
            with open(live_log, "a") as f: f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        except Exception: pass

    log(f"[FULL_SWEEP] mode={args.mode} symbols={len(symbols)} worker-size={args.worker_size} out={out_dir}")

    # Load baseline config values (optional)
    base_ovr = {}
    if args.baseline_config_py and Path(args.baseline_config_py).exists():
        import importlib.util
        from dataclasses import fields as dc_fields, is_dataclass
        spec = importlib.util.spec_from_file_location("base_cfg_mod", args.baseline_config_py)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
            for name in ("Config", "QuickConfig", "TradingConfig"):
                if hasattr(mod, name) and is_dataclass(getattr(mod, name)):
                    cls = getattr(mod, name)
                    inst = cls()
                    qc_field_names = {f.name for f in dc_fields(QuickConfig())}
                    for f in dc_fields(inst):
                        if f.name in qc_field_names:
                            try: base_ovr[f.name] = getattr(inst, f.name)
                            except Exception: pass
                    break
        except Exception as e:
            log(f"[FULL_SWEEP] baseline load warning: {e}")
    log(f"[FULL_SWEEP] baseline overrides: {len(base_ovr)} keys")

    # Load configs JSONL
    configs = []
    with open(args.configs) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"): continue
            ovr = json.loads(line)
            full = dict(base_ovr); full.update(ovr)
            configs.append({"variant": ovr, "full": full})
    log(f"[FULL_SWEEP] loaded {len(configs)} configs to sweep")

    # Resolve NPZ dir
    npz_dir = args.npz_dir
    if not npz_dir:
        for base in ("/home/niels/binance-sandbox", str(Path(__file__).resolve().parent)):
            for sub in ("backtest_v8/indicators", "backtest_v5/indicators_3m", "backtest_v5/indicators_5m_tradier"):
                d = Path(base) / sub
                if d.exists() and any(d.glob("*.npz")):
                    npz_dir = str(d); break
            if npz_dir: break
    log(f"[FULL_SWEEP] npz_dir={npz_dir}")

    # Shard symbols into workers
    worker_size = args.worker_size
    shards = [symbols[i:i + worker_size] for i in range(0, len(symbols), worker_size)]
    n_workers = min(len(shards), args.max_workers or mp.cpu_count())
    log(f"[FULL_SWEEP] {len(shards)} shards of up to {worker_size} symbols, running {n_workers} at a time")

    # Write the configs file for reproducibility
    with open(out_dir / "configs.jsonl", "w") as f:
        for c in configs:
            f.write(json.dumps(c["variant"]) + "\n")

    results = []
    fieldnames = ["idx", "score", "pool_sharpe", "accumulated_gain_pct", "max_dd_pct", "avg_dd_pct",
                  "trades", "wr", "avg_pnl_pct", "wins", "losses", "symbols_used", "elapsed_s", "variant_json"]
    results_csv = out_dir / "results.csv"
    with open(results_csv, "w", newline="") as f:
        csv.DictWriter(f, fieldnames=fieldnames).writeheader()

    breakdown_csv = out_dir / "worker_breakdown.csv"
    with open(breakdown_csv, "w", newline="") as f:
        csv.DictWriter(f, fieldnames=["config_idx", "worker_id", "shard_size", "worker_pool_sharpe",
                                      "worker_gain_pct", "worker_max_dd_pct", "worker_trades",
                                      "worker_elapsed_s"]).writeheader()

    total_start = time.time()
    for ci, cfg_entry in enumerate(configs, 1):
        t_cfg = time.time()
        worker_args = [(shard, npz_dir, args.mode, args.start, args.capital, cfg_entry["full"], wi)
                       for wi, shard in enumerate(shards)]
        with mp.Pool(n_workers) as pool:
            worker_results = pool.map(worker_run, worker_args)
        agg = aggregate(worker_results, args.capital)
        sc = score(agg)
        elapsed = time.time() - t_cfg
        row = {
            "idx": ci, "score": sc, **{k: agg[k] for k in ("pool_sharpe","accumulated_gain_pct","max_dd_pct",
                                                          "avg_dd_pct","trades","wr","avg_pnl_pct","wins","losses","symbols_used")},
            "elapsed_s": round(elapsed, 1),
            "variant_json": json.dumps(cfg_entry["variant"]),
        }
        results.append(row)
        with open(results_csv, "a", newline="") as f:
            csv.DictWriter(f, fieldnames=fieldnames).writerow(row)
        with open(breakdown_csv, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["config_idx","worker_id","shard_size","worker_pool_sharpe",
                                              "worker_gain_pct","worker_max_dd_pct","worker_trades","worker_elapsed_s"])
            for wr in worker_results:
                w.writerow({"config_idx": ci, "worker_id": wr["worker_id"], "shard_size": wr["n_loaded"],
                            "worker_pool_sharpe": wr["worker_pool_sharpe"],
                            "worker_gain_pct": wr["worker_gain_pct"],
                            "worker_max_dd_pct": wr["worker_max_dd_pct"],
                            "worker_trades": wr["worker_trades"],
                            "worker_elapsed_s": round(wr["worker_elapsed_s"], 2)})
        log(f"[{ci}/{len(configs)}] score={sc} pool_sharpe={agg['pool_sharpe']} "
            f"gain={agg['accumulated_gain_pct']:.1f}% dd={agg['max_dd_pct']:.1f}% "
            f"trades={agg['trades']} (cfg_elapsed={elapsed:.0f}s total={time.time()-total_start:.0f}s) "
            f"variant={cfg_entry['variant']}")

    # Sort results by score
    results.sort(key=lambda r: r["score"], reverse=True)
    log(f"\n[FULL_SWEEP] DONE — {len(configs)} configs, total {time.time()-total_start:.0f}s. Top 5:")
    for r in results[:5]:
        log(f"  #{r['idx']}: score={r['score']} sharpe={r['pool_sharpe']} "
            f"gain={r['accumulated_gain_pct']:.1f}% dd={r['max_dd_pct']:.1f}% trades={r['trades']} "
            f"variant={r['variant_json']}")


if __name__ == "__main__":
    main()
