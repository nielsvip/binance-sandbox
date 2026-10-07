"""Random-input PARITY test for DC15M_FORCE_EXIT.
Run:
    /opt/anaconda3/envs/binance_env/bin/python vec_decisions/test_process_position_crypto__dc15m_force_exit.py
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.process_position_crypto__dc15m_force_exit import (  # noqa: E402
    _dc15m_force_exit_fires,
    check_dc15m_force_exit_vec,
)


class _Cfg:
    pass


def main(n=30000, seed=11):
    rng = np.random.default_rng(seed)
    price = rng.uniform(90, 110, n)
    gain = rng.uniform(-3.0, 3.0, n)
    dl = np.where(rng.random(n) > 0.1, rng.uniform(85, 108, n), 0.0)
    dh = np.where(rng.random(n) > 0.1, rng.uniform(92, 115, n), 0.0)
    cfg = _Cfg()
    mismatches = 0
    first_bad = None
    for is_long in (True, False):
        vec = check_dc15m_force_exit_vec(cfg, price, gain, dl, dh, is_long)
        for idx in range(n):
            scalar = _dc15m_force_exit_fires(float(price[idx]), float(gain[idx]),
                                             float(dl[idx]), float(dh[idx]), is_long)
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
