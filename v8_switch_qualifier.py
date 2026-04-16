#!/usr/bin/env python3
"""
v8_switch_qualifier.py — Qualify each switch 0-100 by Sharpe sensitivity.

For each HOOKED + TESTABLE switch in config_tradier.py (or config.py for crypto):
  1. Run baseline (current live defaults)
  2. Flip the switch (bool) or test ±20% (numeric)
  3. Measure ΔSharpe vs baseline
  4. Score 0-100 based on |ΔSharpe|

Outputs: ranked JSON + markdown report grouped by impact tier.

Output: data/sweep_alerts/switch_qualification_<mode>_<timestamp>.json
"""
import argparse, json, os, sys, time
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
sys.path.insert(0, str(Path(__file__).resolve().parent))
from v8_quick_engine import QuickConfig, load_npz, simulate, FAST_SYMBOLS_CRYPTO, FAST_SYMBOLS_TRADIER

BASE = Path(__file__).resolve().parent


def load_testable(path='/tmp/tradier_testable.txt'):
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line: continue
            parts = line.split('|')
            if len(parts) == 3:
                out.append({"name": parts[0], "type": parts[1], "default": parts[2]})
    return out


def baseline_cfg(mode):
    c = QuickConfig()
    if mode == "tradier":
        c.apply_tradier_defaults()
    # Locked-true proven
    c.STRENGTH_FILTER_ENABLED = True
    c.STRENGTH_MIN_SCORE = 5.0
    c.CT_WT_VELOCITY_GATE_ENABLED = True
    c.CT_DC_CROSSOVER_SKIP_ENABLED = True
    c.CT_15M_MOMENTUM_GATE_ENABLED = False
    c.CT_CHOP_4H_GATE_ENABLED = False
    c.CT_VOLUME_SURGE_GATE_ENABLED = False
    c.PROFIT_TARGET_ENABLED = True
    c.PROFIT_TARGET_PCT = 1.6
    c.HTF_MIN_ALIGNED = 1
    c.D_TREND_REQUIRED = True
    c.MIN_HOLD_BARS = 10
    c.WT_EXIT_MIN_TFS = 2
    c.COOLDOWN_BARS = 3
    return c


def run_one(args):
    mode, symbols, cfg_dict, npz_dir, start = args
    stores = load_npz(mode, symbols, start, npz_dir)
    if not stores: return {"sharpe": 0, "trades": 0}
    cfg = baseline_cfg(mode)
    for k, v in cfg_dict.items():
        if hasattr(cfg, k):
            cur = getattr(cfg, k)
            if isinstance(cur, bool): setattr(cfg, k, bool(v))
            elif isinstance(cur, int): setattr(cfg, k, int(v))
            elif isinstance(cur, float): setattr(cfg, k, float(v))
            else: setattr(cfg, k, v)
    return simulate(stores, cfg, 10000.0)


def build_experiments(mode, switches):
    """Generate baseline + one perturbation per switch."""
    exps = [("BASELINE", {})]  # empty = use baseline_cfg defaults
    for sw in switches:
        name, ty, dflt = sw["name"], sw["type"], sw["default"]
        # Only test switches that QuickConfig knows about (has to be reachable in engine)
        cfg_base = baseline_cfg(mode)
        if not hasattr(cfg_base, name):
            continue  # not in engine, skip
        try:
            if ty == "bool":
                current = dflt.strip().lower() in ('true', '1', 'yes')
                exps.append((f"{name}=NOT_{current}", {name: not current}))
            elif ty == "int":
                v = int(float(dflt.rstrip(',')))
                if v != 0:
                    exps.append((f"{name}={int(v*1.25)}", {name: max(int(v*1.25), v+1)}))
                else:
                    exps.append((f"{name}=1", {name: 1}))
            elif ty == "float":
                v = float(dflt.rstrip(','))
                if v != 0:
                    exps.append((f"{name}={v*1.2:.3g}", {name: v * 1.2}))
                else:
                    exps.append((f"{name}=0.5", {name: 0.5}))
            # strings skipped — too many tradier-specific TF strings
        except Exception as e:
            continue
    return exps


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["crypto","tradier"], default="tradier")
    p.add_argument("--symbols", default="fast")
    p.add_argument("--start", default="")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--npz-dir", default="")
    p.add_argument("--switch-list", default="/tmp/tradier_testable.txt")
    args = p.parse_args()
    start = args.start or ("2022-01-01" if args.mode == "crypto" else "2024-01-01")
    syms = (FAST_SYMBOLS_TRADIER if args.mode == "tradier" else FAST_SYMBOLS_CRYPTO).split(",") if args.symbols == "fast" else [s.strip() for s in args.symbols.split(",")]

    switches = load_testable(args.switch_list)
    exps = build_experiments(args.mode, switches)
    print(f"Qualifying {len(exps)} configs (1 baseline + {len(exps)-1} perturbations) on {args.mode} ({len(syms)} syms, 6 workers)")

    todo = [(args.mode, syms, cfg_dict, args.npz_dir, start) for _, cfg_dict in exps]
    labels = [lbl for lbl, _ in exps]

    results = {}
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(run_one, t): i for i, t in enumerate(todo)}
        done = 0
        for fut in as_completed(futs):
            idx = futs[fut]
            try: r = fut.result()
            except Exception as e: r = {"sharpe": 0, "trades": 0, "error": str(e)}
            results[labels[idx]] = r
            done += 1
            if done % 25 == 0: print(f"  [{done}/{len(exps)}] so far...")

    baseline = results.get("BASELINE", {"sharpe": 0, "trades": 0})
    b_sharpe = baseline.get("sharpe", 0)
    b_trades = baseline.get("trades", 0)
    print(f"\nBASELINE: Sharpe={b_sharpe:.4f} trades={b_trades} wr={baseline.get('wr',0):.1f}%")

    # Compute qualification
    qualified = []
    for lbl, r in results.items():
        if lbl == "BASELINE": continue
        d_sharpe = r.get("sharpe", 0) - b_sharpe
        d_trades = r.get("trades", 0) - b_trades
        # Raw impact by |ΔSharpe|
        qualified.append({
            "label": lbl,
            "sharpe": r.get("sharpe", 0),
            "delta_sharpe": round(d_sharpe, 4),
            "trades": r.get("trades", 0),
            "delta_trades": d_trades,
            "wr": r.get("wr", 0),
            "avg_pnl_pct": r.get("avg_pnl_pct", 0),
        })
    # Max delta for normalization (lower-bound max at 0.1 so scores don't explode)
    max_abs_delta = max([abs(q["delta_sharpe"]) for q in qualified] + [0.1])
    for q in qualified:
        q["impact_score"] = round(abs(q["delta_sharpe"]) / max_abs_delta * 100, 1)
        q["direction"] = "+" if q["delta_sharpe"] > 0 else ("-" if q["delta_sharpe"] < 0 else "0")
    qualified.sort(key=lambda q: abs(q["delta_sharpe"]), reverse=True)

    # Tier breakdown
    tier_1 = [q for q in qualified if q["impact_score"] >= 50]
    tier_2 = [q for q in qualified if 15 <= q["impact_score"] < 50]
    tier_3 = [q for q in qualified if 3 <= q["impact_score"] < 15]
    noise = [q for q in qualified if q["impact_score"] < 3]

    print(f"\n{'='*90}")
    print(f"  SWITCH QUALIFICATION — {args.mode} baseline Sharpe {b_sharpe:.4f}")
    print(f"{'='*90}")
    print(f"\nTIER 1 ({len(tier_1)} switches, impact >= 50 — HIGHEST priority):")
    for q in tier_1[:30]:
        print(f"  {q['impact_score']:>5.1f} {q['direction']} ΔSharpe={q['delta_sharpe']:>+7.4f}  {q['label']}")
    print(f"\nTIER 2 ({len(tier_2)} switches, impact 15-50 — medium):")
    for q in tier_2[:20]:
        print(f"  {q['impact_score']:>5.1f} {q['direction']} ΔSharpe={q['delta_sharpe']:>+7.4f}  {q['label']}")
    print(f"\nTIER 3 ({len(tier_3)} switches, impact 3-15 — low):")
    for q in tier_3[:10]:
        print(f"  {q['impact_score']:>5.1f} {q['direction']} ΔSharpe={q['delta_sharpe']:>+7.4f}  {q['label']}")
    print(f"\nNOISE ({len(noise)} switches, impact < 3 — dead/redundant): names only")
    for q in noise[:15]:
        print(f"  {q['label']}")

    out = BASE / "data" / "sweep_alerts" / f"switch_qualification_{args.mode}_{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"mode": args.mode, "baseline": baseline, "qualified": qualified, "tier_1": tier_1, "tier_2": tier_2, "tier_3": tier_3, "noise_count": len(noise)}
    out.write_text(json.dumps(payload, indent=2, default=str))
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
