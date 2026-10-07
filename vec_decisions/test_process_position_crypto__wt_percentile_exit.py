"""Random-input PARITY test for WT_PERCENTILE_EXIT.
Run:
    /opt/anaconda3/envs/binance_env/bin/python vec_decisions/test_process_position_crypto__wt_percentile_exit.py
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.process_position_crypto__wt_percentile_exit import (  # noqa: E402
    _wt_percentile_fires,
    check_wt_percentile_exit_vec,
    _wt_percentile_params,
)


class _Cfg:
    WT_PERCENTILE_EXIT_OB_D = 90
    WT_PERCENTILE_EXIT_OB_4H = 75
    WT_PERCENTILE_EXIT_OS_D = 10
    WT_PERCENTILE_EXIT_OS_4H = 25


def main(n=20000, seed=33):
    rng = np.random.default_rng(seed)
    pD = rng.uniform(0, 100, n)
    p4 = rng.uniform(0, 100, n)
    cfg = _Cfg()
    obD, ob4, osD, os4 = _wt_percentile_params(cfg)
    mismatches = 0
    first_bad = None
    for is_long in (True, False):
        vec = check_wt_percentile_exit_vec(cfg, pD, p4, is_long)
        for idx in range(n):
            scalar = _wt_percentile_fires(float(pD[idx]), float(p4[idx]), is_long, obD, ob4, osD, os4)
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
