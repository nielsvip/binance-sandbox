"""Parity test: scalar == vec for WT_DC_ENTRY gates (block predicate) over >=10000 samples.
Sweeps several config combos so every gate branch is exercised."""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.process_position_stocks__wtdc_entry_gates import _wtdc_entry_blocked, wtdc_entry_blocked_vec, _wtdc_entry_gate_params


class _CfgBase:
    WT_DC_HTF_GATE = "none"
    WT_DC_ENTRY_K5M_MAX_LONG = 100
    WT_DC_ENTRY_K5M_MIN_SHORT = 0
    HTF_ALIGN_REQUIRED_TRADIER = 0
    COMBINED_STOCH_GATE_TRADIER = 100.0
    WT_DC_ENTRY_BAR_MATURITY_BLOCK_ENABLED = False
    WT_DC_ENTRY_BAR_MATURITY_BLOCK = 0.7


def _cfg(**kw):
    c = type("C", (_CfgBase,), {})()
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _run(cfg, n, rng):
    gate, k5m_max, k5m_min, align_req, stoch_thr, bm_en, bm_thr = _wtdc_entry_gate_params(cfg)
    total = 0
    for is_long in (True, False):
        p = rng.uniform(50, 150, n)
        k5 = rng.uniform(0, 100, n)
        w11h = rng.uniform(-30, 30, n)
        w21h = rng.uniform(-30, 30, n)
        w14h = rng.uniform(-30, 30, n)
        w24h = rng.uniform(-30, 30, n)
        w1D = rng.uniform(-30, 30, n)
        w2D = rng.uniform(-30, 30, n)
        oD = rng.uniform(0, 150, n)
        oD[:100] = 0.0
        aD = rng.uniform(0, 8, n)
        aD[100:200] = 0.0
        scalar = np.array([_wtdc_entry_blocked(float(p[i]), float(k5[i]), float(w11h[i]), float(w21h[i]), float(w14h[i]), float(w24h[i]), float(w1D[i]), float(w2D[i]), float(oD[i]), float(aD[i]), is_long, gate, k5m_max, k5m_min, align_req, stoch_thr, bm_en, bm_thr)[0] for i in range(n)], dtype=bool)
        vec = wtdc_entry_blocked_vec(cfg, p, k5, w11h, w21h, w14h, w24h, w1D, w2D, oD, aD, is_long)
        mism = int(np.sum(scalar != np.asarray(vec, dtype=bool)))
        total += mism
        print(f"gate={gate} align={align_req} stoch={stoch_thr} bm={bm_en} is_long={is_long}: n={n} blocks={int(np.sum(vec))} mismatches={mism}")
    return total


def run(n=12000, seed=17):
    rng = np.random.default_rng(seed)
    combos = [
        _cfg(),
        _cfg(WT_DC_HTF_GATE="1h"),
        _cfg(WT_DC_HTF_GATE="4h"),
        _cfg(WT_DC_HTF_GATE="4h_d"),
        _cfg(WT_DC_ENTRY_K5M_MAX_LONG=80, WT_DC_ENTRY_K5M_MIN_SHORT=20),
        _cfg(HTF_ALIGN_REQUIRED_TRADIER=2),
        _cfg(COMBINED_STOCH_GATE_TRADIER=60.0),
        _cfg(WT_DC_ENTRY_BAR_MATURITY_BLOCK_ENABLED=True, WT_DC_ENTRY_BAR_MATURITY_BLOCK=0.7),
        _cfg(WT_DC_HTF_GATE="4h_d", HTF_ALIGN_REQUIRED_TRADIER=2, COMBINED_STOCH_GATE_TRADIER=60.0, WT_DC_ENTRY_BAR_MATURITY_BLOCK_ENABLED=True),
    ]
    total = sum(_run(c, n, rng) for c in combos)
    print(f"TOTAL_MISMATCHES={total}")
    assert total == 0, f"PARITY FAIL: {total}"
    print("PARITY_OK")
    return total


if __name__ == "__main__":
    run()
