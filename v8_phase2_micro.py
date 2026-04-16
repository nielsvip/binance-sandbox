#!/usr/bin/env python3
"""
v8_phase2_micro.py — Seeded micro-sweeps from Phase 1 winners.

Seeds (each tested with ±variations):
  S1: S5_H3_HD12 (Sharpe 1.51, 167 trades)
  S2: NO_B11 + S5 + H1 + HD10 (Sharpe 1.49, 157 trades)
  S3: S4_H3_HD12 big-PnL (Sharpe 1.37, 408 trades — MORE DATA)
  S4: PT=1.6 + TOP3 (Sharpe 1.93, 58 trades — PEAK)
  S5: TOP5 ETH+DOT+BTC+UNI+ADA (Sharpe 1.73, 90 trades — no LINK)
  S6: LINK+DOT+ADA (Sharpe 1.71, 42 trades)

Each seed gets 10-20 variations probing nearby dimensions.
"""
import sys, time, json, itertools
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
sys.path.insert(0, str(Path(__file__).resolve().parent))
from v8_quick_engine import QuickConfig, load_npz, simulate, FAST_SYMBOLS_CRYPTO

BASE = Path(__file__).resolve().parent
ALL11 = FAST_SYMBOLS_CRYPTO.split(",")
TOP3 = ["LINKUSDT","ETHUSDT","DOTUSDT"]
NEW_TOP5 = ["ETHUSDT","DOTUSDT","BTCUSDT","UNIUSDT","ADAUSDT"]
LINK_DOT_ADA = ["LINKUSDT","DOTUSDT","ADAUSDT"]


def seed_cfg():
    c = QuickConfig()
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
    label, overrides, symbols = args
    stores = load_npz('crypto', symbols, '2022-01-01')
    if not stores: return {"label": label, "sharpe": 0, "trades": 0}
    cfg = seed_cfg()
    for k, v in overrides.items():
        if hasattr(cfg, k):
            cur = getattr(cfg, k)
            if isinstance(cur, bool): setattr(cfg, k, bool(v))
            elif isinstance(cur, int): setattr(cfg, k, int(v))
            elif isinstance(cur, float): setattr(cfg, k, float(v))
            else: setattr(cfg, k, v)
    t0 = time.time()
    r = simulate(stores, cfg, 10000.0)
    r["label"] = label; r["cfg"] = overrides; r["n_symbols"] = len(symbols)
    r["symbols_list"] = ",".join(symbols)
    r["elapsed"] = round(time.time() - t0, 1)
    return r


def build_experiments():
    exps = []

    # SEED 1: HTF=3 + HD12 — probe score + PT + HTF
    for score in [3.5, 4.0, 4.5, 5.0, 5.5]:
        for pt in [1.3, 1.5, 1.6, 1.7, 1.8]:
            for hold in [10, 12, 15, 18]:
                exps.append((f"S1_S{score}_PT{pt}_HD{hold}", {"STRENGTH_MIN_SCORE": score, "PROFIT_TARGET_PCT": pt, "HTF_MIN_ALIGNED": 3, "MIN_HOLD_BARS": hold}, ALL11))

    # SEED 2: NO_B11 block (DC_BREAK off)
    for pt in [1.4, 1.6, 1.8]:
        for hold in [10, 12, 15]:
            for htf in [1, 2, 3]:
                exps.append((f"S2_NO_B11_PT{pt}_HD{hold}_H{htf}", {"REENTRY_B11_DC_BREAK_ENABLED": False, "PROFIT_TARGET_PCT": pt, "MIN_HOLD_BARS": hold, "HTF_MIN_ALIGNED": htf}, ALL11))

    # SEED 3: Score=4 + HTF=3 + HD12 big-PnL → push with PT + block combinations
    for pt in [1.3, 1.5, 1.7, 2.0]:
        for sl_enable, sl in [(False, 0), (True, 0.5), (True, 1.0)]:
            cfg = {"STRENGTH_MIN_SCORE": 4.0, "HTF_MIN_ALIGNED": 3, "MIN_HOLD_BARS": 12, "PROFIT_TARGET_PCT": pt}
            if sl_enable: cfg["STOP_LOSS_ENABLED"] = True; cfg["STOP_LOSS_PCT"] = sl
            exps.append((f"S3_S4_H3_HD12_PT{pt}_SL{sl if sl_enable else 'off'}", cfg, ALL11))

    # SEED 4: TOP3 + PT refined (try even narrower PT range around 1.6)
    for pt in [1.4, 1.5, 1.55, 1.6, 1.65, 1.7, 1.75]:
        for hold in [8, 10, 12, 15]:
            for htf in [1, 2, 3]:
                exps.append((f"S4_TOP3_PT{pt}_HD{hold}_H{htf}", {"PROFIT_TARGET_PCT": pt, "MIN_HOLD_BARS": hold, "HTF_MIN_ALIGNED": htf}, TOP3))

    # SEED 5: NEW_TOP5 (ETH+DOT+BTC+UNI+ADA) — probe
    for pt in [1.3, 1.5, 1.6, 1.7, 2.0]:
        for hold in [10, 12, 15]:
            exps.append((f"S5_NEWTOP5_PT{pt}_HD{hold}", {"PROFIT_TARGET_PCT": pt, "MIN_HOLD_BARS": hold}, NEW_TOP5))

    # SEED 6: LINK+DOT+ADA — probe
    for pt in [1.3, 1.5, 1.7]:
        for hold in [10, 12, 15, 20]:
            for htf in [1, 2, 3]:
                exps.append((f"S6_LDA_PT{pt}_HD{hold}_H{htf}", {"PROFIT_TARGET_PCT": pt, "MIN_HOLD_BARS": hold, "HTF_MIN_ALIGNED": htf}, LINK_DOT_ADA))

    # SEED 7 — NEW IDEA: combine P1C and P1D wins — HTF=3 + HD12 + NO_B11
    for pt in [1.4, 1.6, 1.8]:
        for score in [4.0, 4.5, 5.0]:
            exps.append((f"S7_S{score}_H3_HD12_NO_B11_PT{pt}", {"STRENGTH_MIN_SCORE": score, "HTF_MIN_ALIGNED": 3, "MIN_HOLD_BARS": 12, "REENTRY_B11_DC_BREAK_ENABLED": False, "PROFIT_TARGET_PCT": pt}, ALL11))

    # SEED 8 — on NEW_TOP5 with refined settings from S1/S3/S7
    for cfg_name, cfg_over in [
        ("S8_NT5_S5_H3_HD12", {"HTF_MIN_ALIGNED": 3, "MIN_HOLD_BARS": 12, "PROFIT_TARGET_PCT": 1.6}),
        ("S8_NT5_S5_H3_HD12_NO_B11", {"HTF_MIN_ALIGNED": 3, "MIN_HOLD_BARS": 12, "PROFIT_TARGET_PCT": 1.6, "REENTRY_B11_DC_BREAK_ENABLED": False}),
        ("S8_NT5_S4_H3_HD12", {"STRENGTH_MIN_SCORE": 4.0, "HTF_MIN_ALIGNED": 3, "MIN_HOLD_BARS": 12, "PROFIT_TARGET_PCT": 1.6}),
    ]:
        exps.append((cfg_name, cfg_over, NEW_TOP5))

    # SEED 9 — 6/7-symbol subsets extending NEW_TOP5
    for extra in ["LINKUSDT", "BNBUSDT", "SOLUSDT", "LTCUSDT"]:
        exps.append((f"S9_NT5_plus_{extra[:4]}", {"HTF_MIN_ALIGNED": 3, "MIN_HOLD_BARS": 12}, NEW_TOP5 + [extra]))

    return exps


def main():
    exps = build_experiments()
    print(f"Phase 2: {len(exps)} seeded experiments, 6 workers...")
    t0 = time.time()
    results = []
    with ProcessPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(run_exp, e): e for e in exps}
        for fut in as_completed(futs):
            try:
                r = fut.result()
                results.append(r)
                if r.get("sharpe", 0) > 1.7:
                    print(f"  🎯 {r['label']:<38} Sharpe={r['sharpe']:.4f} trades={r['trades']:>4} wr={r.get('wr',0):.1f}% avg={r.get('avg_pnl_pct',0):.3f}%")
            except Exception as e:
                print(f"  ERROR: {e}")
    elapsed = time.time() - t0
    print(f"\nPhase 2 done in {elapsed:.0f}s ({len(results)} results)")

    # TOP 30 by Sharpe, with trades>=30 for statistical validity
    valid = sorted([r for r in results if r.get("trades", 0) >= 30], key=lambda r: r["sharpe"], reverse=True)
    print(f"\n{'='*120}")
    print(f"  TOP 30 (trades>=30):")
    print(f"{'='*120}")
    for r in valid[:30]:
        print(f"  {r['label']:<40} Sharpe={r['sharpe']:.4f} trades={r['trades']:>4} wr={r.get('wr',0):.1f}% avg={r.get('avg_pnl_pct',0):.3f}% pnl=${r.get('pnl',0):.0f}")

    # TOP by PnL (for high-frequency strategies)
    pnl_sorted = sorted([r for r in results if r.get("trades", 0) >= 100 and r.get("sharpe", 0) > 1.0], key=lambda r: r.get("pnl", 0), reverse=True)
    print(f"\n  TOP 15 by PnL (trades>=100, Sharpe>1.0) — high-frequency mode:")
    for r in pnl_sorted[:15]:
        print(f"  {r['label']:<40} Sharpe={r['sharpe']:.4f} trades={r['trades']:>4} pnl=${r.get('pnl',0):.0f} avg={r.get('avg_pnl_pct',0):.3f}%")

    out_path = BASE / "data" / "sweep_results" / f"v8_phase2_micro_{int(time.time())}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
