"""Parity test: scalar == vec for MTF_GR_WT_EXIT over >=10000 samples."""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.process_position_stocks__mtf_gr_wt_exit import _mtf_gr_wt_exit_fires, check_mtf_gr_wt_exit_vec


class _Cfg:
    MTF_GR_EXIT_GATE_ENABLED = True
    MTF_WT_CROSS_EXIT_ENABLED = True
    MTF_WT_CROSS_EXIT_TF = "1h"
    MTF_GR_EXIT_MIN_TFS = 3


def run(n=20000, seed=37):
    rng = np.random.default_rng(seed)
    cfg = _Cfg()
    min_tfs = int(cfg.MTF_GR_EXIT_MIN_TFS)
    total = 0
    for is_long in (True, False):
        # tf cross pair (1h) == first gr pair to stay faithful
        gr1 = [rng.uniform(-30, 30, n) for _ in range(4)]
        gr2 = [rng.uniform(-30, 30, n) for _ in range(4)]
        # inject zero wt1 edge (the `w1 != 0` guard) into TFs
        gr1[1][:200] = 0.0
        gr1[2][200:400] = 0.0
        wt1_tf = gr1[0]; wt2_tf = gr2[0]
        scalar = np.array([_mtf_gr_wt_exit_fires(float(wt1_tf[i]), float(wt2_tf[i]), [float(a[i]) for a in gr1], [float(a[i]) for a in gr2], is_long, min_tfs) for i in range(n)], dtype=bool)
        vec = check_mtf_gr_wt_exit_vec(cfg, wt1_tf, wt2_tf, gr1, gr2, is_long)
        m = int(np.sum(scalar != np.asarray(vec, dtype=bool))); total += m
        print(f"is_long={is_long}: fires={int(np.sum(vec))} mism={m}")
    print(f"TOTAL_MISMATCHES={total}")
    assert total == 0, f"PARITY FAIL: {total}"
    print("PARITY_OK")
    return total


if __name__ == "__main__":
    run()
