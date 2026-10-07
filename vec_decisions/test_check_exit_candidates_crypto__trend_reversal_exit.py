"""Parity test: scalar core == vec mask over >=10000 random samples."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from vec_decisions.check_exit_candidates_crypto__trend_reversal_exit import (
    check_trend_reversal_exit, check_trend_reversal_exit_vec)


class _Cfg:
    TREND_MIN_GAIN_EXIT = 0.10
    TREND_EXIT_SCORE_FLIP = 0


def _run(seed, n, cfg, is_long):
    rng = np.random.default_rng(seed)
    score = rng.integers(-8, 9, n).astype(float)
    g = rng.uniform(-5, 10, n)
    vec = check_trend_reversal_exit_vec(cfg, score, g, is_long)
    mism = 0
    for i in range(n):
        s_fires, _ = check_trend_reversal_exit(cfg, float(score[i]), float(g[i]), is_long)
        if bool(s_fires) != bool(vec[i]):
            mism += 1
    return mism, int(vec.sum())


def main():
    tm = tf = tn = 0
    for flip in (0, 1, 2):
        cfg = _Cfg(); cfg.TREND_EXIT_SCORE_FLIP = flip
        for min_g in (0.10, 0.5):
            cfg.TREND_MIN_GAIN_EXIT = min_g
            for is_long in (True, False):
                for seed in range(2):
                    m, f = _run(seed * 17 + flip, 2000, cfg, is_long)
                    tm += m; tf += f; tn += 2000
    print(f"samples={tn} vec_fires={tf} mismatches={tm}")
    print("PARITY_PASS" if tm == 0 else "PARITY_FAIL")
    if tm:
        sys.exit(1)


if __name__ == "__main__":
    main()
