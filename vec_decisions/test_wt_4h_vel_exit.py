"""Parity test: scalar core == vec mask for wt_4h_vel_exit over >=10000 random samples.
Mirrors the pyramid / struct_break parity test pattern.

The vec mask covers the indicator-only portion (vel-against AND k-extreme) — the same
seam the live code and v8_vec_sweep use (profit+age layered by the caller). So we compare
the vec mask against the pure core _wt_4h_vel_exit_fires(), which is also indicator-only.
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.wt_4h_vel_exit import (
    _wt_4h_vel_exit_fires,
    check_wt_4h_vel_exit_vec,
    _wt_4h_vel_exit_thresholds,
)


class _Cfg:
    WT_4H_VEL_EXIT_ENABLED = True
    WT_4H_VEL_EXIT_LONG_VEL_MIN = -2.0
    WT_4H_VEL_EXIT_SHORT_VEL_MIN = 2.0
    WT_4H_VEL_EXIT_REQUIRE_K_EXTREME = True
    WT_4H_VEL_EXIT_K_EXTREME_HIGH = 80.0
    WT_4H_VEL_EXIT_K_EXTREME_LOW = 20.0


class _CfgNoKx(_Cfg):
    WT_4H_VEL_EXIT_REQUIRE_K_EXTREME = False


def _run_cfg(cfg, n, rng):
    long_min, short_min, req_kx, kx_hi, kx_lo = _wt_4h_vel_exit_thresholds(cfg)
    total_mismatch = 0
    for is_long in (True, False):
        vel = rng.uniform(-10.0, 10.0, n)
        k_base = rng.uniform(0.0, 100.0, n)
        k_15m = rng.uniform(0.0, 100.0, n)
        # inject exact-equality / boundary edge cases
        edge = min(50, n)
        vel[:edge] = long_min if is_long else short_min
        k_base[edge : 2 * edge] = kx_hi
        k_15m[2 * edge : 3 * edge] = kx_lo
        vel[3 * edge : 4 * edge] = 0.0
        scalar = np.array(
            [
                _wt_4h_vel_exit_fires(
                    float(vel[i]), float(k_base[i]), float(k_15m[i]), is_long,
                    long_min, short_min, req_kx, kx_hi, kx_lo,
                )
                for i in range(n)
            ],
            dtype=bool,
        )
        vec = check_wt_4h_vel_exit_vec(cfg, vel, k_base, k_15m, is_long)
        mism = int(np.sum(scalar != np.asarray(vec, dtype=bool)))
        total_mismatch += mism
        fires = int(np.sum(np.asarray(vec, dtype=bool)))
        print(f"req_kx={req_kx} is_long={is_long}: samples={n} fires={fires} mismatches={mism}")
    return total_mismatch


def run(n=20000, seed=12345):
    rng = np.random.default_rng(seed)
    total_mismatch = 0
    total_mismatch += _run_cfg(_Cfg(), n, rng)
    total_mismatch += _run_cfg(_CfgNoKx(), n, rng)
    print(f"TOTAL_MISMATCHES={total_mismatch}")
    assert total_mismatch == 0, f"PARITY FAIL: {total_mismatch} mismatches"
    print("PARITY_OK")
    return total_mismatch


if __name__ == "__main__":
    run()
