"""Random-input PARITY test for WT_EXHAUST_EXIT.
Run:
    /opt/anaconda3/envs/binance_env/bin/python vec_decisions/test_process_position_crypto__wt_exhaust_exit.py
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.process_position_crypto__wt_exhaust_exit import (  # noqa: E402
    _wt_exhaust_fires,
    check_wt_exhaust_exit_vec,
    _wt_exhaust_params,
)

_STATES = ["EXHAUST_UP", "EXHAUST_DOWN", "NEUTRAL", "exhaust_up", "", "BUILDING"]


class _Cfg:
    WT_EXHAUST_EXIT_REQUIRE_GAIN = False
    WT_EXHAUST_EXIT_MIN_GAIN_PCT = 0.0


def main(n=20000, seed=21):
    rng = np.random.default_rng(seed)
    m4 = np.array([_STATES[i] for i in rng.integers(0, len(_STATES), n)], dtype=object)
    m1 = np.array([_STATES[i] for i in rng.integers(0, len(_STATES), n)], dtype=object)
    m15 = np.array([_STATES[i] for i in rng.integers(0, len(_STATES), n)], dtype=object)
    g = rng.uniform(-1.0, 2.0, n)
    mismatches = 0
    first_bad = None
    for req in (True, False):
        for min_gain in (0.0, 0.5):
            cfg = _Cfg()
            cfg.WT_EXHAUST_EXIT_REQUIRE_GAIN = req
            cfg.WT_EXHAUST_EXIT_MIN_GAIN_PCT = min_gain
            rq, mg = _wt_exhaust_params(cfg)
            for is_long in (True, False):
                vec = check_wt_exhaust_exit_vec(cfg, m4, m1, m15, g, is_long)
                for idx in range(n):
                    scalar = _wt_exhaust_fires(
                        str(m4[idx]), str(m1[idx]), str(m15[idx]), is_long,
                        float(g[idx]), rq, mg,
                    )
                    if scalar != bool(vec[idx]):
                        mismatches += 1
                        if first_bad is None:
                            first_bad = (idx, is_long, req, min_gain, scalar, bool(vec[idx]))
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
