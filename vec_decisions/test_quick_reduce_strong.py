"""Random-input parity test for QUICK_REDUCE_STRONG (HLR_TOP_EXIT family).

Mirrors the pyramid parity test: assert the scalar path == the vectorized mask over many
random samples. Two parity assertions:
  A) core-vs-vec: the pure _quick_reduce_strong_fires() evaluated per-row == the numpy
     mask, with the side-resolved booleans built identically on both sides.
  B) wrapper-vs-vec: the full scalar check_quick_reduce_strong() (which reconstructs the
     live EXHAUST string from velocity+acceleration, divergence/peak from NPZ int8) ==
     the vec mask, end to end.
Run: /opt/anaconda3/envs/binance_env/bin/python vec_decisions/test_quick_reduce_strong.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vec_decisions.quick_reduce_strong import (  # noqa: E402
    check_quick_reduce_strong,
    check_quick_reduce_strong_vec,
    _quick_reduce_strong_fires,
    _quick_reduce_strong_thresholds,
    _reconstruct_mom,
    _is_exhaust,
    _div_against,
    _peak_bad,
)


class _Cfg:
    HLR_TOP_EXIT_ENABLED = True
    HLR_TOP_MIN_GAIN_PCT = 1.5
    HLR_TOP_VEL_1H_THRESH = -1.0
    HLR_TOP_VEL_4H_THRESH = 0.0
    HLR_TOP_VEL_D_THRESH = 0.0
    NOLOSS_MIN_PROFIT_PCT = 0.30
    HLR_TOP_MIN_TFS = 2


def _gen(n, seed):
    rng = np.random.default_rng(seed)
    return {
        "gain": rng.uniform(-2.0, 6.0, n),
        "v1h": rng.uniform(-5, 5, n),
        "v4h": rng.uniform(-5, 5, n),
        "vD": rng.uniform(-5, 5, n),
        "vW": rng.uniform(-5, 5, n),
        "a4h": rng.uniform(-5, 5, n),
        "aD": rng.uniform(-5, 5, n),
        "aW": rng.uniform(-5, 5, n),
        "d4h": rng.integers(-1, 2, n),
        "dD": rng.integers(-1, 2, n),
        "p4h": rng.integers(-1, 2, n),
    }


def _run(cfg, is_long, n=20000, seed=0):
    a = _gen(n, seed)
    min_gain, v1h_thr, v4h_thr, vD_thr, noloss_min, min_tfs = _quick_reduce_strong_thresholds(cfg)
    noloss_min = max(noloss_min, 0.3)
    vec = check_quick_reduce_strong_vec(
        cfg, a["gain"], is_long, a["v1h"], a["v4h"], a["vD"], a["vW"],
        a["a4h"], a["aD"], a["aW"], a["d4h"], a["dD"], a["p4h"],
    )
    core = np.zeros(n, dtype=bool)
    wrap = np.zeros(n, dtype=bool)
    for i in range(n):
        mom4 = _reconstruct_mom(float(a["a4h"][i]), float(a["v4h"][i]))
        momD = _reconstruct_mom(float(a["aD"][i]), float(a["vD"][i]))
        momW = _reconstruct_mom(float(a["aW"][i]), float(a["vW"][i]))
        exh4 = _is_exhaust(mom4, is_long)
        exhD = _is_exhaust(momD, is_long)
        exhW = _is_exhaust(momW, is_long)
        div4 = _div_against(int(a["d4h"][i]), is_long)
        divD = _div_against(int(a["dD"][i]), is_long)
        peak4 = _peak_bad(int(a["p4h"][i]), is_long)
        core[i] = _quick_reduce_strong_fires(
            float(a["gain"][i]), is_long, float(a["v1h"][i]), float(a["v4h"][i]),
            float(a["vD"][i]), float(a["vW"][i]), exh4, exhD, exhW, div4, divD, peak4,
            min_gain, v1h_thr, v4h_thr, vD_thr, noloss_min, min_tfs,
        )
        ind = {
            "wt_velocity_1h": a["v1h"][i], "wt_velocity_4h": a["v4h"][i],
            "wt_velocity_D": a["vD"][i], "wt_velocity_W": a["vW"][i],
            "wt_acceleration_4h": a["a4h"][i], "wt_acceleration_D": a["aD"][i],
            "wt_acceleration_W": a["aW"][i],
            "wt_divergence_4h": int(a["d4h"][i]), "wt_divergence_D": int(a["dD"][i]),
            "wt_peak_structure_4h": int(a["p4h"][i]),
        }
        wrap[i], _ = check_quick_reduce_strong(cfg, ind, float(a["gain"][i]), is_long)
    core_mis = int(np.sum(core != vec))
    wrap_mis = int(np.sum(wrap != vec))
    return core_mis, wrap_mis, int(np.sum(vec))


def main():
    total_mis = 0
    total_fires = 0
    total_n = 0
    for is_long in (True, False):
        for seed in range(3):
            cm, wm, fires = _run(_Cfg(), is_long, n=20000, seed=seed)
            total_mis += cm + wm
            total_fires += fires
            total_n += 20000
            print(f"is_long={is_long} seed={seed}: core_mismatch={cm} wrap_mismatch={wm} vec_fires={fires}/20000")
    print(f"\nTOTAL samples={total_n} mismatches={total_mis} total_vec_fires={total_fires}")
    assert total_mis == 0, f"PARITY FAIL: {total_mis} mismatches"
    print("PARITY OK: scalar core == scalar wrapper == vec mask over", total_n, "samples")


if __name__ == "__main__":
    main()
