"""Parity test: scalar == vec for DELTA_HTF_GATE block predicate over >=10000 samples."""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.process_position_stocks__delta_htf_gate import _delta_htf_blocked, delta_htf_blocked_vec, _delta_htf_params


class _CfgBase:
    DELTA_HTF_GATE = "none"
    DELTA_ATR_ENTRY_FILTER = False


def _cfg(**kw):
    c = type("C", (_CfgBase,), {})()
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _run(cfg, n, rng):
    gate, atr_filter = _delta_htf_params(cfg)
    total = 0
    for is_long in (True, False):
        p = rng.uniform(50, 150, n)
        w14 = rng.uniform(-30, 30, n); w24 = rng.uniform(-30, 30, n)
        w1D = rng.uniform(-30, 30, n); w2D = rng.uniform(-30, 30, n)
        k4 = rng.uniform(0, 100, n); kD = rng.uniform(0, 100, n)
        a1 = rng.uniform(0, 5, n); a1[:100] = 0.0
        cp = p + rng.uniform(-3, 3, n)
        scalar = np.array([_delta_htf_blocked(float(p[i]), float(w14[i]), float(w24[i]), float(w1D[i]), float(w2D[i]), float(k4[i]), float(kD[i]), float(a1[i]), float(cp[i]), is_long, gate, atr_filter)[0] for i in range(n)], dtype=bool)
        vec = delta_htf_blocked_vec(cfg, p, w14, w24, w1D, w2D, k4, kD, a1, cp, is_long)
        m = int(np.sum(scalar != np.asarray(vec, dtype=bool))); total += m
        print(f"gate={gate} atr={atr_filter} is_long={is_long}: blocks={int(np.sum(vec))} mism={m}")
    return total


def run(n=12000, seed=29):
    rng = np.random.default_rng(seed)
    combos = [
        _cfg(),
        _cfg(DELTA_HTF_GATE="4h"),
        _cfg(DELTA_HTF_GATE="4h_D"),
        _cfg(DELTA_HTF_GATE="4h_D_strict"),
        _cfg(DELTA_ATR_ENTRY_FILTER=True),
        _cfg(DELTA_HTF_GATE="4h_D_strict", DELTA_ATR_ENTRY_FILTER=True),
    ]
    total = sum(_run(c, n, rng) for c in combos)
    print(f"TOTAL_MISMATCHES={total}")
    assert total == 0, f"PARITY FAIL: {total}"
    print("PARITY_OK")
    return total


if __name__ == "__main__":
    run()
