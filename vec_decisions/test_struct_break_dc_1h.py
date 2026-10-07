"""Parity test: scalar core == vec mask for struct_break_dc_1h over >=10000 random samples.
Mirrors the pyramid parity test pattern."""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.struct_break_dc_1h import (
    _struct_break_dc_1h_fires,
    check_struct_break_dc_1h_vec,
)


class _Cfg:
    NOLOSS_MIN_PROFIT_PCT_TRADIER = 3.0


def run(n=20000, seed=12345):
    rng = np.random.default_rng(seed)
    cfg = _Cfg()
    min_gain = float(cfg.NOLOSS_MIN_PROFIT_PCT_TRADIER)
    total_mismatch = 0
    for is_long in (True, False):
        gain = rng.uniform(-5.0, 10.0, n)
        price = rng.uniform(1.0, 1000.0, n)
        dc_basis = rng.uniform(1.0, 1000.0, n)
        lr_trend = rng.uniform(-2.0, 2.0, n)
        # inject exact-equality / boundary edge cases
        edge = min(50, n)
        gain[:edge] = min_gain
        lr_trend[edge : 2 * edge] = 0.0
        dc_basis[2 * edge : 3 * edge] = price[2 * edge : 3 * edge]
        scalar = np.array(
            [
                _struct_break_dc_1h_fires(
                    float(gain[i]), float(price[i]), float(dc_basis[i]),
                    float(lr_trend[i]), is_long, min_gain,
                )
                for i in range(n)
            ],
            dtype=bool,
        )
        vec = check_struct_break_dc_1h_vec(cfg, gain, price, dc_basis, lr_trend, is_long)
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
