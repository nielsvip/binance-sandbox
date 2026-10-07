"""Parity test: scalar core == vec mask over >=10000 random samples, both sides."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from vec_decisions.check_exit_candidates_crypto__key_level_crash import (
    check_key_level_crash, check_key_level_crash_vec, _TFS)


class _Cfg:
    KEY_LEVEL_CRASH_CLOSE_MIN_TFS = 3


def _run(seed, n, is_long, cfg):
    rng = np.random.default_rng(seed)
    price = rng.uniform(0.0, 200.0, n)
    lows = [np.where(rng.random(n) < 0.15, 0.0, rng.uniform(0.0, 200.0, n)) for _ in _TFS]
    highs = [np.where(rng.random(n) < 0.15, 0.0, rng.uniform(0.0, 200.0, n)) for _ in _TFS]
    vec = check_key_level_crash_vec(cfg, price, tuple(lows), tuple(highs), is_long)
    mism = 0
    for i in range(n):
        ind = {}
        for j, tf in enumerate(_TFS):
            ind[f"dc_low_{tf}"] = lows[j][i]
            ind[f"dc_high_{tf}"] = highs[j][i]
        s_fires, _ = check_key_level_crash(cfg, ind, float(price[i]), is_long)
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
