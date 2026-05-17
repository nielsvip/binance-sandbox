"""v4_big_sweep.py — run the v4 aggressive winning config across a full universe.

User mandate 2026-05-17 ~18:00 UTC: "do not wire into live just yet — run the
new settings on the big sweeps first." This pools per-trade returns across the
full universe through metrics_guard so we get an honest sample-floor verdict
on whether the per-symbol 4× B&H winner generalizes.

Usage:
    python v4_big_sweep.py --mode tradier --config clean
    python v4_big_sweep.py --mode crypto  --config nuclear

Reads symbol universe from CLI or auto-detects:
    tradier → 114 stocks (the trb+trc union minus blacklist used in v3exp sweep)
    crypto  → 9 majors (skip corrupt LINKUSDC)
"""
from __future__ import annotations

import argparse, datetime, json, sys, time
from pathlib import Path
from typing import Dict, List, Any, Optional

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import metrics_guard
from v8_struct_v4_aggressive import AggressiveCfg, simulate_aggressive
from v8_vec_structure_sweep import load_npz_slim

import platform
IS_SERVER = platform.system() == "Linux"
BASE = Path("/home/niels/binance-sandbox") if IS_SERVER else Path("/Users/niels/Documents/binance")
SWEEP_RESULTS = BASE / "data" / "sweep_results"
DIAGNOSTIC_DIR = BASE / "data" / "_diagnostic"

SAMPLE_FLOOR_CRYPTO = 48
SAMPLE_FLOOR_STOCKS = 100
MIN_TRADES_PER_SYM = 30
MIN_YEARS = 1.0


TRADIER_114 = ("ACN,AEM,AG,AGCO,AGI,ALB,AMD,AMZN,AR,ARM,ASTS,AU,AVGO,AXON,BG,BHP,BKR,BNO,BOTZ,CDE,CF,"
               "CHRD,CIBR,CLF,CMC,COP,COPX,CRK,CRWD,CRWV,CTRA,DAR,DE,DHR,DINO,DIS,DVN,EGLE,EGO,EOG,"
               "EPD,EQT,FANG,FCX,FIVN,FNV,GDX,GDXJ,GLD,GM,GOOGL,HII,HL,IBIT,IBM,INTC,KGC,LAC,LDOS,"
               "LEXX,LNG,LRCX,MA,MOS,MP,MPC,MSTR,NEM,NLR,NOC,NUE,NUKZ,NVDA,NXE,OKE,OLED,PAAS,PBF,"
               "PDBC,PLTR,PR,PSX,PYPL,RBLX,RGLD,RIO,ROBO,ROKU,RRC,RS,SAP,SCCO,SLB,SLV,SMG,SNDK,STLD,"
               "STZ,TDG,TECK,TRGP,TTD,TXN,UAN,UEC,USAR,USO,UUUU,VALE,VLO,WDAY,WPM,XLE,XOP").split(",")
CRYPTO_9 = "BTC,ETH,SOL,XRP,AVAX,ADA,BNB,BAT,1000LUNC".split(",")


def cfg_clean() -> AggressiveCfg:
    return AggressiveCfg(
        exit_X1_topcatch_enabled=False, exit_X3_hardstop_atr_mult=3.0,
        exit_X5_structural_flip_enabled=False, cooldown_bars_after_exit=50,
        path_B_k15_max=25.0, path_B_k1h_max=30.0,
        pyramid_enabled=True, max_pyramid_levels=30, pyramid_add_fraction=1.0,
        pyramid_min_gain_since_last_pct=0.3, pyramid_tf="4h",
        pyramid_also_on_k1h_oversold=True,
        htf_trend_filter_enabled=True, path_G_momentum_continuation_enabled=True,
        exit_X2_trailing_pct=20.0,
    )


def cfg_nuclear() -> AggressiveCfg:
    c = cfg_clean()
    c.max_pyramid_levels = 50
    c.pyramid_add_fraction = 1.5
    c.pyramid_tf = "1h"
    return c


def cfg_safer() -> AggressiveCfg:
    """Less curve-fit version — narrower pyramid, more diversification-friendly."""
    return AggressiveCfg(
        exit_X1_topcatch_enabled=False, exit_X3_hardstop_atr_mult=2.5,
        exit_X5_structural_flip_enabled=False, cooldown_bars_after_exit=80,
        path_B_k15_max=25.0, path_B_k1h_max=30.0,
        pyramid_enabled=True, max_pyramid_levels=8, pyramid_add_fraction=0.5,
        pyramid_min_gain_since_last_pct=2.0, pyramid_tf="D",
        pyramid_also_on_k1h_oversold=False,
        htf_trend_filter_enabled=True, path_G_momentum_continuation_enabled=True,
        exit_X2_trailing_pct=10.0,
    )


CFG_REGISTRY = {"clean": cfg_clean, "nuclear": cfg_nuclear, "safer": cfg_safer}


def run_universe(mode: str, symbols: List[str], cfg: AggressiveCfg, start: str, label: str) -> Dict[str, Any]:
    start_ts = int(datetime.datetime.strptime(start, "%Y-%m-%d")
                   .replace(tzinfo=datetime.timezone.utc).timestamp())
    rows: List[Dict[str, Any]] = []
    returns_by_sym: Dict[str, List[float]] = {}
    bh_mults: Dict[str, float] = {}
    cm_mults: Dict[str, float] = {}
    first_ts = None
    last_ts = None
    t0 = time.time()
    print(f"[CFG] mode={mode} symbols={len(symbols)} start={start} label={label}")
    print(f"      cfg: trail={cfg.exit_X2_trailing_pct} pyr_tf={cfg.pyramid_tf} "
          f"pyr_max={cfg.max_pyramid_levels} pyr_frac={cfg.pyramid_add_fraction} "
          f"htf_filter={cfg.htf_trend_filter_enabled}")
    for i, sym in enumerate(symbols, 1):
        try:
            r = simulate_aggressive(sym, mode, cfg, start_ts=start_ts)
        except FileNotFoundError as e:
            print(f"  [{i}/{len(symbols)}] {sym}: NPZ missing — skip")
            continue
        except Exception as e:
            print(f"  [{i}/{len(symbols)}] {sym}: {type(e).__name__}: {e}")
            continue
        if r.get("skip"):
            continue
        if r["trades"] == 0:
            print(f"  [{i}/{len(symbols)}] {sym}: 0 trades")
            continue
        returns_by_sym[sym] = r["trade_returns"]
        bh_mults[sym] = r["bh_mult"]
        cm_mults[sym] = r["compound_mult"]
        rows.append({
            "sym": sym, "bars": r["n_bars"], "years": r["years"],
            "trades": r["trades"], "wr_pct": r["wr_pct"],
            "avg_gain_trade": r["avg_gain_pct"],
            "bh_mult": r["bh_mult"], "compound_mult": r["compound_mult"],
            "ratio_vs_bh": r["ratio_vs_bh"],
        })
        # Track time span
        # Approx: use first/last NPZ ts — borrow it from per-sym sim
        # (we don't return ts from simulate; reuse load_npz_slim quickly)
        if first_ts is None or last_ts is None:
            try:
                _npz, _ts = load_npz_slim(sym, mode, start_ts=start_ts)
                if first_ts is None or int(_ts[0]) < first_ts: first_ts = int(_ts[0])
                if last_ts is None or int(_ts[-1]) > last_ts:  last_ts = int(_ts[-1])
            except Exception:
                pass
    elapsed = time.time() - t0
    n_syms = len(returns_by_sym)
    total_trades = sum(len(v) for v in returns_by_sym.values())
    if n_syms == 0:
        print("[ABORT] no symbols produced trades"); return {"error": "empty"}
    years = (last_ts - first_ts) / (365.25 * 24 * 3600) if (first_ts and last_ts) else 0.0
    # Canonical metrics through metrics_guard
    std = metrics_guard.standard_metric_set(returns_by_sym, years=years)
    # Per-sym ratio_vs_bh aggregates
    ratios = np.array([row["ratio_vs_bh"] for row in rows if np.isfinite(row["ratio_vs_bh"])])
    bhs = np.array([row["bh_mult"] for row in rows])
    cms = np.array([row["compound_mult"] for row in rows])
    # Equally-weighted portfolio: mean of compound_mult across syms
    eq_weighted_compound = float(np.mean(cms))
    eq_weighted_bh = float(np.mean(bhs))
    eq_weighted_ratio = eq_weighted_compound / eq_weighted_bh if eq_weighted_bh > 0 else float('inf')

    trades_per_sym = total_trades / max(n_syms, 1)
    floor_n = SAMPLE_FLOOR_CRYPTO if mode == "crypto" else SAMPLE_FLOOR_STOCKS
    floor_pass = (n_syms >= floor_n and trades_per_sym >= MIN_TRADES_PER_SYM and years >= MIN_YEARS)

    print()
    print(f"=== {label} {mode} {n_syms} syms ===")
    print(f"Pool sharpe   : {std['pool_sharpe']:+.4f}  ({metrics_guard.tier_name(std['pool_sharpe'])})")
    print(f"Sym sharpe    : {std['sym_sharpe']:+.4f}")
    print(f"avg_gain_trade: {std['avg_gain_trade']:+.3f}%/trade")
    print(f"gain_per_yr   : {std['gain_per_yr']:+.2f}%/yr")
    print(f"gain_sym_yr   : {std['gain_sym_yr']:+.4f}%/sym/yr")
    print(f"Trades total  : {total_trades}  (per sym: {trades_per_sym:.1f})")
    print(f"Years         : {years:.2f}")
    print(f"")
    print(f"B&H mean      : {eq_weighted_bh:.2f}×   (median {np.median(bhs):.2f}×)")
    print(f"Strat mean    : {eq_weighted_compound:.2f}×  (median {np.median(cms):.2f}×)")
    print(f"× B&H mean    : {eq_weighted_ratio:.2f}×   (median {np.median(ratios):.2f}×)")
    print(f"× B&H >=4 cnt : {int((ratios>=4).sum())}/{len(ratios)} symbols clear 4× B&H individually")
    print(f"× B&H >=1 cnt : {int((ratios>=1).sum())}/{len(ratios)} symbols beat B&H")
    print(f"Wall-clock    : {elapsed:.1f}s")
    print(f"")
    floor_str = "✓ PASS" if floor_pass else "✗ FAIL"
    print(f"SAMPLE FLOOR  : {floor_str}  (need n_syms>={floor_n}, trades/sym>={MIN_TRADES_PER_SYM}, years>={MIN_YEARS})")
    if not floor_pass:
        if n_syms < floor_n: print(f"               • n_syms {n_syms} < {floor_n}")
        if trades_per_sym < MIN_TRADES_PER_SYM: print(f"               • trades/sym {trades_per_sym:.1f} < {MIN_TRADES_PER_SYM}")
        if years < MIN_YEARS: print(f"               • years {years:.2f} < {MIN_YEARS}")

    # Write canonical row
    out_dir = SWEEP_RESULTS if floor_pass else DIAGNOSTIC_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    ts_run = int(time.time())
    csv_path = out_dir / f"struct_v4_big_{mode}_{label}_{ts_run}.csv"
    row_for_guard = {
        "iter": f"v4_{label}",
        "pool_sharpe": std["pool_sharpe"], "sym_sharpe": std["sym_sharpe"],
        "avg_gain_trade": std["avg_gain_trade"], "gain_per_yr": std["gain_per_yr"],
        "gain_sym_yr": std["gain_sym_yr"], "max_dd_pct": 0.0,  # placeholder
        "trades": total_trades, "n_syms": n_syms, "years": round(years, 3),
        "bh_mean_mult": round(eq_weighted_bh, 3),
        "strat_mean_mult": round(eq_weighted_compound, 3),
        "x_bh_mean": round(eq_weighted_ratio, 3),
        "x_bh_median": round(float(np.median(ratios)), 3),
        "n_clear_4x": int((ratios >= 4).sum()),
        "n_beat_bh": int((ratios >= 1).sum()),
        "label": label,
        "tier": metrics_guard.tier_name(std["pool_sharpe"]),
    }
    # max DD across pooled returns
    all_returns = [r for v in returns_by_sym.values() for r in v]
    from v8_vec_sweep import _max_dd_pct
    row_for_guard["max_dd_pct"] = _max_dd_pct(all_returns)
    try:
        metrics_guard.write_sharpe_row(csv_path, row_for_guard, mode=mode, append=True)
        print(f"\n[CSV] wrote → {csv_path}")
    except metrics_guard.FakeMetricRefused as e:
        print(f"\n[REFUSED] {e}")
    except Exception as e:
        print(f"\n[ERROR] write_sharpe_row: {e}")

    # Per-sym detail CSV (always)
    detail_path = out_dir / f"struct_v4_big_{mode}_{label}_{ts_run}_per_sym.csv"
    with detail_path.open("w") as f:
        f.write("sym,years,trades,wr_pct,avg_gain_trade,bh_mult,compound_mult,ratio_vs_bh,cleared_4x\n")
        for r in rows:
            cleared = "1" if r["ratio_vs_bh"] >= 4 else "0"
            f.write(f"{r['sym']},{r['years']:.3f},{r['trades']},{r['wr_pct']:.1f},"
                    f"{r['avg_gain_trade']:+.3f},{r['bh_mult']:.3f},"
                    f"{r['compound_mult']:.3f},{r['ratio_vs_bh']:+.3f},{cleared}\n")
    print(f"[CSV] per-sym → {detail_path}")
    return row_for_guard


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("crypto", "tradier"), required=True)
    ap.add_argument("--config", choices=tuple(CFG_REGISTRY.keys()), default="clean")
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--symbols", default="auto")
    args = ap.parse_args()
    if args.symbols == "auto":
        symbols = TRADIER_114 if args.mode == "tradier" else CRYPTO_9
    else:
        symbols = args.symbols.split(",")
    cfg = CFG_REGISTRY[args.config]()
    run_universe(args.mode, symbols, cfg, args.start, args.config)


if __name__ == "__main__":
    main()
