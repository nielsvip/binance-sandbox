"""Parity test: scalar core == vec mask over >=10000 random samples, both sides."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from vec_decisions.check_exit_candidates_crypto__k1m_extreme_reverse import (
    check_k1m_extreme_reverse, check_k1m_extreme_reverse_vec, _k1m_extreme_reverse_fires)


class _Cfg:
    K1M_EXTREME_REVERSE_ENABLED = True
    K1M_EXTREME_HIGH = 90.0
    K1M_EXTREME_LOW = 10.0


def _run(seed, n, is_long, cfg):
    rng = np.random.default_rng(seed)
    kn = rng.uniform(0, 100, n)
    kp = rng.uniform(0, 100, n)
    gain = rng.uniform(-5, 5, n)
    vec = check_k1m_extreme_reverse_vec(cfg, kn, kp, is_long, gain)
    mism = 0
    for i in range(n):
        ind = {"stoch_k_1m": kn[i], "k_1m_prev": kp[i]}
        s_fires, _ = check_k1m_extreme_reverse(cfg, ind, is_long, float(gain[i]))
        core = _k1m_extreme_reverse_fires(float(kn[i]), float(kp[i]), is_long,
                                          cfg.K1M_EXTREME_HIGH, cfg.K1M_EXTREME_LOW)
        gate_core = core and float(gain[i]) >= 0
        assert s_fires == gate_core, f"scalar!=core@{i}"
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
    off = _Cfg(); off.K1M_EXTREME_REVERSE_ENABLED = False
    rng = np.random.default_rng(7)
    assert not check_k1m_extreme_reverse_vec(off, rng.uniform(0, 100, 5000),
                                             rng.uniform(0, 100, 5000), True).any()
    tn += 5000
    print(f"samples={tn} vec_fires={tf} mismatches={tm}")
    print("PARITY_PASS" if tm == 0 else "PARITY_FAIL")
    if tm: sys.exit(1)


if __name__ == "__main__":
    main()
