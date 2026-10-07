"""Parity: scalar core == vec mask for htf_w_m_align over >=10000 random samples."""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.check_entry_candidates_stocks__htf_w_m_align import (
    _htf_w_m_align_blocks,
    check_htf_w_m_align_blocks_vec,
)


class _Cfg:
    HTF_W_M_ALIGN_TRADIER_REQUIRED = 2


def run(n=20000, seed=11):
    rng = np.random.default_rng(seed)
    cfg = _Cfg()
    req = cfg.HTF_W_M_ALIGN_TRADIER_REQUIRED
    total = 0
    for req_v in (1, 2):
        cfg.HTF_W_M_ALIGN_TRADIER_REQUIRED = req_v
        for is_long in (True, False):
            w1W = rng.uniform(-50, 50, n); w2W = rng.uniform(-50, 50, n)
            w1M = rng.uniform(-50, 50, n); w2M = rng.uniform(-50, 50, n)
            # inject zeros (pre-cache pass-through) and ties
            w1W[:200] = 0.0; w2W[:200] = 0.0
            w1M[200:400] = 0.0; w2M[200:400] = 0.0
            w1W[400:450] = w2W[400:450]
            scalar = np.array([_htf_w_m_align_blocks(float(w1W[i]), float(w2W[i]), float(w1M[i]),
                                                     float(w2M[i]), is_long, req_v) for i in range(n)], dtype=bool)
            vec = check_htf_w_m_align_blocks_vec(cfg, w1W, w2W, w1M, w2M, is_long)
            mism = int(np.sum(scalar != vec)); total += mism
            print(f"req={req_v} is_long={is_long}: fires={int(vec.sum())} mismatches={mism}")
    print(f"TOTAL_MISMATCHES={total}")
    assert total == 0, f"PARITY FAIL: {total}"
    print("PARITY_OK")
    return total


if __name__ == "__main__":
    run()
