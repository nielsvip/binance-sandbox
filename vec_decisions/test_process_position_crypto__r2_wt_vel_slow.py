"""Random-input PARITY test for R2_WT_VEL_SLOW: scalar core == vectorized mask.

>=10000 samples per side x decel_only in {True,False}, ZERO mismatches.
Run:
    /opt/anaconda3/envs/binance_env/bin/python vec_decisions/test_process_position_crypto__r2_wt_vel_slow.py
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.process_position_crypto__r2_wt_vel_slow import (  # noqa: E402
    _r2_tf_fires,
    check_r2_wt_vel_slow_vec,
    _r2_params,
)


class _Cfg:
    R2_PEAK_MIN_PCT = 0.5
    WT_15M_VEL_SLOW_GAIN_BAND_PCT = 0.10
    WT_15M_VEL_SLOW_GAIN_FLOOR_PCT = 0.01
    WT_VEL_DECEL_RATIO = 0.5
    WT_15M_VEL_NEAR_ZERO_THRESHOLD = 0.1
    WT_VEL_USE_DECEL_RATIO_ONLY = True


def main(n=20000, seed=99):
    rng = np.random.default_rng(seed)
    gain = rng.uniform(-0.2, 0.6, n)
    max_gain = rng.uniform(0.0, 2.0, n)
    v = rng.uniform(-3.0, 3.0, n)
    accel = rng.uniform(-3.0, 3.0, n)
    wt1_D = np.where(rng.random(n) > 0.2, rng.uniform(-50, 50, n), 0.0)
    wt2_D = np.where(rng.random(n) > 0.2, rng.uniform(-50, 50, n), 0.0)
    mismatches = 0
    first_bad = None
    for decel_only in (True, False):
        cfg = _Cfg()
        cfg.WT_VEL_USE_DECEL_RATIO_ONLY = decel_only
        peak_min, band, floor, dr, nz, do = _r2_params(cfg)
        for is_long in (True, False):
            vec = check_r2_wt_vel_slow_vec(cfg, gain, max_gain, v, accel, wt1_D, wt2_D, is_long)
            for idx in range(n):
                scalar = _r2_tf_fires(
                    float(gain[idx]), float(max_gain[idx]), float(v[idx]), float(accel[idx]),
                    float(wt1_D[idx]), float(wt2_D[idx]), is_long,
                    peak_min, floor, band, dr, nz, do,
                )
                if scalar != bool(vec[idx]):
                    mismatches += 1
                    if first_bad is None:
                        first_bad = (idx, is_long, decel_only, scalar, bool(vec[idx]))
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
