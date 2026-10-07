"""PARITY test: scalar core == vectorized mask for PULLBACK_AUGMENT_QUICK.
Run: /opt/anaconda3/envs/binance_env/bin/python vec_decisions/test_check_entry_candidates_crypto__pullback_augment.py
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.check_entry_candidates_crypto__pullback_augment import (  # noqa: E402
    _pullback_augment_fires,
    check_pullback_augment_vec,
)


def main(n=20000, seed=2024):
    rng = np.random.default_rng(seed)
    k_3m = rng.uniform(0.0, 100.0, n)
    has_position = rng.random(n) > 0.3
    gain_non_positive = rng.random(n) > 0.5
    is_small = rng.random(n) > 0.5
    total = 0
    mismatches = 0
    first_bad = None
    for is_long in (True, False):
        vec = check_pullback_augment_vec(None, k_3m, is_long, has_position,
                                         gain_non_positive, is_small)
        for i in range(n):
            scalar = _pullback_augment_fires(float(k_3m[i]), is_long,
                                             bool(has_position[i]),
                                             bool(gain_non_positive[i]),
                                             bool(is_small[i]))
            total += 1
            if scalar != bool(vec[i]):
                mismatches += 1
                if first_bad is None:
                    first_bad = (i, is_long, float(k_3m[i]), scalar, bool(vec[i]))
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
