"""Random-input PARITY test: scalar core == vectorized mask for R1_DC_LOW4_3M.

>=10000 random samples per side, assert ZERO mismatches between _r1_breaches and
check_r1_dc_low4_emergency_vec. Run:
    /opt/anaconda3/envs/binance_env/bin/python vec_decisions/test_process_position_crypto__r1_dc_low4_emergency.py
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.process_position_crypto__r1_dc_low4_emergency import (  # noqa: E402
    _r1_breaches,
    check_r1_dc_low4_emergency_vec,
    _r1_atr_mult,
)


class _Cfg:
    R1_ATR_3M_MULT = 3.0


def main(n=20000, seed=7):
    rng = np.random.default_rng(seed)
    price = rng.uniform(90.0, 110.0, n)
    # mix of zero (skip) and positive stops to exercise active mask
    r1_stop = np.where(rng.random(n) > 0.3, rng.uniform(95.0, 105.0, n), 0.0)
    entry_px = np.where(rng.random(n) > 0.2, rng.uniform(95.0, 105.0, n), 0.0)
    atr_3m = np.where(rng.random(n) > 0.2, rng.uniform(0.1, 3.0, n), 0.0)
    high_3m = np.where(rng.random(n) > 0.15, rng.uniform(95.0, 105.0, n), 0.0)
    high_3m_prev = np.where(rng.random(n) > 0.15, rng.uniform(95.0, 105.0, n), 0.0)
    low_3m = np.where(rng.random(n) > 0.15, rng.uniform(90.0, 100.0, n), 0.0)
    low_3m_prev = np.where(rng.random(n) > 0.15, rng.uniform(90.0, 100.0, n), 0.0)
    cfg = _Cfg()
    mult = _r1_atr_mult(cfg)
    mismatches = 0
    first_bad = None
    for is_long in (True, False):
        vec = check_r1_dc_low4_emergency_vec(
            cfg, price, r1_stop, entry_px, atr_3m, high_3m, high_3m_prev,
            low_3m, low_3m_prev, is_long,
        )
        for idx in range(n):
            scalar = _r1_breaches(
                float(price[idx]), float(r1_stop[idx]), is_long,
                float(atr_3m[idx]), float(entry_px[idx]), mult,
                float(high_3m[idx]), float(high_3m_prev[idx]),
                float(low_3m[idx]), float(low_3m_prev[idx]),
            )
            if scalar != bool(vec[idx]):
                mismatches += 1
                if first_bad is None:
                    first_bad = (idx, is_long, scalar, bool(vec[idx]))
    total = n * 2
    print(f"samples={total} mismatches={mismatches}")
    if first_bad is not None:
        print("FIRST MISMATCH:", first_bad)
    if mismatches == 0:
        print("PARITY_OK scalar==vec")
        return 0
    print("PARITY_FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
