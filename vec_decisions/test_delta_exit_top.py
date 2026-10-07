"""Parity test: scalar core == vec mask for delta_exit_top (DELTA_EXIT_TOP/BOTTOM)
over >=10000 random samples per side. Mirrors the pyramid / struct_break parity test."""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.delta_exit_top import (
    _delta_exit_top_fires,
    check_delta_exit_top_vec,
    _delta_exit_thresholds,
)


class _Cfg:
    DELTA_ENGINE_ENABLED = True
    DELTA_EXIT_ENABLED = True
    RZ_EXIT_ENABLED = True
    RZ_TOP_BB_THRESHOLD = 0.85
    RZ_BOT_BB_THRESHOLD = 0.15
    RZ_ZSCORE_ZONE_ENABLED = True


def run(n=20000, seed=98765):
    rng = np.random.default_rng(seed)
    cfg = _Cfg()
    rz_top_bb, rz_bot_bb, zscore_enabled = _delta_exit_thresholds(cfg)
    total_mismatch = 0
    for is_long in (True, False):
        price = rng.uniform(0.0, 1200.0, n)
        bb_upper = rng.uniform(0.0, 1200.0, n)
        bb_lower = rng.uniform(0.0, 1200.0, n)
        dc_high = rng.uniform(0.0, 1200.0, n)
        dc_low = rng.uniform(0.0, 1200.0, n)
        bb_pb = rng.uniform(-0.1, 1.1, n)
        dc_pos = rng.uniform(0.0, 1.0, n)
        zs1 = rng.uniform(-3.0, 3.0, n)
        zs4 = rng.uniform(-3.0, 3.0, n)
        mom1 = rng.integers(-2, 3, n).astype(float)
        mom4 = rng.integers(-2, 3, n).astype(float)
        div1 = rng.integers(-1, 2, n).astype(float)
        div4 = rng.integers(-1, 2, n).astype(float)
        divD = rng.integers(-1, 2, n).astype(float)
        vel1 = rng.uniform(-5.0, 5.0, n)
        k1 = rng.uniform(0.0, 100.0, n)
        hi_co = rng.integers(0, 2, n).astype(bool)
        k15 = rng.uniform(0.0, 100.0, n)
        dc_lo4 = rng.uniform(0.0, 1200.0, n)
        vel3 = rng.uniform(-5.0, 5.0, n)
        k4 = rng.uniform(0.0, 100.0, n)
        mfi1 = rng.uniform(0.0, 100.0, n)
        mfi4 = rng.uniform(0.0, 100.0, n)
        lo_cu = rng.integers(0, 2, n).astype(bool)
        # inject boundary edge cases
        e = min(40, n)
        bb_pb[:e] = rz_top_bb
        bb_pb[e:2 * e] = rz_bot_bb
        dc_pos[2 * e:3 * e] = 0.7
        k1[3 * e:4 * e] = 90.0
        k15[4 * e:5 * e] = 5.0
        k15[5 * e:6 * e] = 20.0
        vel3[6 * e:7 * e] = 2.0
        zs4[7 * e:8 * e] = 2.0
        price[8 * e:9 * e] = dc_lo4[8 * e:9 * e] * 1.002
        scalar = np.array(
            [
                _delta_exit_top_fires(
                    is_long, float(price[i]), float(bb_upper[i]), float(bb_lower[i]),
                    float(dc_high[i]), float(dc_low[i]), float(bb_pb[i]), float(dc_pos[i]),
                    float(zs1[i]), float(zs4[i]), int(mom1[i]), int(mom4[i]),
                    int(div1[i]), int(div4[i]), int(divD[i]), float(vel1[i]), float(k1[i]),
                    bool(hi_co[i]), float(k15[i]), float(dc_lo4[i]), float(vel3[i]),
                    float(k4[i]), float(mfi1[i]), float(mfi4[i]), bool(lo_cu[i]),
                    rz_top_bb, rz_bot_bb, zscore_enabled,
                )
                for i in range(n)
            ],
            dtype=bool,
        )
        vec = check_delta_exit_top_vec(
            cfg, price, bb_upper, bb_lower, dc_high, dc_low, bb_pb, dc_pos, zs1, zs4,
            mom1, mom4, div1, div4, divD, vel1, k1, hi_co, k15, dc_lo4, vel3, k4,
            mfi1, mfi4, lo_cu, is_long,
        )
        mism = int(np.sum(scalar != vec))
        total_mismatch += mism
        fires = int(np.sum(vec))
        print(f"is_long={is_long}: samples={n} fires={fires} mismatches={mism}")
    print(f"TOTAL_MISMATCHES={total_mismatch}")
    assert total_mismatch == 0, f"PARITY FAIL: {total_mismatch} mismatches"
    print("PARITY_OK")
    return total_mismatch


if __name__ == "__main__":
    run()
