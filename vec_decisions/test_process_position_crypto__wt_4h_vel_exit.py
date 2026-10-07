"""Random-input PARITY test for WT_4H_VEL_EXIT.
Run:
    /opt/anaconda3/envs/binance_env/bin/python vec_decisions/test_process_position_crypto__wt_4h_vel_exit.py
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.process_position_crypto__wt_4h_vel_exit import (  # noqa: E402
    _wt_4h_vel_fires,
    check_wt_4h_vel_exit_vec,
    _wt_4h_params,
)


class _Cfg:
    WT_4H_VEL_EXIT_LONG_VEL_MIN = -2.0
    WT_4H_VEL_EXIT_SHORT_VEL_MIN = 2.0
    WT_4H_VEL_EXIT_REQUIRE_PROFIT = True
    COMMISSION_BUFFER_PCT = 0.10
    WT_4H_VEL_EXIT_REQUIRE_K_EXTREME = True
    WT_4H_VEL_EXIT_K_EXTREME_HIGH = 80.0
    WT_4H_VEL_EXIT_K_EXTREME_LOW = 20.0


def main(n=20000, seed=11):
    rng = np.random.default_rng(seed)
    v = rng.uniform(-5, 5, n)
    g = rng.uniform(-1.0, 2.0, n)
    k3 = rng.uniform(0, 100, n)
    k15 = rng.uniform(0, 100, n)
    mismatches = 0
    first_bad = None
    for req_p in (True, False):
        for req_kx in (True, False):
            cfg = _Cfg()
            cfg.WT_4H_VEL_EXIT_REQUIRE_PROFIT = req_p
            cfg.WT_4H_VEL_EXIT_REQUIRE_K_EXTREME = req_kx
            p = _wt_4h_params(cfg)
            for is_long in (True, False):
                vec = check_wt_4h_vel_exit_vec(cfg, v, g, k3, k15, is_long)
                for idx in range(n):
                    scalar = _wt_4h_vel_fires(
                        float(v[idx]), float(g[idx]), float(k3[idx]), float(k15[idx]),
                        is_long, p[0], p[1], p[2], p[3], p[4], p[5], p[6],
                    )
                    if scalar != bool(vec[idx]):
                        mismatches += 1
                        if first_bad is None:
                            first_bad = (idx, is_long, req_p, req_kx, scalar, bool(vec[idx]))
    total = n * 8
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
