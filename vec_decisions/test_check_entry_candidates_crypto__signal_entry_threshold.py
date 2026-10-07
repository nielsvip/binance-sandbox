"""PARITY test: scalar core == vectorized mask for SIGNAL_ENTRY_THRESHOLD.
Run: /opt/anaconda3/envs/binance_env/bin/python vec_decisions/test_check_entry_candidates_crypto__signal_entry_threshold.py
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.check_entry_candidates_crypto__signal_entry_threshold import (  # noqa: E402
    _signal_entry_fires,
    _effective_score_min,
    check_signal_entry_threshold_vec,
)

_REC_POOL = [None, "WAIT", "STRONG_BUY", "STRONG_SELL", "BUY", "SELL", "HEDGE_CANDIDATE",
             "GOOD_BUY", "WAIT_BUY", "HEDGE", "BUY_WAIT", "NEUTRAL", "STRONG_BUY_HEDGE"]


def main(n=12000, seed=171):
    rng = np.random.default_rng(seed)
    cfg = object()
    score = rng.uniform(-2.0, 30.0, n)
    per_sym = rng.choice([None, 4.0, 8.0, 18.0, 2.0], n)
    rec = np.array([_REC_POOL[i] for i in rng.integers(0, len(_REC_POOL), n)], dtype=object)
    smin = np.array([_effective_score_min(4.0, p) for p in per_sym], dtype=float)
    vf = check_signal_entry_threshold_vec(cfg, score, rec, smin, default_min=4.0)
    total = 0
    mism = 0
    first = None
    for i in range(n):
        sf = _signal_entry_fires(float(score[i]), str(rec[i]) if rec[i] is not None else "",
                                 float(smin[i]))
        total += 1
        if bool(sf) != bool(vf[i]):
            mism += 1
            if first is None:
                first = (i, rec[i], score[i], smin[i], sf, bool(vf[i]))
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
