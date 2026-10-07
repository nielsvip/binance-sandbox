"""Parity test: scalar cores == vec masks for both vetoes, >=10000 samples, both sides."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from vec_decisions.check_exit_candidates_crypto__htf_wt_vetoes import (
    check_trend_regime_veto, check_trend_regime_veto_vec,
    check_htf_exit_veto, check_htf_exit_veto_vec)


class _Cfg:
    TREND_REGIME_VETO_ENABLED = True
    HTF_EXIT_VETO_ENABLED = True
    HTF_EXIT_VETO_MAX_LOSS_PCT = 2.0
    HTF_EXIT_VETO_MIN_ALIGNED = 2


def _arrs(rng, n):
    return tuple(rng.uniform(-50, 50, n) for _ in range(6))


def _run(seed, n, is_long, cfg):
    rng = np.random.default_rng(seed)
    a1, b1, a4, b4, aD, bD = _arrs(rng, n)
    gain = rng.uniform(-5, 5, n)
    v_tr = check_trend_regime_veto_vec(cfg, a1, b1, a4, b4, aD, bD, is_long)
    v_htf = check_htf_exit_veto_vec(cfg, a1, b1, a4, b4, aD, bD, is_long, gain)
    mism = 0
    for i in range(n):
        ind = {"wt1_1h": a1[i], "wt2_1h": b1[i], "wt1_4h": a4[i], "wt2_4h": b4[i],
               "wt1_D": aD[i], "wt2_D": bD[i]}
        s_tr = check_trend_regime_veto(cfg, ind, is_long)
        s_htf = check_htf_exit_veto(cfg, ind, is_long, float(gain[i]))
        if bool(s_tr) != bool(v_tr[i]) or bool(s_htf) != bool(v_htf[i]):
            mism += 1
    return mism, int(v_tr.sum()), int(v_htf.sum())


def main():
    cfg = _Cfg()
    tm = ttr = thtf = tn = 0
    for is_long in (True, False):
        for seed in range(3):
            m, tr, htf = _run(seed, 6000, is_long, cfg)
            tm += m; ttr += tr; thtf += htf; tn += 6000
    print(f"samples={tn} trend_veto_fires={ttr} htf_veto_fires={thtf} mismatches={tm}")
    print("PARITY_PASS" if tm == 0 else "PARITY_FAIL")
    if tm: sys.exit(1)


if __name__ == "__main__":
    main()
