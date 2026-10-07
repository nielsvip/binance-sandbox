"""Random-input PARITY test for WT15M_AGAINST trigger.
Run:
    /opt/anaconda3/envs/binance_env/bin/python vec_decisions/test_process_position_crypto__wt15m_against.py
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.process_position_crypto__wt15m_against import (  # noqa: E402
    _wt15m_against,
    check_wt15m_against_vec,
)


class _Cfg:
    pass


def main(n=20000, seed=44):
    rng = np.random.default_rng(seed)
    # include zeros to exercise the guard
    w1 = np.where(rng.random(n) > 0.1, rng.uniform(-60, 60, n), 0.0)
    w2 = np.where(rng.random(n) > 0.1, rng.uniform(-60, 60, n), 0.0)
    cfg = _Cfg()
    mismatches = 0
    first_bad = None
    for is_long in (True, False):
        vec = check_wt15m_against_vec(cfg, w1, w2, is_long)
        for idx in range(n):
            scalar = _wt15m_against(float(w1[idx]), float(w2[idx]), is_long)
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
