"""Random-input PARITY test for DC_BASIS_3M_REDUCE.
Run:
    /opt/anaconda3/envs/binance_env/bin/python vec_decisions/test_process_position_crypto__dc_basis_3m_reduce.py
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.process_position_crypto__dc_basis_3m_reduce import (  # noqa: E402
    _dc_basis_3m_reduce_fires,
    check_dc_basis_3m_reduce_vec,
)


class _Cfg:
    def __init__(self, hedge_mode):
        self.HEDGE_MODE = hedge_mode


def main(n=30000, seed=23):
    rng = np.random.default_rng(seed)
    price = rng.uniform(90, 110, n)
    dl = np.where(rng.random(n) > 0.1, rng.uniform(85, 108, n), 0.0)
    dh = np.where(rng.random(n) > 0.1, rng.uniform(92, 115, n), 0.0)
    mismatches = 0
    first_bad = None
    for hedge_mode in (False, True):
        cfg = _Cfg(hedge_mode)
        for is_long in (True, False):
            vec = check_dc_basis_3m_reduce_vec(cfg, price, dl, dh, is_long)
            for idx in range(n):
                scalar = _dc_basis_3m_reduce_fires(float(price[idx]), float(dl[idx]),
                                                   float(dh[idx]), is_long, hedge_mode)
                if scalar != bool(vec[idx]):
                    mismatches += 1
                    if first_bad is None:
                        first_bad = (idx, is_long, hedge_mode, scalar, bool(vec[idx]))
    total = n * 4
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
