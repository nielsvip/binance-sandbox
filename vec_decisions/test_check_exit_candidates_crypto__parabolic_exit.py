"""Parity test: scalar core == vec mask over >=10000 random samples, both sides."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from vec_decisions.check_exit_candidates_crypto__parabolic_exit import (
    check_parabolic_exit, check_parabolic_exit_vec)


class _Cfg:
    PARABOLIC_K15M_HIGH = 90.0
    PARABOLIC_K15M_LOW = 10.0


def _run(seed, n, is_long, cfg):
    rng = np.random.default_rng(seed)
    k15 = rng.uniform(0, 100, n)
    dch = np.where(rng.random(n) < 0.1, 0.0, rng.uniform(0, 200, n))
    dcl = np.where(rng.random(n) < 0.1, 0.0, rng.uniform(0, 200, n))
    price = rng.uniform(0, 200, n)
    lo = np.where(rng.random(n) < 0.1, 0.0, rng.uniform(0, 200, n))
    lop = np.where(rng.random(n) < 0.1, 0.0, rng.uniform(0, 200, n))
    hi = np.where(rng.random(n) < 0.1, 0.0, rng.uniform(0, 200, n))
    hip = np.where(rng.random(n) < 0.1, 0.0, rng.uniform(0, 200, n))
    nodecel = rng.random(n) < 0.7
    vec = check_parabolic_exit_vec(cfg, k15, dch, dcl, price, lo, lop, hi, hip, is_long, nodecel)
    mism = 0
    for i in range(n):
        ind = {"stoch_k_15m": k15[i], "dc_high_3m": dch[i], "dc_low_3m": dcl[i],
               "low_3m": lo[i], "low_3m_prev": lop[i], "high_3m": hi[i], "high_3m_prev": hip[i]}
        s_fires, _ = check_parabolic_exit(cfg, ind, float(price[i]), is_long, bool(nodecel[i]))
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
