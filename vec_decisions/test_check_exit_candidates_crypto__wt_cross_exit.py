"""Parity test: scalar core == vec mask over >=10000 random samples, both sides."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from vec_decisions.check_exit_candidates_crypto__wt_cross_exit import (
    check_wt_cross_exit, check_wt_cross_exit_vec)


class _Cfg:
    WT_CROSS_EXIT_ENABLED = True
    WT_CROSS_EXIT_REQUIRE_15M_CONFIRM = True
    WT_CROSS_EXIT_3M_VETO_MAX_AGE = 30.0


def _run(seed, n, is_long, cfg):
    rng = np.random.default_rng(seed)
    # include zeros so the have_* guards (wt1!=0 or wt2!=0) are exercised
    def w(): return np.where(rng.random(n) < 0.12, 0.0, rng.uniform(-60, 60, n))
    a1, b1, a15, b15, a3, b3 = w(), w(), w(), w(), w(), w()
    age = rng.uniform(0, 60, n)
    vec = check_wt_cross_exit_vec(cfg, a1, b1, a15, b15, a3, b3, is_long, age)
    mism = 0
    for i in range(n):
        ind = {"wt1_1h": a1[i], "wt2_1h": b1[i], "wt1_15m": a15[i], "wt2_15m": b15[i],
               "wt1_3m": a3[i], "wt2_3m": b3[i]}
        s_fires, _ = check_wt_cross_exit(cfg, ind, is_long, float(age[i]))
        if bool(s_fires) != bool(vec[i]):
            mism += 1
    return mism, int(vec.sum())


def main():
    tm = tf = tn = 0
    for req15 in (True, False):
        cfg = _Cfg(); cfg.WT_CROSS_EXIT_REQUIRE_15M_CONFIRM = req15
        for is_long in (True, False):
            for seed in range(2):
                m, f = _run(seed, 3000, is_long, cfg)
                tm += m; tf += f; tn += 3000
    off = _Cfg(); off.WT_CROSS_EXIT_ENABLED = False
    rng = np.random.default_rng(7)
    z = rng.uniform(-60, 60, 5000)
    assert not check_wt_cross_exit_vec(off, z, z, z, z, z, z, True, rng.uniform(0, 60, 5000)).any()
    tn += 5000
    print(f"samples={tn} vec_fires={tf} mismatches={tm}")
    print("PARITY_PASS" if tm == 0 else "PARITY_FAIL")
    if tm: sys.exit(1)


if __name__ == "__main__":
    main()
