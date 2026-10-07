"""Random-input PARITY test for E_1_WT_DELTA_EXIT.
Run:
    /opt/anaconda3/envs/binance_env/bin/python vec_decisions/test_process_position_crypto__e1_wt_delta_exit.py
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.process_position_crypto__e1_wt_delta_exit import (  # noqa: E402
    _e1_wt_delta_fires,
    check_e1_wt_delta_exit_vec,
    _e1_thr,
)


class _Cfg:
    E_1_EXIT_DELTA_THR = 50.0


def main(n=20000, seed=55):
    rng = np.random.default_rng(seed)
    d = rng.uniform(-120, 120, n)
    cfg = _Cfg()
    thr = _e1_thr(cfg)
    mismatches = 0
    first_bad = None
    for is_long in (True, False):
        vec = check_e1_wt_delta_exit_vec(cfg, d, is_long)
        for idx in range(n):
            scalar = _e1_wt_delta_fires(float(d[idx]), is_long, thr)
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
