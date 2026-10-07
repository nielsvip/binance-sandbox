"""PARITY test: scalar core == vectorized mask+delta for COMPRESSION_BOOST.
Run: /opt/anaconda3/envs/binance_env/bin/python vec_decisions/test_check_entry_candidates_crypto__compression_boost.py
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.check_entry_candidates_crypto__compression_boost import (  # noqa: E402
    _compression_boost_fires,
    check_compression_boost_vec,
)


def main(n=20000, seed=909):
    rng = np.random.default_rng(seed)
    atr_1h = rng.uniform(0.0, 0.4, n)
    atr_4h = rng.uniform(0.0, 0.4, n)
    atr_D = rng.uniform(0.0, 0.4, n)
    wt1_15m = rng.uniform(-50.0, 50.0, n)
    wt2_15m = rng.uniform(-50.0, 50.0, n)
    score = rng.uniform(0.0, 30.0, n)
    total = 0
    mismatches = 0
    first_bad = None
    for is_long in (True, False):
        vf, vd = check_compression_boost_vec(None, atr_1h, atr_4h, atr_D, wt1_15m,
                                             wt2_15m, score, is_long)
        for i in range(n):
            sf, sd = _compression_boost_fires(float(atr_1h[i]), float(atr_4h[i]),
                                              float(atr_D[i]), float(wt1_15m[i]),
                                              float(wt2_15m[i]), float(score[i]),
                                              is_long)
            total += 1
            if sf != bool(vf[i]) or abs(sd - float(vd[i])) > 1e-9:
                mismatches += 1
                if first_bad is None:
                    first_bad = (i, is_long, sf, bool(vf[i]), sd, float(vd[i]))
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
