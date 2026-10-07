"""Random-input parity test for vec_decisions/reentry_breakout.

Asserts the scalar core (_reentry_breakout_fires applied per-row) equals the
vectorized mask (check_reentry_breakout_vec) over >=10000 random samples, across
all four (allow_15m, require_k, require_wt) flag combinations and both sides.
Mirrors the pyramid parity test pattern.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vec_decisions.reentry_breakout import (
    _reentry_breakout_fires,
    check_reentry_breakout_vec,
)


class _Cfg:
    def __init__(self, allow_15m, require_k, require_wt, ftf="3m"):
        self.REENTRY2_DC_BREAK_ALLOW_15M = allow_15m
        self.REENTRY2_DC_BREAK_REQUIRE_K_FILTER = require_k
        self.REENTRY2_DC_BREAK_REQUIRE_WT_FILTER = require_wt
        self.REENTRY2_DC_BREAK_FILTER_TF = ftf


def _gen(n, seed):
    rng = np.random.default_rng(seed)
    base = rng.uniform(10.0, 200.0, n)
    price = base * rng.uniform(0.9, 1.1, n)
    # DC channels near price so breakouts fire ~half the time; sprinkle zeros to
    # exercise the `> 0` guards.
    dh3 = base * rng.uniform(0.95, 1.05, n)
    dl3 = base * rng.uniform(0.95, 1.05, n)
    dh1h = base * rng.uniform(0.95, 1.05, n)
    dl1h = base * rng.uniform(0.95, 1.05, n)
    dh15 = base * rng.uniform(0.95, 1.05, n)
    dl15 = base * rng.uniform(0.95, 1.05, n)
    for arr in (dh3, dl3, dh1h, dl1h, dh15, dl15):
        arr[rng.random(n) < 0.1] = 0.0
    # K/D/WT include exact-zero rows (no-data) to test the kdata/wtdata guards.
    fk = rng.uniform(0, 100, n)
    fd = rng.uniform(0, 100, n)
    fw1 = rng.uniform(-80, 80, n)
    fw2 = rng.uniform(-80, 80, n)
    zero_kd = rng.random(n) < 0.15
    fk[zero_kd] = 0.0
    fd[zero_kd] = 0.0
    zero_wt = rng.random(n) < 0.15
    fw1[zero_wt] = 0.0
    fw2[zero_wt] = 0.0
    return price, dh3, dl3, dh1h, dl1h, dh15, dl15, fk, fd, fw1, fw2


def main():
    n = 20000
    total_mismatch = 0
    total_checked = 0
    fire_count = 0
    for seed, (allow_15m, require_k, require_wt) in enumerate(
        [(a, k, w) for a in (True, False) for k in (True, False) for w in (True, False)]
    ):
        cfg = _Cfg(allow_15m, require_k, require_wt)
        for is_long in (True, False):
            (price, dh3, dl3, dh1h, dl1h, dh15, dl15, fk, fd, fw1, fw2) = _gen(n, seed * 7 + int(is_long))
            vec_mask = check_reentry_breakout_vec(
                cfg, price, dh3, dl3, dh1h, dl1h, dh15, dl15, fk, fd, fw1, fw2, is_long
            )
            scalar = np.empty(n, dtype=bool)
            for j in range(n):
                scalar[j] = _reentry_breakout_fires(
                    float(price[j]), float(dh3[j]), float(dl3[j]), float(dh1h[j]),
                    float(dl1h[j]), float(dh15[j]), float(dl15[j]), float(fk[j]),
                    float(fd[j]), float(fw1[j]), float(fw2[j]), is_long,
                    allow_15m, require_k, require_wt,
                )
            mism = int(np.sum(scalar != np.asarray(vec_mask, dtype=bool)))
            total_mismatch += mism
            total_checked += n
            fire_count += int(np.sum(scalar))
    print(f"samples_checked={total_checked} fires={fire_count} mismatches={total_mismatch}")
    assert total_mismatch == 0, f"PARITY FAIL: {total_mismatch} mismatches"
    print("PARITY OK")


if __name__ == "__main__":
    main()
