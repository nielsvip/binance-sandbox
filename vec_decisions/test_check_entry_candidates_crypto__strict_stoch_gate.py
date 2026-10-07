"""PARITY test: scalar core == vectorized mask for STRICT_STOCH_GATE.
Run: /opt/anaconda3/envs/binance_env/bin/python vec_decisions/test_check_entry_candidates_crypto__strict_stoch_gate.py
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.check_entry_candidates_crypto__strict_stoch_gate import (  # noqa: E402
    _strict_stoch_gate_passes,
    check_strict_stoch_gate_vec,
)


def main(n=20000, seed=77):
    rng = np.random.default_rng(seed)
    k1m = rng.uniform(0.0, 100.0, n)
    d1m = rng.uniform(0.0, 100.0, n)
    # force a chunk of exact ties to test the boundary
    k1m[:2000] = d1m[:2000]
    total = 0
    mismatches = 0
    first_bad = None
    for is_long in (True, False):
        vec = check_strict_stoch_gate_vec(None, k1m, d1m, is_long)
        for i in range(n):
            scalar = _strict_stoch_gate_passes(float(k1m[i]), float(d1m[i]), is_long)
            total += 1
            if scalar != bool(vec[i]):
                mismatches += 1
                if first_bad is None:
                    first_bad = (i, is_long, float(k1m[i]), float(d1m[i]), scalar, bool(vec[i]))
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
