"""Parity test: scalar core == vec mask over >=10000 random samples, both sides."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from vec_decisions.check_exit_candidates_crypto__emergency_dc1h_breach import (
    check_emergency_dc1h_breach, check_emergency_dc1h_breach_vec, _emergency_dc1h_breach_fires)


class _Cfg:
    EMERGENCY_DC1H_BREACH_ENABLED = True
    EMERGENCY_DC1H_LOW_MULT = 0.997
    EMERGENCY_DC1H_HIGH_MULT = 1.003


def _run(seed, n, is_long, cfg):
    rng = np.random.default_rng(seed)
    price = rng.uniform(0.0, 200.0, n)
    lo = np.where(rng.random(n) < 0.1, 0.0, rng.uniform(0.0, 200.0, n))
    hi = np.where(rng.random(n) < 0.1, 0.0, rng.uniform(0.0, 200.0, n))
    vec = check_emergency_dc1h_breach_vec(cfg, price, lo, hi, is_long)
    mism = 0
    for i in range(n):
        ind = {"dc_low_1h": lo[i], "dc_high_1h": hi[i]}
        s_fires, _ = check_emergency_dc1h_breach(cfg, ind, float(price[i]), is_long)
        core = _emergency_dc1h_breach_fires(float(price[i]), float(lo[i]), float(hi[i]),
                                            is_long, cfg.EMERGENCY_DC1H_LOW_MULT, cfg.EMERGENCY_DC1H_HIGH_MULT)
        assert s_fires == core, f"scalar!=core@{i}"
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
    off = _Cfg(); off.EMERGENCY_DC1H_BREACH_ENABLED = False
    rng = np.random.default_rng(7)
    assert not check_emergency_dc1h_breach_vec(off, rng.uniform(0, 200, 5000),
                                               rng.uniform(0, 200, 5000), rng.uniform(0, 200, 5000), True).any()
    tn += 5000
    print(f"samples={tn} vec_fires={tf} mismatches={tm}")
    print("PARITY_PASS" if tm == 0 else "PARITY_FAIL")
    if tm: sys.exit(1)


if __name__ == "__main__":
    main()
