"""Parity: scalar core == vec mask for stdev_breakout + stdev_bounce over >=10000 samples each."""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.check_entry_candidates_stocks__stdev_breakout_bounce import (
    _stdev_breakout_fires, _stdev_bounce_fires,
    check_stdev_breakout_vec, check_stdev_bounce_vec,
    _breakout_params, _bounce_params,
)


class _Cfg:
    STDEV_BREAKOUT_PCTB_LONG = 1.0
    STDEV_BREAKOUT_PCTB_SHORT = 0.0
    STDEV_BREAKOUT_RVOL_MIN = 1.2
    STDEV_BOUNCE_PCTB_LONG = 0.05
    STDEV_BOUNCE_PCTB_SHORT = 0.95
    STDEV_BOUNCE_RVOL_MIN = 1.2


def run(n=20000, seed=33):
    rng = np.random.default_rng(seed)
    cfg = _Cfg()
    total = 0
    bo = _breakout_params(cfg)
    bn = _bounce_params(cfg)
    for is_long in (True, False):
        pctb = rng.uniform(-0.2, 1.2, n)
        rvol = rng.uniform(0.0, 3.0, n)
        pctb[:100] = bo[0]; pctb[100:200] = bo[1]; rvol[200:300] = bo[2]
        sc_bo = np.array([_stdev_breakout_fires(float(pctb[i]), float(rvol[i]), is_long, *bo) for i in range(n)], dtype=bool)
        vc_bo = check_stdev_breakout_vec(cfg, pctb, rvol, is_long)
        m_bo = int(np.sum(sc_bo != vc_bo)); total += m_bo
        print(f"BREAKOUT is_long={is_long}: fires={int(vc_bo.sum())} mismatches={m_bo}")
        sc_bn = np.array([_stdev_bounce_fires(float(pctb[i]), float(rvol[i]), is_long, *bn) for i in range(n)], dtype=bool)
        vc_bn = check_stdev_bounce_vec(cfg, pctb, rvol, is_long)
        m_bn = int(np.sum(sc_bn != vc_bn)); total += m_bn
        print(f"BOUNCE   is_long={is_long}: fires={int(vc_bn.sum())} mismatches={m_bn}")
    print(f"TOTAL_MISMATCHES={total}")
    assert total == 0, f"PARITY FAIL: {total}"
    print("PARITY_OK")
    return total


if __name__ == "__main__":
    run()
