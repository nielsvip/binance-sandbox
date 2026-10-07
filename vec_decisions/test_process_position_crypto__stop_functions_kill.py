"""Random-input PARITY test for STOP_FUNCTIONS_KILL.
Run:
    /opt/anaconda3/envs/binance_env/bin/python vec_decisions/test_process_position_crypto__stop_functions_kill.py

Covers: A-condition (kill_enabled), B-condition (age+k/d), early size guard,
price<=0 guard, and the None(scalar)/NaN(vec) k/d path.
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.process_position_crypto__stop_functions_kill import (  # noqa: E402
    _stop_functions_kill_fires,
    _stop_functions_kill_thresholds,
    check_stop_functions_kill_vec,
)


class _Cfg:
    def __init__(self, kill_enabled, start_size=9.0):
        self.LOSS_EXIT_STOP_FUNCTIONS_KILL_ENABLED = kill_enabled
        self.START_POSITION_SIZE = start_size


def main(n=15000, seed=31):
    rng = np.random.default_rng(seed)
    price = np.where(rng.random(n) > 0.05, rng.uniform(50, 150, n), 0.0)
    gain = rng.uniform(-10.0, 5.0, n)
    age = rng.uniform(0.0, 12.0, n)
    # position_amt spans below the 0.5x guard and above the 5x A-threshold
    amt = rng.uniform(0.0, 2.0, n)
    k3 = rng.uniform(0, 100, n)
    d3 = rng.uniform(0, 100, n)
    # inject None/NaN for ~10% of k/d
    nan_mask = rng.random(n) < 0.1
    k3_vec = np.where(nan_mask, np.nan, k3)
    d3_vec = np.where(nan_mask, np.nan, d3)
    mismatches = 0
    first_bad = None
    for kill_enabled in (False, True):
        cfg = _Cfg(kill_enabled)
        _ke, start_size = _stop_functions_kill_thresholds(cfg)
        for is_long in (True, False):
            vec = check_stop_functions_kill_vec(cfg, gain, age, amt, price,
                                                k3_vec, d3_vec, is_long)
            for idx in range(n):
                k_s = None if nan_mask[idx] else float(k3[idx])
                d_s = None if nan_mask[idx] else float(d3[idx])
                scalar = _stop_functions_kill_fires(
                    float(gain[idx]), float(age[idx]), float(amt[idx]),
                    float(price[idx]), k_s, d_s, is_long, kill_enabled, start_size,
                )
                if scalar != bool(vec[idx]):
                    mismatches += 1
                    if first_bad is None:
                        first_bad = (idx, is_long, kill_enabled, scalar, bool(vec[idx]))
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
