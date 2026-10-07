"""Random-input parity test: scalar core (_dc_break_fires via check_dc_break) ==
vectorized mask (check_dc_break_vec) over >=10000 samples, for both LONG and SHORT.
Mirrors the pyramid parity test pattern."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from vec_decisions.dc_break import check_dc_break, check_dc_break_vec, _dc_break_fires


class _Cfg:
    DC_BREAKOUT_ENTRY_ENABLED = True
    DC_BREAKOUT_TF = "1h"
    DC_BREAKOUT_MIN_ADX = 25.0
    DC_BREAKOUT_SCORE = 15


def _run(seed, n, is_long, cfg):
    rng = np.random.default_rng(seed)
    # Wide ranges + zeros/edge values so the guard (hi>0, lo>0, adx>min) is exercised.
    price = rng.uniform(0.0, 200.0, n)
    dc_hi = np.where(rng.random(n) < 0.1, 0.0, rng.uniform(0.0, 200.0, n))
    dc_lo = np.where(rng.random(n) < 0.1, 0.0, rng.uniform(0.0, 200.0, n))
    dc_adx = np.where(rng.random(n) < 0.1, 0.0, rng.uniform(0.0, 60.0, n))
    vec = check_dc_break_vec(cfg, price, dc_hi, dc_lo, dc_adx, is_long)
    mism = 0
    tf = cfg.DC_BREAKOUT_TF
    for i in range(n):
        ind = {f"dc_high_{tf}": dc_hi[i], f"dc_low_{tf}": dc_lo[i], f"adx_{tf}": dc_adx[i]}
        s_fires, _, _ = check_dc_break(cfg, ind, float(price[i]), is_long)
        # also exercise the pure core directly
        core = _dc_break_fires(float(price[i]), float(dc_hi[i]), float(dc_lo[i]),
                               float(dc_adx[i]), is_long, cfg.DC_BREAKOUT_MIN_ADX)
        assert s_fires == core, f"scalar wrapper != core at {i}"
        if bool(s_fires) != bool(vec[i]):
            mism += 1
    return mism, int(vec.sum())


def main():
    cfg = _Cfg()
    total_mism = 0
    total_fires = 0
    total_n = 0
    for is_long in (True, False):
        for seed in range(3):
            mism, fires = _run(seed, 6000, is_long, cfg)
            total_mism += mism
            total_fires += fires
            total_n += 6000
    # Disabled-flag parity
    cfg_off = _Cfg()
    cfg_off.DC_BREAKOUT_ENTRY_ENABLED = False
    rng = np.random.default_rng(99)
    price = rng.uniform(0, 200, 5000)
    hi = rng.uniform(0, 200, 5000)
    lo = rng.uniform(0, 200, 5000)
    adx = rng.uniform(0, 60, 5000)
    vec_off = check_dc_break_vec(cfg_off, price, hi, lo, adx, True)
    assert not vec_off.any(), "disabled flag must produce all-False vec mask"
    total_n += 5000
    print(f"samples={total_n} vec_fires={total_fires} mismatches={total_mism}")
    if total_mism == 0:
        print("PARITY_PASS")
    else:
        print("PARITY_FAIL")
        sys.exit(1)


if __name__ == "__main__":
    main()
