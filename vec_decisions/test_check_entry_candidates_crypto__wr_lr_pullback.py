"""PARITY test: scalar core == vectorized mask for WR/LR PULLBACK.
Run: /opt/anaconda3/envs/binance_env/bin/python vec_decisions/test_check_entry_candidates_crypto__wr_lr_pullback.py
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.check_entry_candidates_crypto__wr_lr_pullback import (  # noqa: E402
    _wr_lr_pullback_fires,
    check_wr_lr_pullback_vec,
)


def main(n=20000, seed=31337):
    rng = np.random.default_rng(seed)
    ha_choices = np.array(["red", "green", "gray", ""], dtype=object)
    wt_choices = np.array(["BULL", "BEAR", "NONE", ""], dtype=object)
    ha_4h = rng.choice(ha_choices, n)
    wt_cross_1m = rng.choice(wt_choices, n)
    k_1h = rng.uniform(0.0, 100.0, n)
    k_15m = rng.uniform(0.0, 100.0, n)
    k_3m = rng.uniform(0.0, 100.0, n)
    k_1m = rng.uniform(0.0, 100.0, n)
    k_1m_prev = rng.uniform(0.0, 100.0, n)
    total = 0
    mismatches = 0
    first_bad = None
    for is_long in (True, False):
        for in_list in (True, False):
            vec = check_wr_lr_pullback_vec(None, ha_4h, k_1h, k_15m, k_3m, k_1m,
                                           k_1m_prev, wt_cross_1m, in_list, is_long)
            for i in range(n):
                scalar = _wr_lr_pullback_fires(
                    str(ha_4h[i]), float(k_1h[i]), float(k_15m[i]), float(k_3m[i]),
                    float(k_1m[i]), float(k_1m_prev[i]), str(wt_cross_1m[i]),
                    in_list, is_long,
                )
                total += 1
                if scalar != bool(vec[i]):
                    mismatches += 1
                    if first_bad is None:
                        first_bad = (i, is_long, in_list, str(ha_4h[i]),
                                     str(wt_cross_1m[i]), scalar, bool(vec[i]))
    print(f"samples={total} mismatches={mismatches}")
    if first_bad is not None:
        print("FIRST MISMATCH:", first_bad)
    if mismatches == 0:
        print("PARITY_OK scalar==vec")
        return 0
    print("PARITY_FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
