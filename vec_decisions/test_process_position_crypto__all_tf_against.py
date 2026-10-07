"""Random-input PARITY test for ALL_TF_AGAINST_CLOSE.
Run:
    /opt/anaconda3/envs/binance_env/bin/python vec_decisions/test_process_position_crypto__all_tf_against.py
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.process_position_crypto__all_tf_against import (  # noqa: E402
    _all_tf_against_fires,
    check_all_tf_against_vec,
    _TFS,
)


class _Cfg:
    ALL_TF_AGAINST_CLOSE_MIN_TFS = 5


def main(n=20000, seed=3):
    rng = np.random.default_rng(seed)
    wt1_arrs = {tf: rng.uniform(-60, 60, n) for tf in _TFS}
    wt2_arrs = {tf: rng.uniform(-60, 60, n) for tf in _TFS}
    mismatches = 0
    first_bad = None
    for min_tfs in (3, 4, 5):
        cfg = _Cfg()
        cfg.ALL_TF_AGAINST_CLOSE_MIN_TFS = min_tfs
        for is_long in (True, False):
            vec = check_all_tf_against_vec(cfg, wt1_arrs, wt2_arrs, is_long)
            for idx in range(n):
                w1 = {tf: float(wt1_arrs[tf][idx]) for tf in _TFS}
                w2 = {tf: float(wt2_arrs[tf][idx]) for tf in _TFS}
                scalar = _all_tf_against_fires(w1, w2, is_long, min_tfs)
                if scalar != bool(vec[idx]):
                    mismatches += 1
                    if first_bad is None:
                        first_bad = (idx, is_long, min_tfs, scalar, bool(vec[idx]))
    total = n * 6
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
