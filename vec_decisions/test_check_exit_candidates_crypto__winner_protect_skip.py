"""Parity test: scalar core == vec mask over >=10000 random samples."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from vec_decisions.check_exit_candidates_crypto__winner_protect_skip import (
    check_winner_protect_skip, check_winner_protect_skip_vec)


class _Cfg:
    WINNER_PROTECT_ENABLED = True
    RP_PROTECT_THRESHOLD = 70.0
    RP_PROTECT_MIN_GAIN = 2.0


def _run(seed, n, cfg, is_long):
    rng = np.random.default_rng(seed)
    rp = rng.uniform(-100, 100, n)
    g = rng.uniform(-5, 10, n)
    vec = check_winner_protect_skip_vec(cfg, rp, g, is_long)
    mism = 0
    for i in range(n):
        s = check_winner_protect_skip(cfg, float(rp[i]), float(g[i]), is_long)
        if bool(s) != bool(vec[i]):
            mism += 1
    return mism, int(vec.sum())


def main():
    tm = tf = tn = 0
    for thr in (70.0, 50.0):
        for min_g in (1.0, 2.0):
            cfg = _Cfg(); cfg.RP_PROTECT_THRESHOLD = thr; cfg.RP_PROTECT_MIN_GAIN = min_g
            for is_long in (True, False):
                for seed in range(2):
                    m, f = _run(seed * 31 + int(thr), 2000, cfg, is_long)
                    tm += m; tf += f; tn += 2000
    off = _Cfg(); off.WINNER_PROTECT_ENABLED = False
    rng = np.random.default_rng(99)
    assert not check_winner_protect_skip_vec(off, rng.uniform(-100, 100, 4000), rng.uniform(-5, 10, 4000), True).any()
    tn += 4000
    print(f"samples={tn} vec_fires={tf} mismatches={tm}")
    print("PARITY_PASS" if tm == 0 else "PARITY_FAIL")
    if tm:
        sys.exit(1)


if __name__ == "__main__":
    main()
