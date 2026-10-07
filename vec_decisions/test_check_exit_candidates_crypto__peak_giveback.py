"""Parity test: scalar core == vec mask over >=10000 random samples."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from vec_decisions.check_exit_candidates_crypto__peak_giveback import (
    check_peak_giveback, check_peak_giveback_vec, _peak_giveback_fires)


class _Cfg:
    PEAK_GIVEBACK_PROTECTION_ENABLED = True
    PEAK_GIVEBACK_MIN_PEAK_PCT = 0.5
    PEAK_GIVEBACK_HARD_ZERO_ENABLED = True
    PEAK_GIVEBACK_HARD_ZERO_GAIN = 0.08
    PEAK_GIVEBACK_DROP_TRIGGER_ENABLED = False
    PEAK_GIVEBACK_DROP_PCT = 1.0


def _run(seed, n, cfg):
    rng = np.random.default_rng(seed)
    mg = rng.uniform(-2, 10, n)
    g = rng.uniform(-5, 10, n)
    vec = check_peak_giveback_vec(cfg, mg, g)
    mism = 0
    for i in range(n):
        s_fires, _ = check_peak_giveback(cfg, float(mg[i]), float(g[i]))
        if bool(s_fires) != bool(vec[i]):
            mism += 1
    return mism, int(vec.sum())


def main():
    tm = tf = tn = 0
    for drop in (False, True):
        cfg = _Cfg(); cfg.PEAK_GIVEBACK_DROP_TRIGGER_ENABLED = drop
        for seed in range(4):
            m, f = _run(seed, 3000, cfg)
            tm += m; tf += f; tn += 3000
    off = _Cfg(); off.PEAK_GIVEBACK_PROTECTION_ENABLED = False
    rng = np.random.default_rng(7)
    assert not check_peak_giveback_vec(off, rng.uniform(-2, 10, 5000), rng.uniform(-5, 10, 5000)).any()
    tn += 5000
    print(f"samples={tn} vec_fires={tf} mismatches={tm}")
    print("PARITY_PASS" if tm == 0 else "PARITY_FAIL")
    if tm: sys.exit(1)


if __name__ == "__main__":
    main()
