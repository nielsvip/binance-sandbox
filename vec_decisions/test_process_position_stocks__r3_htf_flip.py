"""Parity test: scalar core == vec mask for process_position_stocks R3_HTF_FLIP over >=10000 samples."""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.process_position_stocks__r3_htf_flip import _r3_htf_flip_fires, check_r3_htf_flip_vec


class _Cfg:
    R3_HTF_FLIP_EXIT_ENABLED = True
    R3_HTF_FLIP_4H_TIER_ENABLED = True


class _CfgNo4h(_Cfg):
    R3_HTF_FLIP_4H_TIER_ENABLED = False


def _run(cfg, n, rng):
    enable_4h = bool(cfg.R3_HTF_FLIP_4H_TIER_ENABLED)
    total = 0
    for is_long in (True, False):
        p = rng.uniform(50, 150, n)
        dcD = rng.uniform(0, 150, n)  # includes 0 (daily-invalid) region
        dcD[: n // 10] = 0.0
        w1D = rng.uniform(-30, 30, n)
        w2D = rng.uniform(-30, 30, n)
        w1W = rng.uniform(-30, 30, n)
        w2W = rng.uniform(-30, 30, n)
        e4 = rng.uniform(0, 150, n)
        e4[n // 10 : 2 * (n // 10)] = 0.0
        a4 = rng.uniform(0, 5, n)
        a4[2 * (n // 10) : 3 * (n // 10)] = 0.0
        w14 = rng.uniform(-30, 30, n)
        w24 = rng.uniform(-30, 30, n)
        scalar = np.array([_r3_htf_flip_fires(float(p[i]), float(dcD[i]), float(w1D[i]), float(w2D[i]), float(w1W[i]), float(w2W[i]), float(e4[i]), float(a4[i]), float(w14[i]), float(w24[i]), is_long, enable_4h)[0] for i in range(n)], dtype=bool)
        vec = check_r3_htf_flip_vec(cfg, p, dcD, w1D, w2D, w1W, w2W, e4, a4, w14, w24, is_long)
        mism = int(np.sum(scalar != np.asarray(vec, dtype=bool)))
        total += mism
        print(f"enable_4h={enable_4h} is_long={is_long}: n={n} fires={int(np.sum(vec))} mismatches={mism}")
    return total


def run(n=20000, seed=7):
    rng = np.random.default_rng(seed)
    t = _run(_Cfg(), n, rng) + _run(_CfgNo4h(), n, rng)
    print(f"TOTAL_MISMATCHES={t}")
    assert t == 0, f"PARITY FAIL: {t}"
    print("PARITY_OK")
    return t


if __name__ == "__main__":
    run()
