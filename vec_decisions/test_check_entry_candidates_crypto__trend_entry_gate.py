"""PARITY test: scalar core == vectorized mask for TREND_ENTRY_GATE.
Run: /opt/anaconda3/envs/binance_env/bin/python vec_decisions/test_check_entry_candidates_crypto__trend_entry_gate.py
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.check_entry_candidates_crypto__trend_entry_gate import (  # noqa: E402
    _trend_entry_gate_passes,
    check_trend_entry_gate_vec,
)


class _Cfg:
    def __init__(self, bull, bear):
        self.TREND_HTF_MIN_BULL = bull
        self.TREND_HTF_MIN_BEAR = bear


def main(n=20000, seed=5):
    rng = np.random.default_rng(seed)
    score = rng.uniform(-15.0, 15.0, n)
    total = 0
    mismatches = 0
    first_bad = None
    for bull, bear in ((7, 7), (5, 9)):
        cfg = _Cfg(bull, bear)
        for is_long in (True, False):
            for is_trend in (True, False):
                vec = check_trend_entry_gate_vec(cfg, score, is_long, is_trend)
                for i in range(n):
                    if is_trend:
                        scalar = _trend_entry_gate_passes(float(score[i]), is_long,
                                                          float(bull), float(bear))
                    else:
                        scalar = True
                    total += 1
                    if scalar != bool(vec[i]):
                        mismatches += 1
                        if first_bad is None:
                            first_bad = (i, is_long, is_trend, float(score[i]),
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
