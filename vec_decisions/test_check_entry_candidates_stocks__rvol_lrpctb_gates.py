"""Parity: scalar cores == vec masks for rvol + lr_pctb_D gates over >=10000 samples each."""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.check_entry_candidates_stocks__rvol_lrpctb_gates import (
    _rvol_gate_blocks, _lr_pctb_d_blocks_short,
    check_rvol_gate_vec, check_lr_pctb_d_short_vec,
    _rvol_min, _lr_pctb_short_thr,
)


class _Cfg:
    RVOL_MOMENTUM_MIN = 1.5
    LR_PCTB_D_SHORT_THRESHOLD = 0.1


def run(n=20000, seed=77):
    rng = np.random.default_rng(seed)
    cfg = _Cfg()
    total = 0
    rmin = _rvol_min(cfg)
    rv = rng.uniform(-0.5, 3.0, n); rv[:200] = 0.0; rv[200:300] = rmin
    scr = np.array([_rvol_gate_blocks(float(rv[i]), rmin) for i in range(n)], dtype=bool)
    vcr = check_rvol_gate_vec(cfg, rv)
    mr = int(np.sum(scr != vcr)); total += mr
    print(f"RVOL: blocks={int(vcr.sum())} mismatches={mr}")
    thr = _lr_pctb_short_thr(cfg)
    lr = rng.uniform(-0.2, 1.2, n); lr[:100] = thr
    scl = np.array([_lr_pctb_d_blocks_short(float(lr[i]), thr) for i in range(n)], dtype=bool)
    vcl = check_lr_pctb_d_short_vec(cfg, lr)
    ml = int(np.sum(scl != vcl)); total += ml
    print(f"LR_PCTB_D short: blocks={int(vcl.sum())} mismatches={ml}")
    print(f"TOTAL_MISMATCHES={total}")
    assert total == 0, f"PARITY FAIL: {total}"
    print("PARITY_OK")
    return total


if __name__ == "__main__":
    run()
