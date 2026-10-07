"""Parity test: scalar == vec for WT_3M_FORCE_OPEN over >=10000 samples."""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.process_position_stocks__wt_3m_force_open import _wt_3m_force_open_fires, check_wt_3m_force_open_vec


class _Cfg:
    WT_3M_FORCE_OPEN_ENABLED = True


def run(n=20000, seed=11):
    rng = np.random.default_rng(seed)
    cfg = _Cfg()
    total = 0
    for is_long in (True, False):
        w1 = rng.uniform(-50, 50, n)
        w2 = rng.uniform(-50, 50, n)
        w1[:50] = w2[:50]  # exact-equality edge
        scalar = np.array([_wt_3m_force_open_fires(float(w1[i]), float(w2[i]), is_long) for i in range(n)], dtype=bool)
        vec = check_wt_3m_force_open_vec(cfg, w1, w2, is_long)
        mism = int(np.sum(scalar != np.asarray(vec, dtype=bool)))
        total += mism
        print(f"is_long={is_long}: n={n} fires={int(np.sum(vec))} mismatches={mism}")
    print(f"TOTAL_MISMATCHES={total}")
    assert total == 0, f"PARITY FAIL: {total}"
    print("PARITY_OK")
    return total


if __name__ == "__main__":
    run()
