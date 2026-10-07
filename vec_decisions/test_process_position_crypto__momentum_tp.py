"""Random-input PARITY test for MOMENTUM_TP.
Run:
    /opt/anaconda3/envs/binance_env/bin/python vec_decisions/test_process_position_crypto__momentum_tp.py
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.process_position_crypto__momentum_tp import (  # noqa: E402
    _momentum_tp_fires,
    check_momentum_tp_vec,
)


class _Cfg:
    pass


def main(n=40000, seed=77):
    rng = np.random.default_rng(seed)
    price = rng.uniform(90, 110, n)
    gain = rng.uniform(-0.5, 2.0, n)
    rr = rng.random(n) > 0.5
    k3 = rng.uniform(0, 100, n)
    k3p = rng.uniform(0, 100, n)
    k15 = rng.uniform(0, 100, n)
    k15p = rng.uniform(0, 100, n)
    k1h = rng.uniform(0, 100, n)
    lo = np.where(rng.random(n) > 0.1, rng.uniform(85, 105, n), 0.0)
    lop = np.where(rng.random(n) > 0.1, rng.uniform(85, 105, n), 0.0)
    hi = np.where(rng.random(n) > 0.1, rng.uniform(95, 115, n), 0.0)
    hip = np.where(rng.random(n) > 0.1, rng.uniform(95, 115, n), 0.0)
    cfg = _Cfg()
    mismatches = 0
    first_bad = None
    for is_long in (True, False):
        vec = check_momentum_tp_vec(cfg, price, gain, rr, k3, k3p, k15, k15p, k1h,
                                    lo, lop, hi, hip, is_long)
        for idx in range(n):
            scalar = _momentum_tp_fires(
                is_long, float(price[idx]), float(gain[idx]), bool(rr[idx]),
                float(k3[idx]), float(k3p[idx]), float(k15[idx]), float(k15p[idx]),
                float(k1h[idx]), float(lo[idx]), float(lop[idx]),
                float(hi[idx]), float(hip[idx]),
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
