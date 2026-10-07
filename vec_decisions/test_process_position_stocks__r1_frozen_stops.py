"""Parity test: scalar == vec for R1 breach + FROZEN_ACT_STOP over >=10000 samples."""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.process_position_stocks__r1_frozen_stops import (
    _r1_breach_fires, check_r1_breach_vec,
    _frozen_act_fires, check_frozen_act_stop_vec, _frozen_floor_pct,
)


class _Cfg:
    R1_DC_LOW4_3M_EMERGENCY_ENABLED = True
    FROZEN_ACTIVATION_STOP_ENABLED = True
    FROZEN_ABSOLUTE_FLOOR_PCT_TRADIER = -8.0


def run(n=15000, seed=31):
    rng = np.random.default_rng(seed)
    cfg = _Cfg()
    total = 0
    for is_long in (True, False):
        # R1 breach — stop array includes 0 (inactive) and crossing values
        p = rng.uniform(50, 150, n)
        stop = rng.uniform(0, 150, n)
        stop[:200] = 0.0  # inactive
        stop[200:400] = p[200:400]  # exact-equality edge (<=/>=)
        s = np.array([_r1_breach_fires(float(p[i]), float(stop[i]), is_long) for i in range(n)], dtype=bool)
        v = check_r1_breach_vec(cfg, p, stop, is_long)
        m = int(np.sum(s != np.asarray(v, dtype=bool))); total += m
        print(f"R1 is_long={is_long}: fires={int(np.sum(v))} mism={m}")
        # FROZEN — frozen level array with NaN (=None) for some bars; gain spans floor
        g = rng.uniform(-12, 5, n)
        fl = rng.uniform(50, 150, n)
        fl[:300] = np.nan  # None case
        floor_pct = _frozen_floor_pct(cfg)
        sf = np.array([_frozen_act_fires(float(p[i]), float(g[i]), (None if np.isnan(fl[i]) else float(fl[i])), is_long, floor_pct)[0] for i in range(n)], dtype=bool)
        vf = check_frozen_act_stop_vec(cfg, p, g, fl, is_long)
        mf = int(np.sum(sf != np.asarray(vf, dtype=bool))); total += mf
        print(f"FROZEN is_long={is_long}: fires={int(np.sum(vf))} mism={mf}")
    print(f"TOTAL_MISMATCHES={total}")
    assert total == 0, f"PARITY FAIL: {total}"
    print("PARITY_OK")
    return total


if __name__ == "__main__":
    run()
