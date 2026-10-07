"""Random-input parity tests for the check_exit_candidates_stocks__* modules.

For each branch: build random per-bar indicator arrays (with zeros/edge values to exercise
guards), run the numpy vec mask, then for every sample reconstruct the live-shaped
indicators dict, run the scalar wrapper, and assert scalar-fire == vec-mask (0 mismatches),
and also assert the scalar wrapper agrees with the pure core directly. >=10000 samples per
branch, both LONG and SHORT.

Run: /opt/anaconda3/envs/binance_env/bin/python vec_decisions/test_check_exit_candidates_stocks.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from vec_decisions.check_exit_candidates_stocks__noloss_bb1h import (
    check_noloss_bb1h, check_noloss_bb1h_vec)
from vec_decisions.check_exit_candidates_stocks__wt_crossunder_final import (
    check_wt_crossunder_final, check_wt_crossunder_final_vec)
from vec_decisions.check_exit_candidates_stocks__ibs_exhaustion import (
    check_ibs_exhaustion, check_ibs_exhaustion_vec)
from vec_decisions.check_exit_candidates_stocks__k5m_real_drop_dc import (
    check_k5m_real_drop_dc, check_k5m_real_drop_dc_vec)
from vec_decisions.check_exit_candidates_stocks__struct_lh_hl_5m import (
    check_struct_lh_hl_5m, check_struct_lh_hl_5m_vec)
from vec_decisions.check_exit_candidates_stocks__options_d_reversal import (
    check_options_d_reversal, check_options_d_reversal_vec)
from vec_decisions.check_exit_candidates_stocks__technical_breakdown_15m import (
    check_extreme_tp, check_extreme_tp_vec, check_technical_breakdown_15m,
    check_technical_breakdown_15m_vec)
from vec_decisions.check_exit_candidates_stocks__stdev_bb_rz_exit import (
    check_stdev_bb_rz_exit, check_stdev_bb_rz_exit_vec)
from vec_decisions.check_exit_candidates_stocks__stdev_reject_exit import (
    check_stdev_reject_exit, check_stdev_reject_exit_vec)
from vec_decisions.check_exit_candidates_stocks__structural_range_shift import (
    check_structural_range_shift, check_structural_range_shift_vec, _FIELD_MAP)
from vec_decisions.check_exit_candidates_stocks__htf_w_reversal import (
    check_htf_w_reversal, check_htf_w_reversal_vec)
from vec_decisions.check_exit_candidates_stocks__wt_exit_tf_against import (
    check_wt_exit_confirm_count, check_wt_exit_confirm_count_vec,
    check_wt_exit_tfs_varfix, check_wt_exit_tfs_varfix_vec)
from vec_decisions.check_exit_candidates_stocks__struct_break_dc_cascade import (
    check_struct_break_dc_cascade, check_struct_break_dc_cascade_vec, _select_tier)
from vec_decisions.check_exit_candidates_stocks__htf_quick_tp import (
    check_htf_quick_tp, check_htf_quick_tp_vec)

N = 6000
RESULTS = []


def _z(rng, n, lo, hi, zero_p=0.1):
    a = rng.uniform(lo, hi, n)
    return np.where(rng.random(n) < zero_p, 0.0, a)


class Cfg:
    NOLOSS_BB1H_GATE_ENABLED = True
    NOLOSS_MIN_PROFIT_PCT_TRADIER = 3.0
    STDEV_BB_RZ_EXIT_ENABLED = True
    STDEV_BB_RZ_EXIT_TF = "D"
    STDEV_REJECT_EXIT_ENABLED = True
    STDEV_REJECT_EXIT_TF = "D"
    STDEV_REJECT_EXIT_ZONE = 0.80
    STDEV_REJECT_EXIT_RETURN = 0.65
    STRUCTURAL_RANGE_SHIFT_EXIT = True
    STRUCTURAL_RANGE_SHIFT_TF = "bb_1h"
    STRUCTURAL_RANGE_SHIFT_PROXIMITY_BPS = 100.0
    STRUCTURAL_RANGE_SHIFT_K_HIGH = 80.0
    STRUCTURAL_RANGE_SHIFT_K_LOW = 20.0
    HTF_W_REVERSAL_EXIT_TRADIER_ENABLED = True
    HTF_W_REVERSAL_EXIT_TRADIER_REQUIRE_D = True
    WT_EXIT_VETO_ENABLED_TRADIER = True
    WT_EXIT_TFS_TRADIER = "5m+15m+1h+4h+D"
    WT_EXIT_MIN_TFS_TRADIER = 4


def _record(name, mism, fires, n):
    RESULTS.append((name, mism, fires, n))
    assert mism == 0, f"{name}: {mism} mismatches over {n}"


def t_noloss_bb1h(cfg):
    for is_long in (True, False):
        rng = np.random.default_rng(1 if is_long else 2)
        p = _z(rng, N, 0, 200)
        bu = _z(rng, N, 0, 200)
        bl = _z(rng, N, 0, 200)
        gain = rng.uniform(-5, 5, N)
        vec = check_noloss_bb1h_vec(cfg, p, bu, bl, is_long)
        mism = 0
        fires = 0
        for k in range(N):
            ind = {"bb_upper_1h": bu[k], "bb_lower_1h": bl[k]}
            s, _ = check_noloss_bb1h(cfg, ind, float(gain[k]), float(p[k]), is_long)
            # vec excludes the gain<0 state gate; apply it to compare
            v = bool(vec[k]) and (gain[k] < 0)
            mism += int(bool(s) != v)
            fires += int(bool(s))
        _record(f"noloss_bb1h_{'L' if is_long else 'S'}", mism, fires, N)


def t_wt_crossunder(cfg):
    for is_long in (True, False):
        rng = np.random.default_rng(10 if is_long else 11)
        a = lambda lo=-100, hi=100: rng.uniform(lo, hi, N)
        w15, w25 = a(), a()
        w115, w215 = a(), a()
        w11h, w21h = a(), a()
        w14h, w24h = a(), a()
        w1D, w2D = a(), a()
        vec = check_wt_crossunder_final_vec(cfg, w15, w25, w115, w215, w11h, w21h, w14h, w24h, w1D, w2D, is_long)
        mism = 0
        fires = 0
        for k in range(N):
            ind = {"wt1_5m": w15[k], "wt2_5m": w25[k], "wt1_15m": w115[k], "wt2_15m": w215[k],
                   "wt1_1h": w11h[k], "wt2_1h": w21h[k], "wt1_4h": w14h[k], "wt2_4h": w24h[k],
                   "wt1_D": w1D[k], "wt2_D": w2D[k]}
            s, _ = check_wt_crossunder_final(cfg, ind, 1.0, 100.0, is_long)
            mism += int(bool(s) != bool(vec[k]))
            fires += int(bool(s))
        _record(f"wt_crossunder_{'L' if is_long else 'S'}", mism, fires, N)


def t_ibs(cfg):
    # test the per-bar vec core (single bar series); gain gate + 5m/15m precedence are caller-layered
    for is_long in (True, False):
        rng = np.random.default_rng(20 if is_long else 21)
        h = _z(rng, N, 0, 100)
        lo = _z(rng, N, 0, 100)
        c = _z(rng, N, 0, 100)
        vec = check_ibs_exhaustion_vec(cfg, h, lo, c, is_long)
        mism = 0
        fires = 0
        for k in range(N):
            ind = {"high_5m_prev": h[k], "low_5m_prev": lo[k], "close_5m_prev": c[k]}
            # wrapper with gain 2.0 ensures gain>1.0; only 5m bar matters (15m fields absent->no fire)
            s, _ = check_ibs_exhaustion(cfg, ind, 2.0, is_long)
            mism += int(bool(s) != bool(vec[k]))
            fires += int(bool(s))
        _record(f"ibs_{'L' if is_long else 'S'}", mism, fires, N)


def t_k5m(cfg):
    for is_long in (True, False):
        rng = np.random.default_rng(30 if is_long else 31)
        p = _z(rng, N, 0, 200)
        dl = _z(rng, N, 0, 200)
        dh = _z(rng, N, 0, 200)
        k = rng.uniform(0, 100, N)
        gain = rng.uniform(2, 6, N)  # mix below/above noloss to exercise the state gate
        vec = check_k5m_real_drop_dc_vec(cfg, p, dl, dh, k, is_long)
        mism = 0
        fires = 0
        for j in range(N):
            ind = {"dc_low_5m": dl[j], "dc_high_5m": dh[j], "stoch_k_5m": k[j]}
            s, _ = check_k5m_real_drop_dc(cfg, ind, float(gain[j]), float(p[j]), is_long)
            v = bool(vec[j]) and (gain[j] >= cfg.NOLOSS_MIN_PROFIT_PCT_TRADIER)  # caller layers gain gate
            mism += int(bool(s) != v)
            fires += int(bool(s))
        _record(f"k5m_{'L' if is_long else 'S'}", mism, fires, N)


def t_struct_lh_hl(cfg):
    for is_long in (True, False):
        rng = np.random.default_rng(40 if is_long else 41)
        lo = _z(rng, N, 0, 100)
        lop = _z(rng, N, 0, 100)
        hi = _z(rng, N, 0, 100)
        hip = _z(rng, N, 0, 100)
        k = rng.uniform(0, 100, N)
        kp = rng.uniform(0, 100, N)
        vec = check_struct_lh_hl_5m_vec(cfg, lo, lop, hi, hip, k, kp, is_long)
        mism = 0
        fires = 0
        for j in range(N):
            ind = {"low_5m": lo[j], "low_5m_prev": lop[j], "high_5m": hi[j], "high_5m_prev": hip[j],
                   "stoch_k_5m": k[j], "stoch_k_5m_prev": kp[j]}
            s, _ = check_struct_lh_hl_5m(cfg, ind, 5.0, 30.0, 2, is_long)  # gain/hold/exit gates pass
            mism += int(bool(s) != bool(vec[j]))
            fires += int(bool(s))
        _record(f"struct_lh_hl_{'L' if is_long else 'S'}", mism, fires, N)


def t_options(cfg):
    for is_long in (True, False):
        rng = np.random.default_rng(50 if is_long else 51)
        w1 = rng.uniform(-100, 100, N)
        w2 = rng.uniform(-100, 100, N)
        cross = rng.integers(0, 3, N)  # 0 none,1 BEAR,2 BULL
        ha = rng.integers(0, 3, N)  # 0 none,1 red,2 green
        vec = check_options_d_reversal_vec(cfg, w1, w2, cross, ha, is_long)
        cmap = {0: "", 1: "BEAR", 2: "BULL"}
        hmap = {0: "neutral", 1: "red", 2: "green"}
        mism = 0
        fires = 0
        for j in range(N):
            ind = {"wt1_D": w1[j], "wt2_D": w2[j], "wt_cross_D": cmap[int(cross[j])], "ha_D": hmap[int(ha[j])]}
            s, _ = check_options_d_reversal(cfg, ind, 1.0, is_long)
            mism += int(bool(s) != bool(vec[j]))
            fires += int(bool(s))
        _record(f"options_drev_{'L' if is_long else 'S'}", mism, fires, N)


def t_extreme_tp(cfg):
    for is_long in (True, False):
        rng = np.random.default_rng(60 if is_long else 61)
        p = rng.uniform(0, 200, N)
        dh = rng.uniform(0, 200, N)
        dl = rng.uniform(0, 200, N)
        r = rng.uniform(0, 100, N)
        gain = rng.uniform(1.6, 5, N)  # > 1.5
        vec = check_extreme_tp_vec(cfg, p, dh, dl, r, is_long)
        mism = 0
        fires = 0
        for j in range(N):
            ind = {"dc_high_1h": dh[j], "dc_low_1h": dl[j], "rsi_15m": r[j]}
            s, _ = check_extreme_tp(cfg, ind, float(gain[j]), float(p[j]), is_long)
            v = bool(vec[j]) and (gain[j] > 1.5)
            mism += int(bool(s) != v)
            fires += int(bool(s))
        _record(f"extreme_tp_{'L' if is_long else 'S'}", mism, fires, N)


def t_tech_breakdown(cfg):
    for is_long in (True, False):
        rng = np.random.default_rng(70 if is_long else 71)
        r = rng.uniform(0, 100, N)
        k = rng.uniform(0, 100, N)
        d = rng.uniform(0, 100, N)
        ha = rng.integers(0, 3, N)
        gain = rng.uniform(3, 6, N)  # >= noloss
        vec = check_technical_breakdown_15m_vec(cfg, r, k, d, ha, is_long)
        hmap = {0: "neutral", 1: "red", 2: "green"}
        mism = 0
        fires = 0
        for j in range(N):
            ind = {"rsi_15m": r[j], "stoch_k_15m": k[j], "stoch_d_15m": d[j], "ha_15m": hmap[int(ha[j])]}
            s, _ = check_technical_breakdown_15m(cfg, ind, float(gain[j]), is_long)
            v = bool(vec[j]) and (gain[j] >= cfg.NOLOSS_MIN_PROFIT_PCT_TRADIER)
            mism += int(bool(s) != v)
            fires += int(bool(s))
        _record(f"tech_breakdown_{'L' if is_long else 'S'}", mism, fires, N)


def t_stdev_bb_rz(cfg):
    for is_long in (True, False):
        rng = np.random.default_rng(80 if is_long else 81)
        now = rng.uniform(-0.2, 1.2, N)
        prev = rng.uniform(-0.2, 1.2, N)
        vel = rng.uniform(-5, 5, N)
        vec = check_stdev_bb_rz_exit_vec(cfg, now, prev, vel, is_long)
        tf = cfg.STDEV_BB_RZ_EXIT_TF
        mism = 0
        fires = 0
        for j in range(N):
            ind = {f"bb_pct_b_{tf}": now[j], f"bb_pct_b_{tf}_prev": prev[j], "wt_velocity_1h": vel[j]}
            s, _ = check_stdev_bb_rz_exit(cfg, ind, 1.0, is_long)
            mism += int(bool(s) != bool(vec[j]))
            fires += int(bool(s))
        _record(f"stdev_bb_rz_{'L' if is_long else 'S'}", mism, fires, N)


def t_stdev_reject(cfg):
    for is_long in (True, False):
        rng = np.random.default_rng(90 if is_long else 91)
        now = rng.uniform(-0.2, 1.2, N)
        prev = rng.uniform(-0.2, 1.2, N)
        vel = rng.uniform(-5, 5, N)
        vec = check_stdev_reject_exit_vec(cfg, now, prev, vel, is_long)
        tf = cfg.STDEV_REJECT_EXIT_TF
        mism = 0
        fires = 0
        for j in range(N):
            ind = {f"bb_pct_b_{tf}": now[j], f"bb_pct_b_{tf}_prev": prev[j], "wt_velocity_1h": vel[j]}
            s, _ = check_stdev_reject_exit(cfg, ind, 1.0, is_long)
            mism += int(bool(s) != bool(vec[j]))
            fires += int(bool(s))
        _record(f"stdev_reject_{'L' if is_long else 'S'}", mism, fires, N)


def t_srs(cfg):
    hk, lk = _FIELD_MAP[cfg.STRUCTURAL_RANGE_SHIFT_TF]
    for is_long in (True, False):
        rng = np.random.default_rng(100 if is_long else 101)
        # Construct so the fire path is actually exercised: band edges near price, entry
        # outside the band, K's spanning the extremes. Still random + zeros to hit guards.
        hi = _z(rng, N, 50, 150)
        lo = _z(rng, N, 50, 150)
        if is_long:
            p = hi * rng.uniform(0.99, 1.02, N)  # near the top band
            e = hi * rng.uniform(0.98, 1.10, N)  # often > srs_high
        else:
            p = lo * rng.uniform(0.98, 1.01, N)
            e = lo * rng.uniform(0.90, 1.02, N)  # often < srs_low
        k1 = rng.uniform(0, 100, N)
        k15 = rng.uniform(0, 100, N)
        k5 = rng.uniform(0, 100, N)
        k5p = rng.uniform(0, 100, N)
        vec = check_structural_range_shift_vec(cfg, e, p, hi, lo, k1, k15, k5, k5p, is_long)
        mism = 0
        fires = 0
        for j in range(N):
            ind = {hk: hi[j], lk: lo[j], "stoch_k_1h": k1[j], "stoch_k_15m": k15[j],
                   "stoch_k_5m": k5[j], "stoch_k_5m_prev": k5p[j]}
            s, _ = check_structural_range_shift(cfg, ind, float(e[j]), float(p[j]), is_long)
            mism += int(bool(s) != bool(vec[j]))
            fires += int(bool(s))
        _record(f"srs_{'L' if is_long else 'S'}", mism, fires, N)


def t_htf_w_rev(cfg):
    for is_long in (True, False):
        rng = np.random.default_rng(110 if is_long else 111)
        w1w = _z(rng, N, -100, 100, 0.15)
        w2w = _z(rng, N, -100, 100, 0.15)
        w1d = _z(rng, N, -100, 100, 0.15)
        w2d = _z(rng, N, -100, 100, 0.15)
        vec = check_htf_w_reversal_vec(cfg, w1w, w2w, w1d, w2d, is_long)
        mism = 0
        fires = 0
        for j in range(N):
            ind = {"wt1_W": w1w[j], "wt2_W": w2w[j], "wt1_D": w1d[j], "wt2_D": w2d[j]}
            s, _ = check_htf_w_reversal(cfg, ind, 1.0, is_long)  # gain>0 passes
            mism += int(bool(s) != bool(vec[j]))
            fires += int(bool(s))
        _record(f"htf_w_rev_{'L' if is_long else 'S'}", mism, fires, N)


def t_wt_against(cfg):
    tfs = ["5m", "15m", "1h", "4h", "D"]
    for is_long in (True, False):
        rng = np.random.default_rng(120 if is_long else 121)
        w1 = [rng.uniform(-100, 100, N) for _ in tfs]
        w2 = [rng.uniform(-100, 100, N) for _ in tfs]
        # confirm count (4 TFs) parity
        vec_cnt = check_wt_exit_confirm_count_vec(cfg, w1[:4], w2[:4], is_long)
        vec_var = check_wt_exit_tfs_varfix_vec(cfg, w1, w2, is_long)
        mism = 0
        fires = 0
        for j in range(N):
            ind = {}
            for ti, tf in enumerate(tfs):
                ind[f"wt1_{tf}"] = w1[ti][j]
                ind[f"wt2_{tf}"] = w2[ti][j]
            cnt = check_wt_exit_confirm_count(cfg, ind, is_long)
            mism += int(cnt != int(vec_cnt[j]))
            sv, _ = check_wt_exit_tfs_varfix(cfg, ind, 1.0, is_long)
            mism += int(bool(sv) != bool(vec_var[j]))
            fires += int(bool(sv))
        _record(f"wt_against_{'L' if is_long else 'S'}", mism, fires, N * 2)


def t_struct_cascade(cfg):
    fields = ["dc_low4_5m", "dc_high4_5m", "dc_low_5m", "dc_high_5m", "dc_low_1h", "dc_high_1h", "close_5m_prev"]
    for is_long in (True, False):
        rng = np.random.default_rng(130 if is_long else 131)
        p = rng.uniform(0, 200, N)
        gain = rng.uniform(3, 6, N)  # >= noloss
        ages = rng.choice([10.0, 40.0, 120.0], N)
        cols = {f: rng.uniform(0, 200, N) for f in fields}
        # caller pre-selects tier per bar (state seam), exactly as the scalar _select_tier does
        dc_stop = np.empty(N)
        buf = np.empty(N)
        conf = np.empty(N, dtype=bool)
        for j in range(N):
            ind = {f: cols[f][j] for f in fields}
            ds, b, c, _ = _select_tier(ind, float(ages[j]), float(p[j]), is_long)
            dc_stop[j], buf[j], conf[j] = ds, b, c
        vec = check_struct_break_dc_cascade_vec(cfg, p, dc_stop, buf, conf, is_long)
        mism = 0
        fires = 0
        for j in range(N):
            ind = {f: cols[f][j] for f in fields}
            s, _ = check_struct_break_dc_cascade(cfg, ind, float(gain[j]), float(p[j]), float(ages[j]), is_long)
            v = bool(vec[j]) and (gain[j] >= cfg.NOLOSS_MIN_PROFIT_PCT_TRADIER)
            mism += int(bool(s) != v)
            fires += int(bool(s))
        _record(f"struct_cascade_{'L' if is_long else 'S'}", mism, fires, N)


def t_htf_quick_tp(cfg):
    for is_long in (True, False):
        rng = np.random.default_rng(140 if is_long else 141)
        k1 = rng.uniform(0, 100, N)
        k4 = rng.uniform(0, 100, N)
        d4 = rng.uniform(0, 100, N)
        w14 = rng.uniform(-100, 100, N)
        w24 = rng.uniform(-100, 100, N)
        k5 = rng.uniform(0, 100, N)
        k5p = rng.uniform(0, 100, N)
        k15 = rng.uniform(0, 100, N)
        k15p = rng.uniform(0, 100, N)
        vec = check_htf_quick_tp_vec(cfg, k1, k4, d4, w14, w24, k5, k5p, k15, k15p, is_long)
        mism = 0
        fires = 0
        for j in range(N):
            ind = {"stoch_k_1h": k1[j], "stoch_k_4h": k4[j], "stoch_d_4h": d4[j], "wt1_4h": w14[j],
                   "wt2_4h": w24[j], "stoch_k_5m": k5[j], "stoch_k_5m_prev": k5p[j],
                   "stoch_k_15m": k15[j], "stoch_k_15m_prev": k15p[j]}
            s, _ = check_htf_quick_tp(cfg, ind, 5.0, is_long)  # gain>=noloss(1.0) passes
            mism += int(bool(s) != bool(vec[j]))
            fires += int(bool(s))
        _record(f"htf_quick_tp_{'L' if is_long else 'S'}", mism, fires, N)


def main():
    cfg = Cfg()
    for fn in (t_noloss_bb1h, t_wt_crossunder, t_ibs, t_k5m, t_struct_lh_hl, t_options,
               t_extreme_tp, t_tech_breakdown, t_stdev_bb_rz, t_stdev_reject, t_srs,
               t_htf_w_rev, t_wt_against, t_struct_cascade, t_htf_quick_tp):
        fn(cfg)
    total_mism = sum(r[1] for r in RESULTS)
    total_fires = sum(r[2] for r in RESULTS)
    total_n = sum(r[3] for r in RESULTS)
    for name, mism, fires, n in RESULTS:
        print(f"{name:24s} n={n:6d} fires={fires:6d} mism={mism}")
    print(f"TOTAL samples={total_n} vec_fires={total_fires} mismatches={total_mism}")
    if total_mism == 0:
        print("PARITY_PASS")
    else:
        print("PARITY_FAIL")
        sys.exit(1)


if __name__ == "__main__":
    main()
