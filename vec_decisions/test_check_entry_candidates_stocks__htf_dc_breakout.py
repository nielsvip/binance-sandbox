"""Parity: scalar core == vec mask for htf_dc_breakout over >=10000 random samples."""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.check_entry_candidates_stocks__htf_dc_breakout import (
    _htf_dc_breakout_fires,
    check_htf_dc_breakout_vec,
)


class _Cfg:
    HTF_DC_BREAKOUT_TRADIER_THRESHOLD_PCT = 0.5
    HTF_DC_BREAKOUT_TRADIER_REQUIRE_W_WT = True


def run(n=20000, seed=22):
    rng = np.random.default_rng(seed)
    cfg = _Cfg()
    total = 0
    for thr_pct in (0.0, 0.5):
        for require_w in (True, False):
            cfg.HTF_DC_BREAKOUT_TRADIER_THRESHOLD_PCT = thr_pct
            cfg.HTF_DC_BREAKOUT_TRADIER_REQUIRE_W_WT = require_w
            thr = thr_pct / 100.0
            for is_long in (True, False):
                dc = rng.uniform(0, 1000, n)
                p = rng.uniform(0, 1000, n)
                w1 = rng.uniform(-50, 50, n); w2 = rng.uniform(-50, 50, n)
                dc[:100] = 0.0; p[100:200] = 0.0
                w1[200:300] = 0.0; w2[200:300] = 0.0  # W not has
                # force some right at breakout boundary
                p[300:400] = dc[300:400] * (1.0 + thr)
                scalar = np.array([_htf_dc_breakout_fires(float(dc[i]), float(p[i]), float(w1[i]),
                                                          float(w2[i]), is_long, thr, require_w) for i in range(n)], dtype=bool)
                vec = check_htf_dc_breakout_vec(cfg, dc, p, w1, w2, is_long)
                mism = int(np.sum(scalar != vec)); total += mism
                print(f"thr={thr_pct} req_w={require_w} is_long={is_long}: fires={int(vec.sum())} mismatches={mism}")
    print(f"TOTAL_MISMATCHES={total}")
    assert total == 0, f"PARITY FAIL: {total}"
    print("PARITY_OK")
    return total


if __name__ == "__main__":
    run()
