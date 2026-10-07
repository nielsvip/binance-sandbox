"""Random-input PARITY test: scalar core == vectorized mask for the tiered
DC_BREAKOUT entry block in check_entry_candidates_for_account.

>=10000 random samples (×LONG/SHORT ×allow-flag combos), assert ZERO mismatches.
Run: /opt/anaconda3/envs/binance_env/bin/python vec_decisions/test_check_entry_candidates_crypto__dc_breakout_tiered.py
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.check_entry_candidates_crypto__dc_breakout_tiered import (  # noqa: E402
    _dc_breakout_tiered_fires,
    check_dc_breakout_tiered_vec,
)


class _Cfg:
    def __init__(self, allow15, allow3, cap=95.0):
        self.DC_BREAKOUT_ALLOW_15M = allow15
        self.DC_BREAKOUT_ALLOW_3M = allow3
        self.DC_BREAKOUT_OVERBOUGHT_K15M_CAP = cap


def main(n=15000, seed=4242):
    rng = np.random.default_rng(seed)
    # price near 100; DC levels scattered around it so all tiers can fire.
    price = rng.uniform(90.0, 110.0, n)
    dch4h = rng.uniform(95.0, 105.0, n) * (rng.random(n) > 0.1)
    dch1h = rng.uniform(95.0, 105.0, n) * (rng.random(n) > 0.1)
    dch15 = rng.uniform(95.0, 105.0, n) * (rng.random(n) > 0.1)
    dch3 = rng.uniform(95.0, 105.0, n) * (rng.random(n) > 0.1)
    dcl4h = rng.uniform(95.0, 105.0, n) * (rng.random(n) > 0.1)
    dcl1h = rng.uniform(95.0, 105.0, n) * (rng.random(n) > 0.1)
    dcl15 = rng.uniform(95.0, 105.0, n) * (rng.random(n) > 0.1)
    dcl3 = rng.uniform(95.0, 105.0, n) * (rng.random(n) > 0.1)
    k15 = rng.uniform(0.0, 100.0, n)
    wt1_3m = rng.uniform(-80.0, 80.0, n)
    wt2_3m = rng.uniform(-80.0, 80.0, n)
    wt1_15m = rng.uniform(-80.0, 80.0, n)
    wt2_15m = rng.uniform(-80.0, 80.0, n)
    total = 0
    mismatches = 0
    first_bad = None
    for is_long in (True, False):
        for allow15 in (True, False):
            for allow3 in (True, False):
                for cap in (95.0, 70.0):
                    cfg = _Cfg(allow15, allow3, cap)
                    vec = check_dc_breakout_tiered_vec(
                        cfg, price, dch4h, dch1h, dch15, dch3, dcl4h, dcl1h, dcl15,
                        dcl3, k15, wt1_3m, wt2_3m, wt1_15m, wt2_15m, is_long,
                    )
                    for i in range(n):
                        scalar = _dc_breakout_tiered_fires(
                            float(price[i]), float(dch4h[i]), float(dch1h[i]),
                            float(dch15[i]), float(dch3[i]), float(dcl4h[i]),
                            float(dcl1h[i]), float(dcl15[i]), float(dcl3[i]),
                            float(k15[i]), float(wt1_3m[i]), float(wt2_3m[i]),
                            float(wt1_15m[i]), float(wt2_15m[i]), is_long,
                            allow15, allow3, cap,
                        )
                        total += 1
                        if scalar != bool(vec[i]):
                            mismatches += 1
                            if first_bad is None:
                                first_bad = (i, is_long, allow15, allow3, cap,
                                             scalar, bool(vec[i]))
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
