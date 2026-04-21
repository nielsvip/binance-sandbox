#!/usr/bin/env python3
"""
ppl_orchestrator.py — 3-stage sequential sweep runner (2026-04-21).

Stage 1: 16 binary on/off combos of the headline features (WRONG_SIDE, NOLOSS_BYPASS,
         PPL, HEDGE_ENTRY_MODE). Picks top 3 by combined score.
Stage 2: Threshold grid on Stage-1 winners (WRONG_SIDE thresholds + PPL parameters +
         HEDGE_ENTRY_MODE sweep). Picks top 5.
Stage 3: Top 5 run through backtest_v8_engine.py (real code path) on 48 symbols.
         Confirms winners hold up outside the fast sandbox.

Scoring: per_sym_sharpe × accumulated_gain_pct / max(max_dd_pct, 1.0).
Higher = better. NaN/zero-trade runs score 0.

Marginal analysis: for each feature, score(ON)/score(OFF) ratio holding other
features at Stage-1 winner values. Surfaces which knobs drive the gain.

Usage:
  # Crypto (S1 or local with NPZ available):
  python3 ppl_orchestrator.py --mode crypto --start 2022-01-01 \
    --symbols BTCUSDT,ETHUSDT,SOLUSDT,LINKUSDT,BNBUSDT,XRPUSDT,AVAXUSDT,DOTUSDT,MATICUSDT,ATOMUSDT,LTCUSDT,UNIUSDT

  # Tradier (S2):
  python3 ppl_orchestrator.py --mode tradier --start 2023-01-01 \
    --symbols AAPL,MSFT,NVDA,AMZN,JPM,XOM,ABBV,TSLA,SPY,META,BA,GLD
"""
import argparse
import csv
import itertools
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v8_quick_engine import QuickConfig, load_npz, simulate


CRYPTO_48 = [
    "BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","ADAUSDT","AVAXUSDT","DOTUSDT",
    "LINKUSDT","LTCUSDT","UNIUSDT","ATOMUSDT","BCHUSDT","ETCUSDT","FILUSDT","TRXUSDT",
    "NEARUSDT","AAVEUSDT","ALGOUSDT","APTUSDT","ARBUSDT","AXSUSDT","BANDUSDT","BATUSDT",
    "CHZUSDT","COMPUSDT","CRVUSDT","DOGEUSDT","EGLDUSDT","ENJUSDT","EOSUSDT","FLOWUSDT",
    "GALAUSDT","GMTUSDT","GRTUSDT","ICPUSDT","INJUSDT","KAVAUSDT","KSMUSDT","MANAUSDT",
    "MKRUSDT","NEOUSDT","ONTUSDT","OPUSDT","QTUMUSDT","ROSEUSDT","RUNEUSDT","SANDUSDT",
]
TRADIER_48 = [
    "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","SPY","QQQ","JPM",
    "BAC","XOM","CVX","ABBV","LLY","UNH","JNJ","PFE","MRK","V",
    "MA","PG","KO","PEP","COST","WMT","HD","MCD","NKE","DIS",
    "BA","CAT","GE","F","GM","T","VZ","CSCO","ADBE","CRM",
    "NFLX","AMD","INTC","PYPL","GLD","SLV","USO","TLT",
]


def combined_score(r: Dict[str, Any]) -> float:
    """Higher = better. Per-sym-avg Sharpe * accumulated_gain_pct / max_dd_pct (>=1.0 floor)."""
    s = float(r.get("sharpe", 0) or 0)
    g = float(r.get("accumulated_gain_pct", 0) or 0)
    d = float(r.get("max_dd_pct", 0) or 0)
    t = int(r.get("trades", 0) or 0)
    if t < 30 or s <= 0 or g <= 0:
        return 0.0
    return s * g / max(d, 1.0)


def apply_overrides(base_cfg: QuickConfig, overrides: Dict[str, Any]) -> QuickConfig:
    """Clone base cfg and apply overrides in-place on the clone."""
    import copy
    cfg = copy.deepcopy(base_cfg)
    for k, v in overrides.items():
        if hasattr(cfg, k):
            cur = getattr(cfg, k)
            if isinstance(cur, bool):
                setattr(cfg, k, bool(v))
            elif isinstance(cur, int):
                setattr(cfg, k, int(v))
            elif isinstance(cur, float):
                setattr(cfg, k, float(v))
            else:
                setattr(cfg, k, v)
    return cfg


def run_one(stores, base_cfg: QuickConfig, overrides: Dict[str, Any], capital: float) -> Dict[str, Any]:
    """Run simulate() with overrides on a pre-loaded stores dict. Returns result dict + overrides + score."""
    t0 = time.time()
    cfg = apply_overrides(base_cfg, overrides)
    try:
        r = simulate(stores, cfg, capital)
    except Exception as e:
        r = {"error": f"{type(e).__name__}: {e}"}
    r["overrides"] = overrides
    r["elapsed_s"] = round(time.time() - t0, 2)
    r["score"] = combined_score(r)
    return r


def build_stage1_grid() -> List[Dict[str, Any]]:
    """16 binary configs: 4 features × 2 values. HEDGE_ENTRY_MODE uses LOSS_AND_WT (current) vs LOSS_OR_WT (alternative)."""
    out = []
    for ws, nlb, ppl, hem in itertools.product(
        [False, True],
        [False, True],
        [False, True],
        ["LOSS_AND_WT", "LOSS_OR_WT"],
    ):
        out.append({
            "WRONG_SIDE_ABS_KILL_ENABLED": ws,
            "NOLOSS_BYPASS_WT_5OF5_ENABLED": nlb,
            "PARTIAL_PROFIT_LOCK_ENABLED": ppl,
            "HEDGE_ENTRY_MODE": hem,
        })
    return out


def build_stage2_grid(stage1_winners: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Threshold sweep on each stage-1 winner. Only varies thresholds relevant to ON features."""
    out = []
    for winner in stage1_winners:
        base_ovr = winner["overrides"]
        if base_ovr.get("WRONG_SIDE_ABS_KILL_ENABLED"):
            for wt_req, wt_red, div_req, div_lb, age in itertools.product(
                [3, 4, 5], [2, 3, 4], [1, 2, 3], [10, 20, 40], [15, 30, 60, 120],
            ):
                if wt_red >= wt_req:
                    continue
                ovr = dict(base_ovr)
                ovr.update({
                    "WRONG_SIDE_WT_TFS_REQUIRED": wt_req,
                    "WRONG_SIDE_WT_TFS_REDUCED": wt_red,
                    "WRONG_SIDE_DIV_TFS_REQUIRED": div_req,
                    "WRONG_SIDE_DIV_LOOKBACK_BARS": div_lb,
                    "WRONG_SIDE_MIN_AGE_MIN": age,
                })
                out.append(ovr)
        if base_ovr.get("PARTIAL_PROFIT_LOCK_ENABLED"):
            for gain, arm, buf in itertools.product(
                [0.4, 0.5, 0.6], [0.6, 0.75, 1.0], [0.02, 0.05, 0.10],
            ):
                ovr = dict(base_ovr)
                ovr.update({
                    "PARTIAL_PROFIT_LOCK_GAIN_PCT": gain,
                    "PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT": arm,
                    "PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT": buf,
                })
                out.append(ovr)
        if not any(k in base_ovr for k in ("WRONG_SIDE_ABS_KILL_ENABLED", "PARTIAL_PROFIT_LOCK_ENABLED")):
            out.append(dict(base_ovr))
    seen = set()
    uniq = []
    for o in out:
        key = tuple(sorted(o.items()))
        if key not in seen:
            seen.add(key)
            uniq.append(o)
    return uniq


def marginal_contribution(results: List[Dict[str, Any]], feature_key: str) -> Dict[str, float]:
    """For a feature, compute mean score when ON vs OFF. Returns {'on': x, 'off': y, 'ratio': x/y}."""
    on_scores = [r["score"] for r in results if r["overrides"].get(feature_key) is True and r["score"] > 0]
    off_scores = [r["score"] for r in results if r["overrides"].get(feature_key) is False and r["score"] > 0]
    on_mean = sum(on_scores) / len(on_scores) if on_scores else 0.0
    off_mean = sum(off_scores) / len(off_scores) if off_scores else 0.0
    ratio = on_mean / off_mean if off_mean > 0 else (float("inf") if on_mean > 0 else 0.0)
    return {"on_mean": round(on_mean, 4), "off_mean": round(off_mean, 4), "ratio": round(ratio, 3), "n_on": len(on_scores), "n_off": len(off_scores)}


def write_stage_csv(results: List[Dict[str, Any]], out_path: Path, stage_name: str):
    if not results:
        return
    keys_all = set()
    for r in results:
        for k in r.keys(): keys_all.add(k)
        for k in r.get("overrides", {}).keys(): keys_all.add(f"ovr_{k}")
    fieldnames = ["stage", "score", "sharpe", "accumulated_gain_pct", "max_dd_pct", "avg_dd_pct",
                  "trades", "wr", "symbols_used", "pool_sharpe", "avg_pnl_pct", "elapsed_s", "error"]
    extra = sorted(k for k in keys_all if k.startswith("ovr_"))
    fieldnames += extra
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in results:
            row = {k: r.get(k, "") for k in fieldnames if not k.startswith("ovr_") and k != "stage"}
            row["stage"] = stage_name
            for ok, ov in r.get("overrides", {}).items():
                row[f"ovr_{ok}"] = ov
            w.writerow(row)


def stage_runner(stores, base_cfg, configs, capital, stage_name):
    """Run a list of configs, log progress, return sorted-by-score list."""
    print(f"\n===== {stage_name}: {len(configs)} configs on {len(stores)} symbols =====", flush=True)
    results = []
    t0 = time.time()
    for idx, ovr in enumerate(configs, 1):
        r = run_one(stores, base_cfg, ovr, capital)
        results.append(r)
        if idx % max(1, len(configs) // 10) == 0 or idx == len(configs):
            elapsed = time.time() - t0
            print(f"  [{stage_name}] {idx}/{len(configs)} done, elapsed={elapsed:.1f}s, "
                  f"best_score={max((r['score'] for r in results), default=0):.2f}", flush=True)
    results.sort(key=lambda r: r["score"], reverse=True)
    return results


def run_stage3(mode, winners, symbols, start_date, out_dir):
    """Launch backtest_v8_engine.py subprocess for each winner. Slow but real."""
    print(f"\n===== STAGE 3: backtest_v8_engine on {len(winners)} winners × {len(symbols)} symbols =====", flush=True)
    results = []
    for idx, w in enumerate(winners, 1):
        ovr = w["overrides"]
        ovr_file = out_dir / f"stage3_cfg_{idx}.json"
        with open(ovr_file, "w") as f:
            json.dump(ovr, f, indent=2)
        log_path = out_dir / f"stage3_run_{idx}.log"
        sym_str = ",".join(symbols)
        account = "ang" if mode == "crypto" else "trb"
        py = os.environ.get("V8_PYTHON", sys.executable)
        cmd = [py, "-u", "backtest_v8_engine.py",
               "--mode", mode, "--account", account, "--start", start_date,
               "--symbols", sym_str, "--capital", "10000"]
        env = os.environ.copy()
        env["V8_OVERRIDE_FILE"] = str(ovr_file)
        env["V8_SKIP_PROCESS_POSITION"] = "1"
        env["V8_SWEEP_MODE"] = "1"
        t0 = time.time()
        print(f"  [STAGE3] Launching config {idx}/{len(winners)}: {ovr}", flush=True)
        try:
            with open(log_path, "w") as lf:
                res = subprocess.run(cmd, env=env, stdout=lf, stderr=subprocess.STDOUT, timeout=7200)
            rc = res.returncode
        except subprocess.TimeoutExpired:
            rc = -1
            print(f"    TIMEOUT after 2hr", flush=True)
        elapsed = time.time() - t0
        sharpe_final = None
        gain_final = None
        try:
            with open(log_path) as lf:
                for line in lf:
                    if "V8_RESULT_FINAL" in line or "V8_RESULT_LIVE" in line:
                        for tok in line.split():
                            if tok.startswith("sharpe_per_trade="):
                                sharpe_final = float(tok.split("=", 1)[1])
                            elif tok.startswith("gain_pct="):
                                gain_final = float(tok.split("=", 1)[1].rstrip("%+"))
        except Exception: pass
        results.append({
            "overrides": ovr,
            "stage3_sharpe_per_trade": sharpe_final,
            "stage3_gain_pct": gain_final,
            "stage3_rc": rc,
            "stage3_elapsed_s": round(elapsed, 1),
            "stage3_log": str(log_path),
        })
        print(f"    done in {elapsed:.1f}s rc={rc} sharpe_pt={sharpe_final} gain={gain_final}", flush=True)
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["crypto", "tradier"], required=True)
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--symbols", type=str, required=True, help="comma-separated symbol list (12 recommended for stage 1-2)")
    ap.add_argument("--stage3-symbols", type=str, default="", help="48-symbol list for stage 3 (default: built-in 48)")
    ap.add_argument("--capital", type=float, default=10000.0)
    ap.add_argument("--npz-dir", default="")
    ap.add_argument("--top1", type=int, default=3, help="how many stage-1 winners to carry to stage 2")
    ap.add_argument("--top2", type=int, default=5, help="how many stage-2 winners to carry to stage 3")
    ap.add_argument("--skip-stage3", action="store_true", help="skip backtest_v8_engine validation")
    ap.add_argument("--out-dir", default="")
    args = ap.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    stage3_syms = [s.strip() for s in args.stage3_symbols.split(",") if s.strip()] or (CRYPTO_48 if args.mode == "crypto" else TRADIER_48)

    out_dir = Path(args.out_dir) if args.out_dir else Path("data/orchestrator") / f"{args.mode}_{time.strftime('%Y%m%d_%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    _live_log = out_dir / "run.log"
    _orig_print = print
    def _p(*a, **kw):
        kw["flush"] = True
        _orig_print(*a, **kw)
        try:
            with open(_live_log, "a") as _f:
                _orig_print(*a, file=_f)
        except Exception: pass
    globals()["print"] = _p
    print(f"[PPL_ORCH] output dir: {out_dir}")
    print(f"[PPL_ORCH] live log: {_live_log}")

    npz_dir = args.npz_dir
    if not npz_dir:
        for base in ("/home/niels/binance-sandbox", str(Path(__file__).resolve().parent)):
            for sub in ("backtest_v8/indicators", "backtest_v5/indicators_3m", "backtest_v5/indicators_5m_tradier"):
                d = Path(base) / sub
                if d.exists() and any(d.glob("*.npz")):
                    npz_dir = str(d); break
            if npz_dir: break
    print(f"[PPL_ORCH] npz_dir: {npz_dir}", flush=True)

    t_load = time.time()
    stores = load_npz(args.mode, symbols, args.start, npz_dir)
    if not stores:
        print(f"[PPL_ORCH] ERROR: no symbols loaded from {npz_dir}", file=sys.stderr)
        sys.exit(1)
    print(f"[PPL_ORCH] loaded {len(stores)} symbols in {time.time()-t_load:.1f}s", flush=True)

    base_cfg = QuickConfig.from_override_file(os.environ.get("V8_OVERRIDE_FILE", ""))
    if args.mode == "tradier":
        base_cfg.apply_tradier_defaults()

    # ===== STAGE 1 =====
    s1_configs = build_stage1_grid()
    s1_results = stage_runner(stores, base_cfg, s1_configs, args.capital, "STAGE1")
    write_stage_csv(s1_results, out_dir / "stage1.csv", "STAGE1")

    print(f"\n[STAGE1] top {args.top1} by combined score:", flush=True)
    for i, r in enumerate(s1_results[:args.top1], 1):
        print(f"  #{i} score={r['score']:.3f} sharpe={r.get('sharpe',0):.3f} gain={r.get('accumulated_gain_pct',0):.2f}% "
              f"max_dd={r.get('max_dd_pct',0):.2f}% trades={r.get('trades',0)} ovr={r['overrides']}", flush=True)

    print("\n[STAGE1] marginal contribution per feature (ON vs OFF mean score):", flush=True)
    for feat in ("WRONG_SIDE_ABS_KILL_ENABLED", "NOLOSS_BYPASS_WT_5OF5_ENABLED", "PARTIAL_PROFIT_LOCK_ENABLED"):
        mc = marginal_contribution(s1_results, feat)
        print(f"  {feat}: on={mc['on_mean']:.2f} off={mc['off_mean']:.2f} ratio={mc['ratio']} "
              f"(n_on={mc['n_on']} n_off={mc['n_off']})", flush=True)

    s1_winners = s1_results[:args.top1]

    # ===== STAGE 2 =====
    s2_configs = build_stage2_grid(s1_winners)
    if not s2_configs:
        print("[STAGE2] no configs generated (stage-1 winners had no tunable thresholds). Stopping.", flush=True)
        return
    s2_results = stage_runner(stores, base_cfg, s2_configs, args.capital, "STAGE2")
    write_stage_csv(s2_results, out_dir / "stage2.csv", "STAGE2")

    print(f"\n[STAGE2] top {args.top2} by combined score:", flush=True)
    for i, r in enumerate(s2_results[:args.top2], 1):
        print(f"  #{i} score={r['score']:.3f} sharpe={r.get('sharpe',0):.3f} gain={r.get('accumulated_gain_pct',0):.2f}% "
              f"max_dd={r.get('max_dd_pct',0):.2f}% trades={r.get('trades',0)} ovr={r['overrides']}", flush=True)

    s2_winners = s2_results[:args.top2]

    # ===== STAGE 3 =====
    if args.skip_stage3:
        print("[STAGE3] skipped per --skip-stage3", flush=True)
        return
    s3_results = run_stage3(args.mode, s2_winners, stage3_syms, args.start, out_dir)
    with open(out_dir / "stage3.json", "w") as f:
        json.dump(s3_results, f, indent=2)
    print(f"\n[STAGE3] results saved to {out_dir/'stage3.json'}", flush=True)

    print(f"\n[PPL_ORCH] DONE. Full outputs in {out_dir}", flush=True)


if __name__ == "__main__":
    main()
