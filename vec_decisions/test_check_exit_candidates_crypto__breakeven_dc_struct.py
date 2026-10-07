"""Parity test: scalar core == vec mask over >=10000 random samples, both sides + modes."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from vec_decisions.check_exit_candidates_crypto__breakeven_dc_struct import (
    check_breakeven_dc_struct, check_breakeven_dc_struct_vec, _breakeven_dc_struct_fields)


class _Cfg:
    BREAKEVEN_DC_LOW4_ENABLED = True
    BREAKEVEN_DC_FIELD_MODE = "DC4"


def _run(seed, n, is_long, cfg):
    rng = np.random.default_rng(seed)
    low_key, high_key, _ = _breakeven_dc_struct_fields(cfg)
    price = rng.uniform(0.0, 200.0, n)
    lo = np.where(rng.random(n) < 0.1, 0.0, rng.uniform(0.0, 200.0, n))
    hi = np.where(rng.random(n) < 0.1, 0.0, rng.uniform(0.0, 200.0, n))
    htf = rng.random(n) < 0.3
    vec = check_breakeven_dc_struct_vec(cfg, price, lo, hi, is_long, htf)
    mism = 0
    for i in range(n):
        ind = {low_key: lo[i], high_key: hi[i]}
        s_fires, _ = check_breakeven_dc_struct(cfg, ind, float(price[i]), is_long, bool(htf[i]))
        if bool(s_fires) != bool(vec[i]):
            mism += 1
    return mism, int(vec.sum())


def main():
    tm = tf = tn = 0
    for mode in ("DC4", "DC"):
        cfg = _Cfg(); cfg.BREAKEVEN_DC_FIELD_MODE = mode
        for is_long in (True, False):
            for seed in range(2):
                m, f = _run(seed, 3000, is_long, cfg)
                tm += m; tf += f; tn += 3000
    off = _Cfg(); off.BREAKEVEN_DC_LOW4_ENABLED = False
    rng = np.random.default_rng(7)
    assert not check_breakeven_dc_struct_vec(off, rng.uniform(0, 200, 5000),
                                             rng.uniform(0, 200, 5000), rng.uniform(0, 200, 5000), True).any()
    tn += 5000
    print(f"samples={tn} vec_fires={tf} mismatches={tm}")
    print("PARITY_PASS" if tm == 0 else "PARITY_FAIL")
    if tm: sys.exit(1)


if __name__ == "__main__":
    main()
