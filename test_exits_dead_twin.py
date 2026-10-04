"""Twin tests for vec_decisions/twin_exits_dead.py (12 DEAD/STAGED switches).

Proves: (1) inert at shipped defaults, (2) fire/no-fire boundaries per family,
(3) vec-mask == scalar-twin agreement on every row of a synthetic grid.
"""
import numpy as np
import vec_decisions.twin_exits_dead as X


def _get(d):
    return lambda k, default: d.get(k, default)


def _safe(npz, key, n, default=0.0):
    a = npz.get(key)
    if a is None:
        return np.full(n, default, dtype=float)
    a = np.asarray(a, dtype=float)
    if len(a) != n:
        return np.full(n, default, dtype=float)
    return a


def _base_npz(n, **kw):
    z = {
        "ema_20_1h": np.full(n, 100.0), "atr_1h": np.full(n, 2.0),
        "bb_upper_1h": np.full(n, 104.0), "bb_lower_1h": np.full(n, 96.0),
        "open_1h": np.full(n, 100.0), "high_1h": np.full(n, 101.0),
        "low_1h": np.full(n, 99.0), "close_1h": np.full(n, 100.0),
        "k_1h": np.full(n, 50.0), "d_1h": np.full(n, 50.0),
        "k_1h_prev": np.full(n, 50.0), "d_1h_prev": np.full(n, 50.0),
        "k_4h": np.full(n, 50.0), "d_4h": np.full(n, 50.0),
        "k_4h_prev": np.full(n, 50.0), "d_4h_prev": np.full(n, 50.0),
        "rsi_1h": np.full(n, 50.0), "rsi_4h": np.full(n, 50.0),
        "high_4h": np.full(n, 101.0), "high_4h_prev": np.full(n, 100.0),
        "close_4h": np.full(n, 100.0), "close_4h_prev": np.full(n, 100.0),
        "high_1h_prev": np.full(n, 100.0), "close_1h_prev": np.full(n, 100.0),
        "k_5m": np.full(n, 50.0), "k_5m_prev": np.full(n, 50.0),
        "wt1_5m": np.full(n, 0.0), "wt2_5m": np.full(n, 0.0),
        "k_15m": np.full(n, 50.0), "k_15m_prev": np.full(n, 50.0),
        "wt1_15m": np.full(n, 0.0), "wt2_15m": np.full(n, 0.0),
        "dc_low_1h": np.full(n, 95.0), "dc_low_4h": np.full(n, 94.0),
        "wt_velocity_1h": np.full(n, 0.0), "wt_velocity_4h": np.full(n, 0.0),
        "wt_velocity_D": np.full(n, 0.0), "wt1_1h": np.full(n, 0.0),
        "wt2_1h": np.full(n, 0.0),
        "bb_pct_b_D": np.full(n, 0.5), "bb_pct_b_4h": np.full(n, 0.5),
        "relative_volume_D": np.full(n, 0.0), "relative_volume_4h": np.full(n, 0.0),
        "relative_volume_1h": np.full(n, 0.0), "relative_volume_15m": np.full(n, 0.0),
    }
    z.update(kw)
    return z


# ─── 1. inert at shipped defaults ───

def test_all_inert_at_defaults():
    n = 8
    npz = _base_npz(n)
    close = np.full(n, 100.0)
    g = _get({})
    assert X.bbkc_entry_pass_mask(npz, n, True, g, _safe, close) is None
    assert X.bbkc_entry_pass_mask(npz, n, False, g, _safe, close) is None
    assert X.bbkc_exit_mask(npz, n, True, g, _safe, close) is None
    assert X.bbkc_exit_mask(npz, n, False, g, _safe, close) is None
    assert X.wick_entry_pass_mask(npz, n, True, g, _safe) is None
    assert X.wick_entry_pass_mask(npz, n, False, g, _safe) is None
    assert X.wick_exit_mask(npz, n, True, g, _safe) is None
    assert X.wick_exit_mask(npz, n, False, g, _safe) is None
    assert X.mu_exit_mask(npz, n, True, "MU", g, _safe) is None
    assert X.mu_reentry_mask(npz, n, True, "MU", g, _safe, close) is None
    assert X.stdev_fail_mask(npz, n, True, g, _safe) is None
    assert X.stdev_fail_mask(npz, n, False, g, _safe) is None
    assert X.bbkc_entry_live_pass({}, 100.0, True, g) == (True, "")
    assert X.bbkc_exit_live_fire({}, 100.0, True, g) == (False, "")
    assert X.wick_entry_live_pass({}, True, g) == (True, "")
    assert X.wick_exit_live_fire({}, True, g) == (False, "")
    assert X.mu_exit_live_fire({}, True, "MU", 5.0, g) == (False, "")
    assert X.mu_reentry_live_fire({}, 100.0, True, "MU", 999.0, g) == (False, "")
    assert X.StdevFailLive().fire({}, True, "MU", g) == (False, "")


def test_velocity_off_is_inert():
    n = 6
    npz = _base_npz(n, wt_velocity_1h=np.full(n, -9.0))
    close = np.full(n, 100.0)
    assert X.velocity_wt_exit_mask(npz, n, True, _get({"EXIT_VELOCITY_WT_TFS": "OFF"}), _safe) is None
    assert X.velocity_wt_exit_mask(npz, n, True, _get({"EXIT_VELOCITY_WT_TFS": ""}), _safe) is None
    assert X.velocity_wt_exit_live_fire({"wt_velocity_1h": -9.0}, True, _get({"EXIT_VELOCITY_WT_TFS": "OFF"})) == (False, "")


def test_enabled_with_off_tf_is_inert():
    n = 4
    npz = _base_npz(n)
    close = np.full(n, 100.0)
    g = _get({"BBKC_ENTRY_ENABLED": True, "BBKC_ENTRY_TF": "OFF"})
    assert X.bbkc_entry_pass_mask(npz, n, True, g, _safe, close) is None
    g = _get({"WICK_REJECT_EXIT_ENABLED": True, "WICK_REJECT_EXIT_TF": "OFF"})
    assert X.wick_exit_mask(npz, n, True, g, _safe) is None


# ─── 2. boundaries ───

def test_bbkc_entry_long_short():
    n = 3
    g = _get({"BBKC_ENTRY_ENABLED": True, "BBKC_ENTRY_TF": "1h"})
    npz = _base_npz(n)
    close = np.array([105.0, 103.5, 100.0])
    m = X.bbkc_entry_pass_mask(npz, n, True, g, _safe, close)
    assert m.tolist() == [True, False, False]
    close_s = np.array([95.5, 96.5, 100.0])
    m = X.bbkc_entry_pass_mask(npz, n, False, g, _safe, close_s)
    assert m.tolist() == [True, False, False]
    assert X.bbkc_entry_live_pass({"kc_upper_1h": 103.0, "kc_middle_1h": 100.0, "kc_lower_1h": 97.0, "bb_upper_1h": 104.0, "bb_lower_1h": 96.0}, 105.0, True, g)[0] is True
    assert X.bbkc_entry_live_pass({"kc_upper_1h": 103.0, "kc_middle_1h": 100.0, "kc_lower_1h": 97.0, "bb_upper_1h": 104.0, "bb_lower_1h": 96.0}, 103.5, True, g)[0] is False
    assert X.bbkc_entry_live_pass({"ema_20_1h": 100.0, "atr_1h": 2.0, "bb_upper_1h": 104.0, "bb_lower_1h": 96.0}, 105.0, True, g)[0] is True
    assert X.bbkc_entry_live_pass({}, 105.0, True, g)[0] is True


def test_bbkc_exit_long_short():
    n = 3
    g = _get({"BBKC_EXIT_ENABLED": True, "BBKC_EXIT_TF": "1h"})
    npz = _base_npz(n)
    close = np.array([99.0, 100.0, 101.0])
    assert X.bbkc_exit_mask(npz, n, True, g, _safe, close).tolist() == [True, False, False]
    assert X.bbkc_exit_mask(npz, n, False, g, _safe, close).tolist() == [False, False, True]
    assert X.bbkc_exit_live_fire({"kc_middle_1h": 100.0}, 99.0, True, g)[0] is True
    assert X.bbkc_exit_live_fire({"kc_middle_1h": 100.0}, 100.0, True, g)[0] is False
    assert X.bbkc_exit_live_fire({}, 50.0, True, g) == (False, "")


def test_wick_entry_exit():
    n = 4
    ge = _get({"WICK_REJECT_ENTRY_ENABLED": True, "WICK_REJECT_ENTRY_TF": "1h"})
    gx = _get({"WICK_REJECT_EXIT_ENABLED": True, "WICK_REJECT_EXIT_TF": "1h"})
    o = np.array([100.0, 99.8, 100.0, 100.0])
    h = np.array([110.0, 100.5, 100.5, 100.0])
    l = np.array([99.0, 99.5, 90.0, 100.0])
    c = np.array([100.0, 100.2, 100.0, 100.0])
    npz = _base_npz(n, open_1h=o, high_1h=h, low_1h=l, close_1h=c)
    assert X.wick_entry_pass_mask(npz, n, True, ge, _safe).tolist() == [False, True, True, True]
    assert X.wick_entry_pass_mask(npz, n, False, ge, _safe).tolist() == [True, True, False, True]
    assert X.wick_exit_mask(npz, n, True, gx, _safe).tolist() == [True, False, False, False]
    assert X.wick_exit_mask(npz, n, False, gx, _safe).tolist() == [False, False, True, False]
    ind = {"high_1h": 110.0, "low_1h": 99.0, "open_1h": 100.0, "close_1h": 100.0}
    assert X.wick_entry_live_pass(ind, True, ge)[0] is False
    assert X.wick_entry_live_pass(ind, False, ge)[0] is True
    assert X.wick_exit_live_fire(ind, True, gx)[0] is True
    assert X.wick_exit_live_fire(ind, False, gx)[0] is False
    assert X.wick_entry_live_pass({}, True, ge)[0] is True
    assert X.wick_exit_live_fire({}, True, gx) == (False, "")


def test_mu_exit_gates_and_fire():
    n = 5
    g = _get({"MU_CORRECTION_EXIT_ENABLED": True})
    npz = _base_npz(n)
    assert X.mu_exit_mask(npz, n, True, "AAPL", g, _safe) is None
    assert X.mu_exit_mask(npz, n, False, "MU", g, _safe) is None
    assert X.mu_exit_live_fire({}, True, "AAPL", 5.0, g) == (False, "")
    assert X.mu_exit_live_fire({}, False, "MU", 5.0, g) == (False, "")
    hot = _base_npz(n, k_1h=np.full(n, 85.0), high_1h=np.full(n, 102.0), high_1h_prev=np.full(n, 101.0),
                    close_1h=np.full(n, 100.0), close_1h_prev=np.full(n, 101.0),
                    k_5m=np.full(n, 40.0), k_5m_prev=np.full(n, 50.0), k_15m=np.full(n, 40.0), k_15m_prev=np.full(n, 50.0))
    m = X.mu_exit_mask(hot, n, True, "MU", g, _safe)
    assert m.tolist() == [True] * n
    ind = {"k_1h": 85.0, "rsi_1h": 50.0, "k_4h": 50.0, "rsi_4h": 50.0, "high_1h": 102.0, "high_1h_prev": 101.0,
           "close_1h": 100.0, "close_1h_prev": 101.0, "high_4h": 0.0, "high_4h_prev": 0.0, "close_4h": 0.0, "close_4h_prev": 0.0,
           "k_5m": 40.0, "k_5m_prev": 50.0, "wt1_5m": 0.0, "wt2_5m": 0.0, "k_15m": 40.0, "k_15m_prev": 50.0, "wt1_15m": 0.0, "wt2_15m": 0.0}
    fire, reason = X.mu_exit_live_fire(ind, True, "MU", 1.5, g)
    assert fire is True and reason.startswith("MU_CORRECTION_PEAK_ROLLOVER")
    assert X.mu_exit_live_fire(dict(ind, k_5m=60.0), True, "MU", 1.5, g)[0] is False
    assert X.mu_exit_live_fire(ind, True, "MU", -0.5, _get({"MU_CORRECTION_EXIT_ENABLED": True, "MU_CORRECTION_MIN_GAIN_PCT": 0.5}))[0] is False


def test_mu_reentry_levels_crosses_cooldown():
    n = 4
    g = _get({"MU_CORRECTION_REENTRY_ENABLED": True})
    npz = _base_npz(n)
    assert X.mu_reentry_mask(npz, n, False, "MU", g, _safe, np.full(n, 100.0)) is None
    assert X.mu_reentry_mask(npz, n, True, "AAPL", g, _safe, np.full(n, 100.0)) is None
    close = np.array([95.5, 92.0, 100.0, 100.0])
    m = X.mu_reentry_mask(npz, n, True, "MU", g, _safe, close)
    assert m.tolist() == [True, True, False, False]
    x = _base_npz(n, k_1h=np.array([60.0, 40.0, 60.0, 40.0]), d_1h=np.array([55.0, 45.0, 55.0, 45.0]),
                  k_1h_prev=np.array([50.0, 50.0, 60.0, 40.0]), d_1h_prev=np.array([55.0, 45.0, 55.0, 45.0]))
    m = X.mu_reentry_mask(x, n, True, "MU", g, _safe, np.full(n, 100.0))
    assert m.tolist() == [True, False, False, False]
    ind = {"dc_low_4h": 94.0, "dc_low_1h": 95.0, "k_1h": 50.0, "d_1h": 50.0, "k_1h_prev": 50.0, "d_1h_prev": 50.0,
           "k_4h": 50.0, "d_4h": 50.0, "k_4h_prev": 50.0, "d_4h_prev": 50.0}
    assert X.mu_reentry_live_fire(ind, 95.5, True, "MU", 45.0, g)[0] is True
    assert X.mu_reentry_live_fire(ind, 95.5, True, "MU", 5.0, g)[0] is False
    assert X.mu_reentry_live_fire(ind, 99.0, True, "MU", 45.0, g)[0] is False


def test_stdev_fail_arm_fire_expire_disarm():
    n = 60
    g = _get({"STDEV_BREAKOUT_ENABLED": True})
    pctb = np.full(n, 0.5)
    pctb[5] = 1.30
    pctb[6:10] = 1.0
    pctb[10] = 0.70
    rvol = np.full(n, 1.5)
    npz = _base_npz(n, bb_pct_b_D=pctb, relative_volume_D=rvol)
    m = X.stdev_fail_mask(npz, n, True, g, _safe)
    assert m.tolist()[10] is True and sum(m.tolist()) == 1
    npz2 = _base_npz(n, bb_pct_b_D=np.full(n, 0.5), relative_volume_D=rvol)
    assert sum(X.stdev_fail_mask(npz2, n, True, g, _safe).tolist()) == 0
    pctb3 = np.full(n, 0.5)
    pctb3[5] = 1.30
    pctb3[6:59] = 1.0
    pctb3[59] = 0.10
    npz3 = _base_npz(n, bb_pct_b_D=pctb3, relative_volume_D=rvol)
    assert sum(X.stdev_fail_mask(npz3, n, True, g, _safe).tolist()) == 0
    pctb4 = np.full(n, 0.5)
    pctb4[5] = 1.30
    pctb4[6:10] = 1.0
    pctb4[10] = 0.70
    pctb4[12] = 0.60
    npz4 = _base_npz(n, bb_pct_b_D=pctb4, relative_volume_D=rvol)
    assert sum(X.stdev_fail_mask(npz4, n, True, g, _safe).tolist()) == 1
    live = X.StdevFailLive()
    assert live.fire({"bb_pct_b_D": 1.30, "relative_volume_D": 1.5}, True, "MU", g) == (False, "")
    fire, reason = live.fire({"bb_pct_b_D": 0.70, "relative_volume_D": 1.5}, True, "MU", g)
    assert fire is True and reason.startswith("STDEV_BREAKOUT_FAILED_D")
    assert live.fire({"bb_pct_b_D": 0.60, "relative_volume_D": 1.5}, True, "MU", g)[0] is False
    live2 = X.StdevFailLive()
    assert live2.fire({"bb_pct_b_D": 1.30, "relative_volume_D": 0.0}, True, "MU", g) == (False, "")
    assert live2.fire({"bb_pct_b_D": 0.10, "relative_volume_D": 0.0}, True, "MU", g)[0] is False


def test_velocity_multi_tf_or():
    n = 4
    g = _get({"EXIT_VELOCITY_WT_TFS": "1h,4h,D"})
    npz = _base_npz(n, wt_velocity_1h=np.array([1.0, -1.0, 0.0, 0.0]), wt_velocity_4h=np.array([0.0, 0.0, -2.0, 0.0]), wt_velocity_D=np.array([0.0, 0.0, 0.0, 3.0]))
    assert X.velocity_wt_exit_mask(npz, n, True, g, _safe).tolist() == [False, True, True, False]
    assert X.velocity_wt_exit_mask(npz, n, False, g, _safe).tolist() == [True, False, False, True]
    assert X.velocity_wt_exit_live_fire({"wt_velocity_1h": 1.0, "wt_velocity_4h": 0.0, "wt_velocity_D": 0.0}, True, g)[0] is False
    assert X.velocity_wt_exit_live_fire({"wt_velocity_1h": 1.0, "wt_velocity_4h": -2.0, "wt_velocity_D": 0.0}, True, g)[0] is True


# ─── 3. vec-formula agreement grid ───

def _grid_rows():
    rows = []
    for px in (96.0, 99.5, 100.0, 103.0, 105.0):
        for hi_extra in (0.5, 5.0, 12.0):
            for lo_extra in (0.5, 5.0, 12.0):
                for k in (30.0, 50.0, 85.0):
                    rows.append((px, hi_extra, lo_extra, k))
    return rows


def test_vec_scalar_agreement_grid():
    rows = _grid_rows()
    n = len(rows)
    px = np.array([r[0] for r in rows])
    hi = px + np.array([r[1] for r in rows])
    lo = px - np.array([r[2] for r in rows])
    kk = np.array([r[3] for r in rows])
    op = px.copy()
    npz = _base_npz(n, open_1h=op, high_1h=hi, low_1h=lo, close_1h=px, k_1h=kk)
    ge = _get({"BBKC_ENTRY_ENABLED": True, "BBKC_ENTRY_TF": "1h"})
    gx = _get({"BBKC_EXIT_ENABLED": True, "BBKC_EXIT_TF": "1h"})
    we = _get({"WICK_REJECT_ENTRY_ENABLED": True, "WICK_REJECT_ENTRY_TF": "1h"})
    wx = _get({"WICK_REJECT_EXIT_ENABLED": True, "WICK_REJECT_EXIT_TF": "1h"})
    for is_long in (True, False):
        be = X.bbkc_entry_pass_mask(npz, n, is_long, ge, _safe, px)
        bx = X.bbkc_exit_mask(npz, n, is_long, gx, _safe, px)
        wpe = X.wick_entry_pass_mask(npz, n, is_long, we, _safe)
        wxm = X.wick_exit_mask(npz, n, is_long, wx, _safe)
        for i in range(n):
            ind = {"kc_upper_1h": 103.0, "kc_middle_1h": 100.0, "kc_lower_1h": 97.0, "bb_upper_1h": 104.0, "bb_lower_1h": 96.0,
                   "high_1h": float(hi[i]), "low_1h": float(lo[i]), "open_1h": float(op[i]), "close_1h": float(px[i])}
            assert X.bbkc_entry_live_pass(ind, float(px[i]), is_long, ge)[0] == bool(be[i]), (is_long, i)
            assert X.bbkc_exit_live_fire(ind, float(px[i]), is_long, gx)[0] == bool(bx[i]), (is_long, i)
            assert X.wick_entry_live_pass(ind, is_long, we)[0] == bool(wpe[i]), (is_long, i)
            assert X.wick_exit_live_fire(ind, is_long, wx)[0] == bool(wxm[i]), (is_long, i)


def test_mu_vec_scalar_agreement_grid():
    n = 24
    rng = np.random.default_rng(7)
    k1 = rng.uniform(20, 95, n)
    rsi1 = rng.uniform(30, 80, n)
    hi = rng.uniform(98, 105, n)
    hip = rng.uniform(98, 105, n)
    cl = rng.uniform(97, 104, n)
    clp = rng.uniform(97, 104, n)
    k5 = rng.uniform(20, 80, n)
    k5p = rng.uniform(20, 80, n)
    k15 = rng.uniform(20, 80, n)
    k15p = rng.uniform(20, 80, n)
    npz = _base_npz(n, k_1h=k1, rsi_1h=rsi1, high_1h=hi, high_1h_prev=hip, close_1h=cl, close_1h_prev=clp,
                    k_5m=k5, k_5m_prev=k5p, k_15m=k15, k_15m_prev=k15p,
                    high_4h=np.zeros(n), high_4h_prev=np.zeros(n), close_4h=np.zeros(n), close_4h_prev=np.zeros(n))
    g = _get({"MU_CORRECTION_EXIT_ENABLED": True})
    m = X.mu_exit_mask(npz, n, True, "MU", g, _safe)
    for i in range(n):
        ind = {"k_1h": float(k1[i]), "rsi_1h": float(rsi1[i]), "k_4h": 50.0, "rsi_4h": 50.0, "high_1h": float(hi[i]),
               "high_1h_prev": float(hip[i]), "close_1h": float(cl[i]), "close_1h_prev": float(clp[i]),
               "high_4h": 0.0, "high_4h_prev": 0.0, "close_4h": 0.0, "close_4h_prev": 0.0,
               "k_5m": float(k5[i]), "k_5m_prev": float(k5p[i]), "wt1_5m": 0.0, "wt2_5m": 0.0,
               "k_15m": float(k15[i]), "k_15m_prev": float(k15p[i]), "wt1_15m": 0.0, "wt2_15m": 0.0}
        assert X.mu_exit_live_fire(ind, True, "MU", 0.0, g)[0] == bool(m[i]), i
