#!/usr/bin/env python3
"""
ppl_ablation.py — single-knob ablation over every QuickConfig field (2026-04-21).

Loads baseline config from the latest server snapshot (le_dynamic_v2_baseline).
For each QuickConfig field:
  - bool: flip True↔False
  - int/float: try 0.5x and 2x of baseline value (or ±1 for small ints)
  - str/list: skip (user sweeps these manually)
Runs v8_quick_engine.simulate() for each variant on N symbols. Reports
"movers" — fields whose variant changed combined_score by >=10%.

Output: data/orchestrator/ablation_{mode}_{timestamp}/
  - baseline.json        (the baseline override dict used)
  - baseline_result.json (score of baseline)
  - movers.csv           (sorted: field, value_tested, baseline_score, new_score, delta, delta_pct)
  - full_ablation.csv    (every variant tested)
  - run.log              (live streaming log)

Usage:
  python3 ppl_ablation.py --mode crypto --start 2022-01-01 \
    --baseline data/orchestrator/server_snapshots/s1_crypto/config.py \
    --symbols BTCUSDT,ETHUSDT,SOLUSDT,LINKUSDT,BNBUSDT,XRPUSDT,AVAXUSDT,DOTUSDT,ATOMUSDT
"""
import argparse
import copy
import csv
import importlib.util
import json
import sys
import time
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from v8_quick_engine import QuickConfig, load_npz, simulate


def combined_score(r: Dict[str, Any]) -> float:
    """Uses POOL sharpe (mean/std of ALL trade returns across ALL symbols) per user 2026-04-21.
    Per-symbol-avg sharpe is diagnostic only — it lies when trade counts vary across symbols."""
    s = float(r.get("pool_sharpe", 0) or 0)
    g = float(r.get("accumulated_gain_pct", 0) or 0)
    d = float(r.get("max_dd_pct", 0) or 0)
    t = int(r.get("trades", 0) or 0)
    if t < 30 or s <= 0 or g <= 0:
        return 0.0
    return s * g / max(d, 1.0)


def load_baseline_values(config_py_path: Path) -> Dict[str, Any]:
    """Load the baseline config.py, instantiate its Config dataclass, return {field_name: value}."""
    spec = importlib.util.spec_from_file_location("baseline_cfg_mod", str(config_py_path))
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:
        print(f"[ABLATION] warning: could not exec baseline config.py: {e}", flush=True)
        return {}
    cls = None
    for name in ("Config", "QuickConfig", "TradingConfig"):
        if hasattr(mod, name) and is_dataclass(getattr(mod, name)):
            cls = getattr(mod, name); break
    if cls is None:
        for attr in dir(mod):
            v = getattr(mod, attr)
            if is_dataclass(v) and attr[:1].isupper():
                cls = v; break
    if cls is None:
        print(f"[ABLATION] ERROR: no dataclass found in {config_py_path}", flush=True)
        return {}
    try:
        inst = cls()
    except Exception as e:
        print(f"[ABLATION] ERROR: could not instantiate {cls.__name__}: {e}", flush=True)
        return {}
    out = {}
    for f in fields(inst):
        try:
            out[f.name] = getattr(inst, f.name)
        except Exception: pass
    return out


def filter_to_quickcfg_fields(baseline: Dict[str, Any]) -> Dict[str, Any]:
    """Only keep baseline fields that exist on QuickConfig — the rest can't affect v8_quick_engine."""
    qc = QuickConfig()
    qc_fields = {f.name for f in fields(qc)}
    return {k: v for k, v in baseline.items() if k in qc_fields}


def apply_overrides(base_cfg: QuickConfig, overrides: Dict[str, Any]) -> QuickConfig:
    cfg = copy.deepcopy(base_cfg)
    for k, v in overrides.items():
        if not hasattr(cfg, k):
            continue
        cur = getattr(cfg, k)
        try:
            if isinstance(cur, bool):
                setattr(cfg, k, bool(v))
            elif isinstance(cur, int):
                setattr(cfg, k, int(v))
            elif isinstance(cur, float):
                setattr(cfg, k, float(v))
            else:
                setattr(cfg, k, v)
        except Exception: pass
    return cfg


def variants_for(field_name: str, current_value: Any) -> List[Tuple[str, Any]]:
    """Return list of (label, variant_value) to test for this field."""
    if isinstance(current_value, bool):
        return [("flip", not current_value)]
    if isinstance(current_value, int):
        v = current_value
        if v == 0: return [("set_1", 1), ("set_5", 5)]
        if v > 0: return [("half", max(1, v // 2)), ("double", v * 2)]
        return [("half_abs", v // 2), ("double_abs", v * 2)]
    if isinstance(current_value, float):
        v = current_value
        if v == 0: return [("set_0.5", 0.5), ("set_2.0", 2.0)]
        if v > 0: return [("half", round(v * 0.5, 6)), ("double", round(v * 2.0, 6))]
        return [("half_abs", round(v * 0.5, 6)), ("double_abs", round(v * 2.0, 6))]
    return []


def run_one(stores, base_cfg: QuickConfig, overrides: Dict[str, Any], capital: float) -> Dict[str, Any]:
    cfg = apply_overrides(base_cfg, overrides)
    try:
        r = simulate(stores, cfg, capital)
    except Exception as e:
        r = {"error": f"{type(e).__name__}: {e}"}
    r["score"] = combined_score(r)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["crypto", "tradier"], required=True)
    ap.add_argument("--start", default="2022-01-01")
    ap.add_argument("--symbols", required=True)
    ap.add_argument("--baseline", required=True, help="Path to baseline config.py / config_tradier.py")
    ap.add_argument("--capital", type=float, default=10000.0)
    ap.add_argument("--npz-dir", default="")
    ap.add_argument("--min-delta-pct", type=float, default=10.0, help="Min |delta%%| to count as mover")
    ap.add_argument("--skip-fields", type=str, default="", help="Comma-sep fields to skip")
    args = ap.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    skip = set(s.strip() for s in args.skip_fields.split(",") if s.strip())

    out_dir = Path("data/orchestrator") / f"ablation_{args.mode}_{time.strftime('%Y%m%d_%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "run.log"
    def log(msg):
        print(msg, flush=True)
        try:
            with open(log_path, "a") as f: f.write(msg + "\n")
        except Exception: pass

    log(f"[ABLATION] out_dir={out_dir}")
    log(f"[ABLATION] mode={args.mode} baseline={args.baseline} symbols={len(symbols)}")

    # Load baseline values
    baseline_all = load_baseline_values(Path(args.baseline))
    baseline = filter_to_quickcfg_fields(baseline_all)
    log(f"[ABLATION] baseline has {len(baseline_all)} fields total, {len(baseline)} map to QuickConfig")
    with open(out_dir / "baseline.json", "w") as f:
        json.dump({k: (str(v) if not isinstance(v, (bool, int, float, str, list, dict, type(None))) else v) for k, v in baseline.items()}, f, indent=2, default=str)

    # Load NPZ data
    npz_dir = args.npz_dir
    if not npz_dir:
        for base in ("/home/niels/binance-sandbox", str(Path(__file__).resolve().parent)):
            for sub in ("backtest_v8/indicators", "backtest_v5/indicators_3m", "backtest_v5/indicators_5m_tradier"):
                d = Path(base) / sub
                if d.exists() and any(d.glob("*.npz")):
                    npz_dir = str(d); break
            if npz_dir: break
    log(f"[ABLATION] npz_dir={npz_dir}")
    t0 = time.time()
    stores = load_npz(args.mode, symbols, args.start, npz_dir)
    log(f"[ABLATION] loaded {len(stores)} symbols in {time.time()-t0:.1f}s")

    # Base config with tradier defaults if needed
    base_cfg = QuickConfig()
    if args.mode == "tradier":
        base_cfg.apply_tradier_defaults()

    # Run baseline
    log(f"[ABLATION] running baseline...")
    base_result = run_one(stores, base_cfg, baseline, args.capital)
    base_score = base_result["score"]
    with open(out_dir / "baseline_result.json", "w") as f:
        json.dump({k: v for k, v in base_result.items() if k != "overrides"}, f, indent=2, default=str)
    log(f"[ABLATION] baseline score={base_score:.4f} sharpe={base_result.get('sharpe',0):.3f} "
        f"gain={base_result.get('accumulated_gain_pct',0):.2f}% dd={base_result.get('max_dd_pct',0):.2f}% "
        f"trades={base_result.get('trades',0)}")

    if base_score <= 0:
        log(f"[ABLATION] WARNING: baseline score is 0 — variants will also tend to 0. Check baseline sensibleness.")

    # Ablation
    qc_instance = QuickConfig()
    qc_field_list = [f.name for f in fields(qc_instance) if f.name not in skip]

    all_rows = []
    movers = []
    t_abl_start = time.time()
    total_tests = 0
    for fi, fname in enumerate(qc_field_list, 1):
        current = baseline.get(fname, getattr(qc_instance, fname))
        vs = variants_for(fname, current)
        if not vs:
            continue
        for label, var_val in vs:
            total_tests += 1
            ovr = dict(baseline); ovr[fname] = var_val
            t_v = time.time()
            r = run_one(stores, base_cfg, ovr, args.capital)
            elapsed = time.time() - t_v
            new_score = r["score"]
            delta = new_score - base_score
            delta_pct = (delta / base_score * 100.0) if base_score > 0 else (float("inf") if new_score > 0 else 0.0)
            row = {
                "field": fname,
                "variant": label,
                "baseline_value": current,
                "variant_value": var_val,
                "baseline_score": round(base_score, 4),
                "new_score": round(new_score, 4),
                "delta": round(delta, 4),
                "delta_pct": round(delta_pct, 2),
                "pool_sharpe": round(float(r.get("pool_sharpe", 0) or 0), 4),
                "per_sym_sharpe": round(float(r.get("sharpe", 0) or 0), 4),
                "gain_pct": round(float(r.get("accumulated_gain_pct", 0) or 0), 4),
                "max_dd_pct": round(float(r.get("max_dd_pct", 0) or 0), 4),
                "trades": int(r.get("trades", 0) or 0),
                "symbols_used": int(r.get("symbols_used", 0) or 0),
                "elapsed_s": round(elapsed, 2),
            }
            all_rows.append(row)
            if abs(delta_pct) >= args.min_delta_pct:
                movers.append(row)
            if fi % 10 == 0 or fi == len(qc_field_list):
                log(f"  [{fi}/{len(qc_field_list)}] {fname} {label} {current}→{var_val}: "
                    f"score {base_score:.3f}→{new_score:.3f} Δ{delta_pct:+.1f}% "
                    f"(tot_tests={total_tests} elapsed={time.time()-t_abl_start:.0f}s)")

    # Write full CSV
    if all_rows:
        with open(out_dir / "full_ablation.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
            w.writeheader()
            for r in all_rows: w.writerow(r)

    # Movers sorted by |delta_pct|
    movers.sort(key=lambda r: abs(r["delta_pct"]), reverse=True)
    if movers:
        with open(out_dir / "movers.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(movers[0].keys()))
            w.writeheader()
            for r in movers: w.writerow(r)

    log(f"\n[ABLATION] DONE. {total_tests} variants tested in {time.time()-t_abl_start:.0f}s.")
    log(f"[ABLATION] {len(movers)} movers (|Δ| >= {args.min_delta_pct}%). Top 20:")
    for r in movers[:20]:
        sign = "+" if r["delta_pct"] > 0 else ""
        log(f"  {r['field']:<50} {r['variant']:<12} "
            f"{r['baseline_value']}→{r['variant_value']}  score={r['new_score']:.3f} ({sign}{r['delta_pct']:.1f}%)")
    log(f"[ABLATION] full output in {out_dir}")


if __name__ == "__main__":
    main()
