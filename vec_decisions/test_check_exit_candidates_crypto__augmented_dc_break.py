"""Parity test: scalar core == vec mask over >=10000 random samples, both sides."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from vec_decisions.check_exit_candidates_crypto__augmented_dc_break import (
    check_augmented_dc_break, check_augmented_dc_break_vec)


class _Cfg:
    AUGMENTED_DC_BREAK_MIN_GAIN = 0.1


def _run(seed, n, is_long, cfg):
    rng = np.random.default_rng(seed)
    price = rng.uniform(0.0, 200.0, n)
    lo = rng.uniform(0.0, 200.0, n)
    hi = rng.uniform(0.0, 200.0, n)
    aug = rng.random(n) < 0.5
    gain = rng.uniform(-2, 5, n)
    hedged = rng.random(n) < 0.3
    vec = check_augmented_dc_break_vec(cfg, price, lo, hi, is_long, aug, gain, hedged)
    mism = 0
    for i in range(n):
        ind = {"dc_low_3m": lo[i], "dc_high_3m": hi[i]}
        s_fires, _ = check_augmented_dc_break(cfg, ind, float(price[i]), is_long,
                                              bool(aug[i]), float(gain[i]), bool(hedged[i]))
        if bool(s_fires) != bool(vec[i]):
            mism += 1
    return mism, int(vec.sum())


def main():
    cfg = _Cfg()
    tm = tf = tn = 0
    for is_long in (True, False):
        for seed in range(3):
            m, f = _run(seed, 6000, is_long, cfg)
            tm += m; tf += f; tn += 6000
    print(f"samples={tn} vec_fires={tf} mismatches={tm}")
    print("PARITY_PASS" if tm == 0 else "PARITY_FAIL")
    if tm: sys.exit(1)


if __name__ == "__main__":
    main()
