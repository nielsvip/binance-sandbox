"""Parity test: scalar core == vec mask over >=10000 random samples, both sides."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from vec_decisions.check_exit_candidates_crypto__structural_range_shift import (
    check_structural_range_shift, check_structural_range_shift_vec, _srs_params)


class _Cfg:
    STRUCTURAL_RANGE_SHIFT_EXIT = True
    STRUCTURAL_RANGE_SHIFT_TF = "dc_4h"
    STRUCTURAL_RANGE_SHIFT_K_HIGH = 75.0
    STRUCTURAL_RANGE_SHIFT_K_LOW = 25.0
    STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS = 100.0


def _run(seed, n, is_long, cfg):
    rng = np.random.default_rng(seed)
    _tf, hi_key, lo_key, _kh, _kl, _band = _srs_params(cfg)
    # Bias toward the fire region so the parity assertion is exercised on TRUE fires,
    # not just on the trivial all-False case: tight prox band, K near extremes, entry
    # past the level. ~100bps band means price must be within ~1% of the level.
    sh = rng.uniform(95, 105, n)
    sl = rng.uniform(95, 105, n)
    price = (sh if is_long else sl) * (1 + rng.uniform(-0.015, 0.015, n))
    ep = np.where(rng.random(n) < 0.1, 0.0,
                  (sh if is_long else sl) * (1 + rng.uniform(-0.02, 0.05, n)))
    k1h = rng.uniform(60, 100, n) if is_long else rng.uniform(0, 40, n)
    k1hp = rng.uniform(0, 100, n)
    k15 = rng.uniform(60, 100, n) if is_long else rng.uniform(0, 40, n)
    k15p = rng.uniform(0, 100, n)
    def w(): return rng.uniform(-60, 60, n)
    a1, b1, a15, b15, a3, b3 = w(), w(), w(), w(), w(), w()
    vec = check_structural_range_shift_vec(cfg, price, sh, sl, k1h, k1hp, k15, k15p,
                                           a1, b1, a15, b15, a3, b3, is_long, ep)
    mism = 0
    for i in range(n):
        ind = {hi_key: sh[i], lo_key: sl[i], "stoch_k_1h": k1h[i], "stoch_k_1h_prev": k1hp[i],
               "stoch_k_15m": k15[i], "stoch_k_15m_prev": k15p[i],
               "wt1_1h": a1[i], "wt2_1h": b1[i], "wt1_15m": a15[i], "wt2_15m": b15[i],
               "wt1_3m": a3[i], "wt2_3m": b3[i]}
        s_fires, _ = check_structural_range_shift(cfg, ind, float(price[i]), is_long, float(ep[i]))
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
    off = _Cfg(); off.STRUCTURAL_RANGE_SHIFT_EXIT = False
    rng = np.random.default_rng(7)
    z = rng.uniform(50, 150, 5000)
    assert not check_structural_range_shift_vec(off, z, z, z, z, z, z, z, z, z, z, z, z, z, True, z).any()
    tn += 5000
    print(f"samples={tn} vec_fires={tf} mismatches={tm}")
    print("PARITY_PASS" if tm == 0 else "PARITY_FAIL")
    if tm: sys.exit(1)


if __name__ == "__main__":
    main()
