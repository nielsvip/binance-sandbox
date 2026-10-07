"""Parity test: scalar == vec for RULE_A_RETEST over >=10000 samples."""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.process_position_stocks__rule_a_retest import _rule_a_retest_fires, check_rule_a_retest_vec, _rule_a_retest_mult


class _Cfg:
    BREAKOUT_RETEST_ARMED_ENABLED = True
    BREAKOUT_RETEST_ARMED_RETEST_ATR_MULT = 0.30


def run(n=20000, seed=13):
    rng = np.random.default_rng(seed)
    cfg = _Cfg()
    mult = _rule_a_retest_mult(cfg)
    total = 0
    for is_long in (True, False):
        p = rng.uniform(50, 150, n)
        w1D = rng.uniform(-30, 30, n)
        w2D = rng.uniform(-30, 30, n)
        w1D[:100] = 0.0  # data-ok guard edge (|wt1_D|<1e-9)
        w1W = rng.uniform(-30, 30, n)
        w2W = rng.uniform(-30, 30, n)
        dcD = rng.uniform(0, 150, n)
        dcD[100:200] = 0.0  # data-ok guard edge
        aD = rng.uniform(0, 8, n)
        aD[200:300] = 0.0  # data-ok guard edge (atr_D<=0)
        w115 = rng.uniform(-30, 30, n)
        w215 = rng.uniform(-30, 30, n)
        w11h = rng.uniform(-30, 30, n)
        w21h = rng.uniform(-30, 30, n)
        k3 = rng.uniform(0, 100, n)
        d3 = rng.uniform(0, 100, n)
        k3p = rng.uniform(0, 100, n)
        scalar = np.array([_rule_a_retest_fires(float(p[i]), float(w1D[i]), float(w2D[i]), float(w1W[i]), float(w2W[i]), float(dcD[i]), float(aD[i]), float(w115[i]), float(w215[i]), float(w11h[i]), float(w21h[i]), float(k3[i]), float(d3[i]), float(k3p[i]), is_long, mult) for i in range(n)], dtype=bool)
        vec = check_rule_a_retest_vec(cfg, p, w1D, w2D, w1W, w2W, dcD, aD, w115, w215, w11h, w21h, k3, d3, k3p, is_long)
        mism = int(np.sum(scalar != np.asarray(vec, dtype=bool)))
        total += mism
        print(f"is_long={is_long}: n={n} fires={int(np.sum(vec))} mismatches={mism}")
    print(f"TOTAL_MISMATCHES={total}")
    assert total == 0, f"PARITY FAIL: {total}"
    print("PARITY_OK")
    return total


if __name__ == "__main__":
    run()
