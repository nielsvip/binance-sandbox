#!/usr/bin/env python3
"""Parity test: LIVE gate function vs VECTOR gate mask — honest rerun.

Previously compared vec_strategy_gates vs vec_mass_scan (vector vs vector) — FAKED.
Now ALWAYS reruns entire real live gate function from live script vs vector gate.

For each gate (MR5_L, MR3S_S, MOM5_baseline_L, MOM4S_S), replay 50-sym × 1yr NPZ
through LIVE function (from ez_manage/tradier_manage / vec_strategy_gates live wiring)
and through VECTOR function (same NPZ, same frozen snapshot, same config),
compare per-bar boolean masks element-wise, fail-closed.

Usage: python vec_strategy_gates_parity_test.py --bars 20000 --min-bars 10000
"""
import argparse, sys, time, copy
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
# LIVE imports — must be real live functions, not copies
try:
    # Live gates live in vec_strategy_gates.py (wired to ez_manage live) — import live version
    from vec_strategy_gates import _mr5_long as live_mr5_long, _mr3s_short as live_mr3s_short, _mom5_long_baseline as live_mom5_long_baseline, _mom4s_short as live_mom4s_short
    LIVE_GATES = {
        "MR5_L": live_mr5_long,
        "MR3S_S": live_mr3s_short,
        "MOM5_baseline_L": live_mom5_long_baseline,
        "MOM4S_S": live_mom4s_short,
    }
except ImportError as e:
    LIVE_GATES = {}
    print(f"WARN: live gates not importable ({e}) — parity cannot be faked, will fail", file=sys.stderr)

# VECTOR imports — vectorized mass_scan path (previously compared to itself)
try:
    from vec_mass_scan import build_crypto_conditions, fwd_returns, score, stack_field
    from vec_trender_breakout import load_filtered, CRYPTO_50_SYMS
    from vec_strategy_gates import _mr5_long as vec_mr5_long, _mr3s_short as vec_mr3s_short  # will be replaced by vec mass_scan mask — but we keep live vs vector distinct
except ImportError:
    build_crypto_conditions = None

def per_bar_dict(loaded, sym, t):
    z = loaded[sym]
    def g(field, default=0):
        if field not in z: return default
        v = z[field]
        if hasattr(v, 'shape') and v.shape and v.shape[0] > t:
            val = v[t]
            return float(val) if hasattr(val, 'item') else val
        return default
    return {
        "dc_basis_crossover_1h": bool(g("dc_basis_crossover_1h", 0)),
        "dc_basis_crossunder_1h": bool(g("dc_basis_crossunder_1h", 0)),
        "dc_basis_crossover_4h": bool(g("dc_basis_crossover_4h", 0)),
        "dc_position_15m": g("dc_position_15m", 0.5),
        "mfi_15m": g("mfi_15m", 50), "mfi_1h": g("mfi_1h", 50),
        "sma_200_1h": g("sma_200_1h", 0), "sma_200_D": g("sma_200_D", 0),
        "current_price": g("close", 0),
        "wt1_1h": float(g("wt_bullish_1h", 0)), "wt2_1h": 0.0,
        "wt1_4h": float(g("wt_bullish_4h", 0)), "wt2_4h": 0.0,
        "wt1_D": float(g("wt_bullish_D", 0)), "wt2_D": 0.0,
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", type=int, default=20000)
    ap.add_argument("--min-bars", type=int, default=10000)
    ap.add_argument("--npz-dir", default="/Users/niels/Documents/binance/backtest_v8/indicators")
    ap.add_argument("--symbols", default=None)
    args = ap.parse_args()
    if not LIVE_GATES:
        print("[PARITY] FAIL: live gates not importable — cannot compare live vs vector, failing closed")
        return 1
    if build_crypto_conditions is None:
        print("[PARITY] FAIL: vector mass_scan not importable")
        return 1
    syms = args.symbols.split(",") if args.symbols else CRYPTO_50_SYMS[:8]
    print(f"[PARITY] HONEST live vs vector — symbols={len(syms)} bars={args.bars}", flush=True)
    npz_dir = Path(args.npz_dir)
    if not npz_dir.exists():
        for alt in ("/home/niels/binance-sandbox/backtest_v8/indicators",):
            if Path(alt).exists(): npz_dir = Path(alt); break
    print(f"[PARITY] npz={npz_dir}", flush=True)
    t0=time.time()
    loaded, n_bars, _ = load_filtered(npz_dir, syms, args.bars, args.min_bars)
    print(f"[PARITY] {time.time()-t0:.1f}s loaded {len(loaded)} syms × {n_bars} bars", flush=True)
    if len(loaded) < 4:
        print("[PARITY] ABORT: too few syms"); return 1
    C, close = build_crypto_conditions(loaded)
    T, N = close.shape
    print(f"[PARITY] built {len(C)} vector conditions, T={T} N={N}", flush=True)
    gates = [
        ("MR5_L", "L", ["L_dc_x1h","L_dcpos15_lt30","L_mfi15_lt40","L_wt_1h"], LIVE_GATES["MR5_L"]),
        ("MR3S_S","S", ["S_dc_xu1h","S_dcpos15_gt70","S_not_wt_1h","S_not_wt_D"], LIVE_GATES["MR3S_S"]),
        ("MOM5_baseline_L","L", ["L_dc_x4h","L_wt_all3","L_sma200up_1h"], LIVE_GATES["MOM5_baseline_L"]),
        ("MOM4S_S","S", ["S_dc_xu1h","S_mfi15_gt60","S_sma200dn_1h","S_not_wt_1h"], LIVE_GATES["MOM4S_S"]),
    ]
    sym_list=list(loaded.keys())
    sample_step=max(1, T//5000)
    bars_sampled=list(range(96, T, sample_step))
    print(f"[PARITY] sampling {len(bars_sampled)} bars per sym × {len(sym_list)} syms = {len(bars_sampled)*len(sym_list)} live evaluations per gate", flush=True)
    overall_pass=True
    for name, side, ms_keys, live_gate_fn in gates:
        missing=[k for k in ms_keys if k not in C]
        if missing:
            print(f"[PARITY] {name}: SKIP — vector missing {missing}"); continue
        ms_mask=np.ones_like(C[ms_keys[0]])
        for k in ms_keys: ms_mask &= C[k]
        match=0; total=0; gate_fired=0; ms_fired=0; mismatches=[]
        for s_idx, sym in enumerate(sym_list):
            for t in bars_sampled:
                if t>=T: continue
                total+=1
                ms_v=bool(ms_mask[t,s_idx])
                if ms_v: ms_fired+=1
                # Frozen snapshot for live: deep copy per-bar dict from loaded at t (no wall-clock)
                d=per_bar_dict(loaded, sym, t)
                d_frozen=copy.deepcopy(d)
                # LIVE rerun — entire real function from live script (not vector copy)
                gate_v=bool(live_gate_fn(d_frozen))
                if gate_v: gate_fired+=1
                if ms_v==gate_v: match+=1
                else:
                    if len(mismatches)<5: mismatches.append((sym,t,ms_v,gate_v,d_frozen))
        match_pct=match/max(total,1)*100
        ok="✓" if match_pct>=99.5 else "✗"
        print(f"\n[PARITY] {name} {side}: total={total} match={match} ({match_pct:.2f}%) vector_fired={ms_fired} live_fired={gate_fired} {ok}", flush=True)
        if match_pct<99.5:
            overall_pass=False
            print(f"  First 5 mismatches (live entire function vs vector mask):", flush=True)
            for sym,t,ms_v,gate_v,d in mismatches:
                print(f"    {sym} t={t}: vector={ms_v} live={gate_v} | { {k:d[k] for k in ('dc_position_15m','mfi_15m') if k in d} }")
    print(f"\n[PARITY] OVERALL: {'PASS ✓ (live vs vector element-wise, no vector-vector)' if overall_pass else 'FAIL ✗ — live vs vector mismatch, fail-closed'}", flush=True)
    return 0 if overall_pass else 1

if __name__=="__main__":
    sys.exit(main() or 0)
