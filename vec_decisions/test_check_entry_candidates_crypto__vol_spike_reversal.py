"""PARITY test: scalar core == vectorized mask for VOL_SPIKE_REVERSAL.
Run: /opt/anaconda3/envs/binance_env/bin/python vec_decisions/test_check_entry_candidates_crypto__vol_spike_reversal.py
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.check_entry_candidates_crypto__vol_spike_reversal import (  # noqa: E402
    _vol_spike_fires,
    check_vol_spike_reversal_vec,
)


class _Cfg:
    VOL_SPIKE_RELVOL_THRESHOLD = 3.0
    VOL_SPIKE_BODY_RATIO = 0.7
    VOL_SPIKE_MIN_ALIGNMENT = 3.0


def main(n=12000, seed=4242):
    rng = np.random.default_rng(seed)
    cfg = _Cfg()
    relvol = rng.uniform(0.0, 6.0, n)
    low = rng.uniform(10.0, 100.0, n)
    rng_sz = rng.uniform(-2.0, 20.0, n)  # allow negative/zero range edge cases
    high = low + rng_sz
    op = low + rng.uniform(-1.0, 22.0, n)
    cl = low + rng.uniform(-1.0, 22.0, n)
    align = rng.uniform(0.0, 8.0, n)
    dcl4 = rng.uniform(0.0, 80.0, n)
    dch4 = rng.uniform(40.0, 140.0, n)
    px = rng.uniform(20.0, 120.0, n)
    ratio_ok = rng.integers(0, 2, n).astype(bool)
    total = 0
    mism = 0
    first = None
    for is_long in (True, False):
        vf = check_vol_spike_reversal_vec(cfg, relvol, high, low, op, cl, align, dcl4,
                                          dch4, px, is_long, ratio_ok)
        for i in range(n):
            sf, _ = _vol_spike_fires(float(relvol[i]), float(high[i]), float(low[i]),
                                     float(op[i]), float(cl[i]), float(align[i]),
                                     float(dcl4[i]), float(dch4[i]), float(px[i]),
                                     is_long, bool(ratio_ok[i]), cfg.VOL_SPIKE_RELVOL_THRESHOLD,
                                     cfg.VOL_SPIKE_BODY_RATIO, cfg.VOL_SPIKE_MIN_ALIGNMENT)
            total += 1
            if bool(sf) != bool(vf[i]):
                mism += 1
                if first is None:
                    first = (i, is_long, sf, bool(vf[i]))
    print(f"samples={total} mismatches={mism}")
    if first is not None:
        print("FIRST MISMATCH:", first)
    if mism == 0:
        print("PARITY_OK scalar==vec")
        return 0
    print("PARITY_FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
