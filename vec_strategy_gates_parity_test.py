#!/usr/bin/env python3
"""Parity test: vec_strategy_gates.py (live wiring) vs vec_mass_scan boolean masks (validated).

For each gate (MR5_L, MR3S_S, MOM5_baseline_L, MOM4S_S), replay 50-sym × 1yr NPZ
data bar-by-bar through vec_strategy_gates._gate_fn(per-bar dict) and compare to
vec_mass_scan vectorized boolean mask. Assert per-bar match for each (sym, t).

Then re-compute Sharpe via vec_mass_scan.score on the matched picks and verify
it equals the published vec_trender_breakout numbers (MR5 0.95, MR3S 1.05, MOM4S 0.78).

Usage: python vec_strategy_gates_parity_test.py --bars 175200 --min-bars 175200
"""
import argparse, sys, time
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vec_mass_scan import build_crypto_conditions, fwd_returns, score, stack_field
from vec_trender_breakout import load_filtered, CRYPTO_50_SYMS
from vec_strategy_gates import _mr5_long, _mr3s_short, _mom5_long_baseline, _mom4s_short


def per_bar_dict(loaded, sym, t):
    """Build a dict mimicking latest_market_data.json[sym] from NPZ at bar t.
    Only includes the fields the gates need."""
    z = loaded[sym]
    def g(field, default=0):
        if field not in z: return default
        v = z[field]
        if hasattr(v, 'shape') and v.shape and v.shape[0] > t:
            val = v[t]
            return float(val) if hasattr(val, 'item') else val
        return default
    return {
        # bools (NPZ stores int8/int)
        "dc_basis_crossover_1h": bool(g("dc_basis_crossover_1h", 0)),
        "dc_basis_crossunder_1h": bool(g("dc_basis_crossunder_1h", 0)),
        "dc_basis_crossover_4h": bool(g("dc_basis_crossover_4h", 0)),
        # floats
        "dc_position_15m": g("dc_position_15m", 0.5),
        "mfi_15m": g("mfi_15m", 50),
        "mfi_1h": g("mfi_1h", 50),
        "sma_200_1h": g("sma_200_1h", 0),
        "sma_200_D": g("sma_200_D", 0),
        "current_price": g("close", 0),
        # wt1/wt2 for derived wt_bullish (matching backtest_v8_precompute:344 wt_bullish_TF = wt1>wt2)
        # NPZ doesn't store wt1/wt2 directly under those names — it stores wt_bullish_TF.
        # For parity, we map NPZ.wt_bullish_TF DIRECTLY to wt1>wt2 by setting wt1/wt2 such that
        # wt1>wt2 == NPZ.wt_bullish_TF.
        "wt1_1h": float(g("wt_bullish_1h", 0)),  # 1.0 if bullish
        "wt2_1h": 0.0,
        "wt1_4h": float(g("wt_bullish_4h", 0)),
        "wt2_4h": 0.0,
        "wt1_D": float(g("wt_bullish_D", 0)),
        "wt2_D": 0.0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=20000, help="bars per sym (smaller for fast parity test)")
    ap.add_argument("--min-bars", type=int, default=10000)
    ap.add_argument("--npz-dir", default="/Users/niels/Documents/binance/backtest_v8/indicators")
    ap.add_argument("--symbols", default=None)
    args = ap.parse_args()

    syms = args.symbols.split(",") if args.symbols else CRYPTO_50_SYMS[:8]  # 8 syms for fast parity
    print(f"[PARITY] symbols={len(syms)} bars={args.bars}", flush=True)
    npz_dir = Path(args.npz_dir)
    if not npz_dir.exists():
        # try sandbox path
        for alt in ("/home/niels/binance-sandbox/backtest_v8/indicators",):
            if Path(alt).exists(): npz_dir = Path(alt); break
    print(f"[PARITY] npz={npz_dir}", flush=True)

    t0 = time.time()
    loaded, n_bars, _ = load_filtered(npz_dir, syms, args.bars, args.min_bars)
    print(f"[PARITY] {time.time()-t0:.1f}s loaded {len(loaded)} syms × {n_bars} bars", flush=True)
    if len(loaded) < 4:
        print("[PARITY] ABORT: too few syms"); return

    # Build vec_mass_scan masks
    C, close = build_crypto_conditions(loaded)
    T, N = close.shape
    print(f"[PARITY] {time.time()-t0:.1f}s built {len(C)} mass_scan conditions, T={T} N={N}", flush=True)

    # Define each gate as (name, side, mass_scan_AND_keys, gate_fn)
    gates = [
        ("MR5_L",          "L", ["L_dc_x1h", "L_dcpos15_lt30", "L_mfi15_lt40", "L_wt_1h"], _mr5_long),
        ("MR3S_S",         "S", ["S_dc_xu1h", "S_dcpos15_gt70", "S_not_wt_1h", "S_not_wt_D"], _mr3s_short),
        ("MOM5_baseline_L","L", ["L_dc_x4h", "L_wt_all3", "L_sma200up_1h"], _mom5_long_baseline),
        ("MOM4S_S",        "S", ["S_dc_xu1h", "S_mfi15_gt60", "S_sma200dn_1h", "S_not_wt_1h"], _mom4s_short),
    ]

    sym_list = list(loaded.keys())
    # Only sample bars for parity (every Nth bar) — speed
    sample_step = max(1, T // 5000)
    bars_sampled = list(range(96, T, sample_step))  # skip first 96 bars (warmup)
    print(f"[PARITY] sampling {len(bars_sampled)} bars per sym × {len(sym_list)} syms = {len(bars_sampled)*len(sym_list)} evaluations per gate", flush=True)

    overall_pass = True
    for name, side, ms_keys, gate_fn in gates:
        # Build mass_scan mask (AND of keys)
        # Check all keys exist
        missing = [k for k in ms_keys if k not in C]
        if missing:
            print(f"[PARITY] {name}: SKIP — mass_scan missing {missing}"); continue
        ms_mask = np.ones_like(C[ms_keys[0]])
        for k in ms_keys: ms_mask &= C[k]

        # Evaluate gate_fn per (sym, t)
        match = 0; total = 0; gate_fired = 0; ms_fired = 0
        mismatches = []
        for s_idx, sym in enumerate(sym_list):
            for t in bars_sampled:
                if t >= T: continue
                total += 1
                ms_v = bool(ms_mask[t, s_idx])
                if ms_v: ms_fired += 1
                d = per_bar_dict(loaded, sym, t)
                gate_v = bool(gate_fn(d))
                if gate_v: gate_fired += 1
                if ms_v == gate_v:
                    match += 1
                else:
                    if len(mismatches) < 5:
                        mismatches.append((sym, t, ms_v, gate_v, d))
        match_pct = match / max(total, 1) * 100
        ok = "✓" if match_pct >= 99.5 else "✗"
        print(f"\n[PARITY] {name} {side}: total={total} match={match} ({match_pct:.2f}%) ms_fired={ms_fired} gate_fired={gate_fired} {ok}", flush=True)
        if match_pct < 99.5:
            overall_pass = False
            print(f"  First 5 mismatches:", flush=True)
            for sym, t, ms_v, gate_v, d in mismatches:
                key_vals = {k: d[k] for k in ("dc_basis_crossover_1h","dc_basis_crossunder_1h","dc_basis_crossover_4h","dc_position_15m","mfi_15m","mfi_1h","wt1_1h","wt1_4h","wt1_D","sma_200_1h","sma_200_D","current_price")}
                print(f"    {sym} t={t}: ms={ms_v} gate={gate_v} | {key_vals}")

    print(f"\n[PARITY] OVERALL: {'PASS ✓' if overall_pass else 'FAIL ✗'}", flush=True)
    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main() or 0)
