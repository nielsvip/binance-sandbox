"""test_p0_crypto_b_twin.py — parity tests for vec_decisions/twin_p0_crypto_b.py.

Proves live-twin <=> vector agreement for P0 crypto batch B (52 switches).
Standalone: no engine import (engines are heavyweight + import absent sibling
modules); expectations are hardcoded from the cited v12 lines, and every twin
is cross-checked vec-vs-scalar on randomized bars.
"""
import numpy as np
import pytest

from vec_decisions import twin_p0_crypto_b as T


def G(**kw):
    base = {"MODE": "crypto", "BASE_TF": "15m"}
    base.update(kw)
    return lambda k, d=None: base.get(k, d)


def agree(vec, scalars):
    assert list(bool(x) for x in vec) == [bool(s) for s in scalars]


# ─── K3M_FLOOR (v12:9001-9025) ───
def test_k3m_default_path_long_short():
    g = G(K3M_FLOOR=30)
    assert T.k3m_floor_ok(g, True, {"stoch_k_15m": 69}) is True
    assert T.k3m_floor_ok(g, True, {"stoch_k_15m": 70}) is False
    assert T.k3m_floor_ok(g, False, {"stoch_k_15m": 31}) is True
    assert T.k3m_floor_ok(g, False, {"stoch_k_15m": 30}) is False


def test_k3m_enabled_branch_or_25():
    g = G(K3M_FLOOR_ENABLED=True, K3M_FLOOR=0)  # `or 25.0` (v12:9010)
    assert T.k3m_floor_ok(g, True, {"stoch_k_15m": 74}) is True
    assert T.k3m_floor_ok(g, True, {"stoch_k_15m": 75}) is False


def test_k3m_override_branch_uses_thr_verbatim():
    g = G(K3M_FLOOR=10)  # != default 30 -> override branch, floor=10
    assert T.k3m_floor_ok(g, True, {"stoch_k_15m": 89}) is True
    assert T.k3m_floor_ok(g, True, {"stoch_k_15m": 90}) is False


def test_k3m_source_chain_15m_1h_base():
    g = G(K3M_FLOOR_ENABLED=True, K3M_FLOOR=30)
    assert T.k3m_floor_ok(g, False, {"stoch_k_15m": 10, "stoch_k_1h": 99}) is False
    assert T.k3m_floor_ok(g, False, {"stoch_k_1h": 99}) is True
    assert T.k3m_floor_ok(g, False, {"k_15m": 99}) is True  # alias backfill


def test_k3m_vec_scalar_agreement():
    rng = np.random.default_rng(7)
    for en, floor, is_long in [(False, 30, True), (True, 25, False), (False, 10, True)]:
        g = G(K3M_FLOOR_ENABLED=en, K3M_FLOOR=floor)
        n = 24
        k15 = rng.uniform(0, 100, n)
        npz = {"stoch_k_15m": k15, "stoch_k_1h": rng.uniform(0, 100, n)}
        vec = T.k3m_floor_ok_vec(npz, n, is_long, g)
        agree(vec, [T.k3m_floor_ok(g, is_long, {"stoch_k_15m": float(k15[i]),
                                                "stoch_k_1h": float(npz["stoch_k_1h"][i])}) for i in range(n)])


# ─── K_ZONE veto (v12:9224-9231) ───
def test_kzone_veto_gates_and_bounds():
    assert T.kzone_veto_ok(G(), True, {"stoch_k_4h": 99}) is True  # gate off
    g = G(K_ZONE_VETO_ENABLED_TRADIER=True)  # lo=100/hi=0 via `or` (v12:9226-27)
    assert T.kzone_veto_ok(g, True, {"stoch_k_4h": 99}) is True
    assert T.kzone_veto_ok(g, False, {"stoch_k_4h": 1}) is True
    g2 = G(K_ZONE_VETO_ENABLED_TRADIER=True, K_ZONE_LONG_THRESHOLD_TRADIER=35,
           K_ZONE_SHORT_THRESHOLD_TRADIER=65)
    assert T.kzone_veto_ok(g2, True, {"stoch_k_4h": 34}) is True
    assert T.kzone_veto_ok(g2, True, {"stoch_k_4h": 35}) is False
    assert T.kzone_veto_ok(g2, False, {"stoch_k_4h": 66}) is True
    assert T.kzone_veto_ok(g2, False, {"stoch_k_4h": 65}) is False


def test_kzone_vec_scalar_agreement():
    rng = np.random.default_rng(11)
    g = G(K_ZONE_VETO_ENABLED_TRADIER=True, K_ZONE_LONG_THRESHOLD_TRADIER=35,
          K_ZONE_SHORT_THRESHOLD_TRADIER=65)
    for is_long in (True, False):
        n = 20
        k = rng.uniform(0, 100, n)
        agree(T.kzone_veto_ok_vec({"stoch_k_4h": k}, n, is_long, g),
              [T.kzone_veto_ok(g, is_long, {"stoch_k_4h": float(v)}) for v in k])


# ─── Stoch cross (v12:9248-9255) ───
def test_stoch_cross_off_inert_and_cross_logic():
    assert T.stoch_cross_ok(G(), True, {"stoch_k_15m": 1, "stoch_d_15m": 99}) is True
    g = G(STOCH_CROSS_ENTRY_TRADIER=True)
    assert T.stoch_cross_ok(g, True, {"stoch_k_15m": 60, "stoch_d_15m": 50}, k_prev=40) is True
    assert T.stoch_cross_ok(g, True, {"stoch_k_15m": 60, "stoch_d_15m": 50}, k_prev=60) is False
    assert T.stoch_cross_ok(g, False, {"stoch_k_15m": 40, "stoch_d_15m": 50}, k_prev=60) is True
    assert T.stoch_cross_ok(g, True, {"stoch_k_15m": 60, "stoch_d_15m": 50}) is False  # no prev: fail-closed


def test_stoch_cross_vec_scalar_agreement():
    rng = np.random.default_rng(13)
    g = G(STOCH_CROSS_ENTRY_TRADIER=True)
    for is_long in (True, False):
        n = 20
        k = rng.uniform(0, 100, n)
        d = rng.uniform(0, 100, n)
        npz = {"stoch_k_15m": k, "stoch_d_15m": d}
        vec = T.stoch_cross_ok_vec(npz, n, is_long, g)
        kp = np.roll(k, 1)
        kp[0] = k[0]
        agree(vec, [T.stoch_cross_ok(g, is_long, {"stoch_k_15m": float(k[i]), "stoch_d_15m": float(d[i])}, k_prev=float(kp[i])) for i in range(n)])


# ─── VWAP (v12:9126-9129) ───
def test_vwap_side_and_zero_gate():
    assert T.vwap_filter_ok(G(), True, {"vwap_D": 1, "close": 0}) is True
    g = G(VWAP_FILTER_ENABLED=True)
    assert T.vwap_filter_ok(g, True, {"vwap_D": 100}, close=101) is True
    assert T.vwap_filter_ok(g, True, {"vwap_D": 100}, close=99) is False
    assert T.vwap_filter_ok(g, False, {"vwap_D": 100}, close=99) is True
    assert T.vwap_filter_ok(g, True, {"vwap_D": 0}, close=1) is True  # sum>0 gate


def test_vwap_vec_scalar_agreement():
    g = G(VWAP_FILTER_ENABLED=True)
    n = 12
    v = np.linspace(90, 110, n)
    c = np.linspace(95, 105, n)
    npz = {"vwap_D": v, "close": c}
    for is_long in (True, False):
        agree(T.vwap_filter_ok_vec(npz, n, is_long, g),
              [T.vwap_filter_ok(g, is_long, {"vwap_D": float(v[i])}, close=float(c[i])) for i in range(n)])
    assert bool(np.all(T.vwap_filter_ok_vec({"vwap_D": np.zeros(4), "close": np.ones(4)}, 4, True, g)))


# ─── WT_DC stoch (v12:9343-9348) ───
def test_wtdc_stoch_thresholds_and_floor():
    g = G()
    assert T.wtdc_stoch_ok(g, True, {"stoch_k_15m": 39}) is True
    assert T.wtdc_stoch_ok(g, True, {"stoch_k_15m": 40}) is False
    assert T.wtdc_stoch_ok(g, False, {"stoch_k_15m": 61}) is True
    assert T.wtdc_stoch_ok(g, False, {"stoch_k_15m": 60}) is False
    assert T.wtdc_stoch_ok(g, True, {}) is True  # missing key fails open
    assert T.wtdc_stoch_ok(G(WT_DC_STOCH_TF="D"), True, {"stoch_k_D": 99}) is True  # D not in tuple


def test_wtdc_stoch_vec_scalar_agreement():
    rng = np.random.default_rng(17)
    g = G(WT_DC_STOCH_THRESHOLD_LONG=30, WT_DC_STOCH_THRESHOLD_SHORT=70)
    for is_long in (True, False):
        n = 20
        k = rng.uniform(0, 100, n)
        agree(T.wtdc_stoch_ok_vec({"stoch_k_15m": k}, n, is_long, g),
              [T.wtdc_stoch_ok(g, is_long, {"stoch_k_15m": float(v)}) for v in k])


# ─── WT_DC HTF (v12:9349-9388) ───
def test_wtdc_htf_baked_4h_d():
    ind = {"wt1_4h": 1, "wt2_4h": 0, "wt1_D": 1, "wt2_D": 0, "wt1_1h": 0, "wt2_1h": 0}
    assert T.wtdc_htf_ok(G(), True, ind) is True
    bad = dict(ind, wt1_D=0, wt2_D=1)
    assert T.wtdc_htf_ok(G(), True, bad) is False
    s = {"wt1_4h": -1, "wt2_4h": 1, "wt1_D": -2, "wt2_D": 3}
    assert T.wtdc_htf_ok(G(), False, s) is True
    assert T.wtdc_htf_ok(G(WT_DC_HTF_GATE="1h"), True, {"wt1_1h": 2, "wt2_1h": 1, "wt1_4h": 0, "wt2_4h": 9, "wt1_D": 0, "wt2_D": 9}) is True


def test_wtdc_htf_expanded_case_bug_and_or_mode():
    # Vec builds "wt1_1H" (upper) which never matches -> 1h/4h/15m expanded TFs
    # always skip to ones (pass). Only TF=D binds. Twin reproduces exactly.
    g = G(WT_DC_TF_HTF="1h", WT_DC_TF_HTF2="4h", WT_DC_HTF_GATE_MODE="AND")
    ind = {"wt1_4h": 1, "wt2_4h": 0, "wt1_D": 1, "wt2_D": 0,
           "wt1_1h": 0, "wt2_1h": 5}  # 1h against, but skipped -> pass
    assert T.wtdc_htf_ok(g, True, ind) is True
    # D binds: htf1=D-align, htf2=none->True; AND=D-align, OR=True
    gd = G(WT_DC_TF_HTF="D", WT_DC_TF_HTF2="none", WT_DC_HTF_GATE_MODE="AND")
    go = G(WT_DC_TF_HTF="D", WT_DC_TF_HTF2="none", WT_DC_HTF_GATE_MODE="OR")
    against = {"wt1_4h": 1, "wt2_4h": 0, "wt1_D": 0, "wt2_D": 5}
    assert T.wtdc_htf_ok(gd, True, against) is False  # base 4h_d already fails too
    ok_base = {"wt1_4h": 1, "wt2_4h": 0, "wt1_D": 1, "wt2_D": 0}
    assert T.wtdc_htf_ok(gd, True, ok_base) is True
    assert T.wtdc_htf_ok(go, True, against) is False  # base gate still binds (ANDed)


def test_wtdc_htf_vec_scalar_agreement():
    rng = np.random.default_rng(19)
    g = G(WT_DC_TF_HTF="1h", WT_DC_TF_HTF2="D")
    n = 16
    npz = {f"wt{a}_{t}": rng.uniform(-3, 3, n) for a in (1, 2) for t in ("1h", "4h", "D")}
    for is_long in (True, False):
        agree(T.wtdc_htf_ok_vec(npz, n, is_long, g),
              [T.wtdc_htf_ok(g, is_long, {k: float(v[i]) for k, v in npz.items()}) for i in range(n)])


# ─── WT_DC hard short (v12:9392-9414) ───
def test_wtdc_k5m_dcpos_final():
    assert T.wtdc_k5m_ok(G(), False, {"stoch_k_15m": 0}) is True  # gate off
    assert T.wtdc_k5m_ok(G(), True, {}) is True  # longs pass
    g = G(WT_DC_K5M_HARD_ENABLED=True, WT_DC_K5M_MIN_SHORT_HARD=20)
    assert T.wtdc_k5m_ok(g, False, {"stoch_k_15m": 20}) is True
    assert T.wtdc_k5m_ok(g, False, {"stoch_k_15m": 19.9}) is False
    assert T.wtdc_k5m_ok(g, False, {"stoch_k_5m": 10, "stoch_k_15m": 99}) is False  # 5m preferred
    assert T.wtdc_dcpos_ok(G(), False, {"dc_high_15m": 110, "dc_low_15m": 100, "close": 102}) is True
    assert T.wtdc_dcpos_ok(G(), False, {"dc_high_15m": 110, "dc_low_15m": 100, "close": 101}) is False
    assert T.wtdc_dcpos_ok(G(), False, {}) is True  # 0.5 fallback >= 0.20
    assert T.wtdc_final_ok(G(), False, {"final_score_norm_lt": 0.39}) is True
    assert T.wtdc_final_ok(G(), False, {"final_score_norm_lt": 0.40}) is False
    assert T.wtdc_final_ok(G(), False, {"trend_val_norm_lt": 0.1}) is True  # fallback key
    assert T.wtdc_final_ok(G(), False, {}) is False  # 0.5 default vetoes (vec-faithful)


def test_wtdc_hard_short_vec_agreement():
    n = 10
    g = G(WT_DC_K5M_HARD_ENABLED=True)
    npz = {"stoch_k_15m": np.linspace(0, 40, n), "dc_high_15m": np.full(n, 110.0),
           "dc_low_15m": np.full(n, 100.0), "close": np.linspace(100, 110, n),
           "final_score_norm_lt": np.linspace(0, 0.8, n),
           "wt1_1h": np.full(n, -1.0), "wt2_1h": np.full(n, 1.0),
           "wt1_4h": np.full(n, -1.0), "wt2_4h": np.full(n, 1.0),
           "wt1_D": np.full(n, -1.0), "wt2_D": np.full(n, 1.0)}
    vec = T.wtdc_hard_short_ok_vec(npz, n, False, g)
    sc = []
    for i in range(n):
        ind = {k: float(v[i]) for k, v in npz.items()}
        sc.append(T.wtdc_k5m_ok(g, False, ind) and T.wtdc_dcpos_ok(g, False, ind)
                  and T.wtdc_final_ok(g, False, ind))
    agree(vec, sc)  # bear 3/3 passes everywhere here
    assert bool(np.all(T.wtdc_hard_short_ok_vec(npz, n, True, g)))


# ─── WT_DC scorer + threshold (v12:9424-9449, scorer_vec:38-76,233-284) ───
def test_wtdc_cross_reader_precedence():
    assert T.wtdc_cross_1h({"wt_cross_1h": 2}) == 1
    assert T.wtdc_cross_1h({"wt_cross_1h": -0.5}) == -1
    assert T.wtdc_cross_1h({"wt_cross_1h": "bear"}) == -1
    assert T.wtdc_cross_1h({"wt_cross_1h": 0, "wt_cross_bull_1h": 1}) == 1
    assert T.wtdc_cross_1h({"wt_cross_bear_1h": 3}) == -1
    assert T.wtdc_cross_1h({}) == 0


def test_wtdc_multitf_score_exact():
    ind = {"wt1_D": 2, "wt2_D": 1, "wt1_4h": 3, "wt2_4h": 0,
           "dc_position_1h": 0.2, "stoch_k_5m": 30, "wt_cross_1h": 1}
    assert T.wtdc_multitf_score(True, ind) == 100.0
    assert T.wtdc_multitf_score(False, ind) == 0.0
    ind2 = {"wt1_D": 0, "wt2_D": 1, "wt1_4h": 0, "wt2_4h": 1,
            "dc_position_1h": 0.8, "stoch_k_5m": 70, "wt_cross_1h": -1}
    assert T.wtdc_multitf_score(False, ind2) == 100.0
    assert T.wtdc_multitf_score(True, {"wt1_D": 2}) == 0.0  # missing key invalid
    assert T.wtdc_multitf_score(True, dict(ind, wt1_D=float("nan"))) == 0.0


def test_wtdc_multitf_score_matches_vec_module():
    wv = pytest.importorskip("wt_dc_entry_scorer_vec")
    n = 6
    neur = {"wt1_D": np.array([2, 0, 2, 0, 2, 1.]),
            "wt2_D": np.array([1, 1, 1, 0, 1, 1.]),
            "wt1_4h": np.array([3, 0, 0, 1, 3, 0.]),
            "wt2_4h": np.array([0, 1, 1, 0, 0, 0.]),
            "dc_position_1h": np.array([0.2, 0.8, 0.2, 0.5, 0.9, 0.1]),
            "stoch_k_5m": np.array([30, 70, 70, 50, 20, 90.]),
            "wt_cross_1h": np.array([1, -1, 0, 1, -1, 0])}
    for is_long in (True, False):
        vec = np.asarray(wv.score_entry_multitf_vec(neur, is_long, n=n))
        for i in range(n):
            assert T.wtdc_multitf_score(is_long, {k: float(v[i]) for k, v in neur.items()}) == vec[i]


def test_wtdc_threshold_adjustment():
    assert T.wtdc_entry_threshold(G()) == (45.0, False)
    assert T.wtdc_entry_threshold(G(WT_DC_TF_ENTRY="15m")) == (35.0, False)
    assert T.wtdc_entry_threshold(G(WT_DC_TF_ENTRY="4h")) == (55.0, False)
    assert T.wtdc_entry_threshold(G(WT_DC_TF_ENTRY="d")) == (60.0, False)
    assert T.wtdc_entry_threshold(G(WT_DC_DETAILED_SCORER_ENABLED=True)) == (43.0, True)


def test_wtdc_live_entry_fire_gate_and_thr():
    assert T.wtdc_live_entry_fire(G(), True, {}) == (False, "")
    ind = {"wt1_D": 2, "wt2_D": 1, "wt1_4h": 3, "wt2_4h": 0,
           "dc_position_1h": 0.2, "stoch_k_5m": 30, "wt_cross_1h": 1}
    ok, why = T.wtdc_live_entry_fire(G(WT_DC_ENABLED=True), True, ind)
    assert ok and "100>=45" in why
    ok2, _ = T.wtdc_live_entry_fire(G(WT_DC_ENABLED=True, WT_DC_ENTRY_THRESHOLD=101), True, ind)
    assert ok2 is False


# ─── Daytrade entry (v12:8673-8679) ───
def test_daytrade_entry_or_gate_and_sides():
    assert T.daytrade_entry_fire(G(), True, {"dc_position_15m": 0}) == (False, "")
    g = G(TRADIER_DC_DAYTRADE_ENABLED=True, TRADIER_DC_DAYTRADE_REQUIRE_1H_EXPANSION=False,
          DC_POSITION_ENTRY_THRESHOLD=0.15)
    assert T.daytrade_entry_fire(g, True, {"dc_position_15m": 0.1})[0] is True
    assert T.daytrade_entry_fire(g, True, {"dc_position_15m": 0.15})[0] is False
    assert T.daytrade_entry_fire(g, False, {"dc_position_15m": 0.86})[0] is True
    assert T.daytrade_entry_fire(g, False, {"dc_position_15m": 0.85})[0] is False
    gt = G(TRADIER_DC_DAYTRADE_ENABLED=True, TRADIER_DC_DAYTRADE_REQUIRE_1H_EXPANSION=False,
           MODE="tradier", TRADIER_DC_POSITION_ENTRY_THRESHOLD=0.25)
    assert T.daytrade_entry_fire(gt, True, {"dc_position_15m": 0.2})[0] is True  # tradier thr


def test_daytrade_expansion_gate():
    g = G(TRADIER_DC_DAYTRADE_ENABLED=True, DC_POSITION_ENTRY_THRESHOLD=0.15)
    ind = {"dc_position_15m": 0.1, "dc_width_1h": 5.0, "dc_width_1h_prev": 4.0}
    assert T.daytrade_entry_fire(g, True, ind)[0] is True
    assert T.daytrade_entry_fire(g, True, {"dc_position_15m": 0.1, "dc_width_1h": 5.0})[0] is False


def test_daytrade_vec_scalar_agreement():
    rng = np.random.default_rng(23)
    g = G(DC_DAYTRADE_ENABLED=True, DC_POSITION_ENTRY_THRESHOLD=0.15)
    n = 20
    pos = rng.uniform(0, 1, n)
    w = rng.uniform(1, 9, n)
    npz = {"dc_position_15m": pos, "dc_width_1h": w}
    for is_long in (True, False):
        vec = T.daytrade_entry_fire_vec(npz, n, is_long, g)
        wp = np.roll(w, 1)
        wp[0] = w[0]
        agree(vec, [T.daytrade_entry_fire(g, is_long, {"dc_position_15m": float(pos[i]),
                                                        "dc_width_1h": float(w[i])},
                                            dc_width_prev=float(wp[i]))[0] for i in range(n)])


# ─── Satoshit exit (v12:10001-10015) ───
def test_satoshit_exit_votes():
    assert T.satoshit_exit_fire(G(), True, {"rsi_1h": 99, "stoch_k_15m": 99}) == (False, "")
    g = G(SATOSHIT_EXIT_ENABLED=True)
    assert T.satoshit_exit_fire(g, True, {"rsi_1h": 55, "stoch_k_15m": 60})[0] is True
    assert T.satoshit_exit_fire(g, True, {"rsi_1h": 54, "stoch_k_15m": 99})[0] is False
    assert T.satoshit_exit_fire(g, False, {"rsi_1h": 42, "stoch_k_15m": 50})[0] is True
    assert T.satoshit_exit_fire(g, False, {"rsi_1h": 42, "stoch_k_15m": 51})[0] is False
    g2 = G(SATOSHIT_EXIT_ENABLED=True, SATOSHIT_MIN_VOTES_TRADIER=2)  # OR mode
    assert T.satoshit_exit_fire(g2, True, {"rsi_1h": 54, "stoch_k_15m": 99})[0] is True


def test_satoshit_vec_scalar_agreement():
    rng = np.random.default_rng(29)
    g = G(SATOSHIT_EXIT_ENABLED=True)
    for is_long in (True, False):
        n = 20
        rsi = rng.uniform(0, 100, n)
        k = rng.uniform(0, 100, n)
        npz = {"rsi_1h": rsi, "stoch_k_15m": k}
        agree(T.satoshit_exit_fire_vec(npz, n, is_long, g),
              [T.satoshit_exit_fire(g, is_long, {"rsi_1h": float(rsi[i]), "stoch_k_15m": float(k[i])})[0] for i in range(n)])


# ─── Tradier-gated twins: crypto inert, tradier binds ───
def test_tradier_gated_crypto_inert():
    ind = {"stoch_k_15m": 0, "rsi_1h": 0, "mfi_D": 0, "wt1_1h": 9, "wt2_1h": 0,
           "wt1_4h": 9, "wt2_4h": 0, "wt1_D": 9, "wt2_D": 0}
    g = G(K_ZONE_ENTRY_ENABLED=True, BACKTEST_VALIDATED_GATES_TRADIER=True,
          TF_ALIGNMENT_MIN_TOTAL=99, TRADIER_ENTRY_SCORE_THRESHOLD=99,
          TRADIER_RSI_ENTRY_LONG_TRADIER=99, K_ZONE_LONG_THRESHOLD=35,
          TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER=0)
    # crypto uses generic thr (35): k=0 fires; tradier knob (0) must NOT bind
    assert T.kzone_entry_fire(g, True, ind)[0] is True
    assert T.kzone_entry_fire(g, True, {"stoch_k_15m": 50}) == (False, "")
    assert T.tradier_rsi_entry_fire(g, True, ind) == (False, "")
    assert T.tradier_stoch_entry_fire(g, True, ind) == (False, "")
    assert T.tf_alignment_ok(g, True, ind) is True
    assert T.tradier_entry_score_ok(g, True, ind) is True
    assert T.reentry_tier1_mult(G(), True) == 1.0
    assert T.mtf_atr_trail_tf(G(MTF_ATR_TRAIL_TF="4h")) == "4h"


def test_kzone_entry_crypto_uses_generic_threshold():
    g = G(K_ZONE_ENTRY_ENABLED=True, K_ZONE_LONG_THRESHOLD=35,
          TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER=1)  # tradier knob must NOT bind crypto
    assert T.kzone_entry_fire(g, True, {"stoch_k_15m": 34})[0] is True
    gt = G(K_ZONE_ENTRY_ENABLED=True, MODE="tradier", BASE_TF="5m",
           TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER=35)
    assert T.kzone_entry_fire(gt, True, {"stoch_k_5m": 34})[0] is True
    assert T.kzone_entry_fire(gt, True, {"stoch_k_5m": 35})[0] is False


def test_tradier_stoch_entry_band_and_cross_select():
    g = G(MODE="tradier", BASE_TF="5m")
    assert T.tradier_stoch_entry_fire(g, True, {"stoch_k_5m": 20})[0] is True
    assert T.tradier_stoch_entry_fire(g, True, {"stoch_k_5m": 10})[0] is False
    assert T.tradier_stoch_entry_fire(g, False, {"stoch_k_5m": 80})[0] is True
    assert T.tradier_stoch_entry_fire(g, False, {"stoch_k_5m": 90})[0] is False
    assert T.tradier_stoch_entry_fire(G(MODE="tradier", BASE_TF="5m", STOCH_CROSS_ENTRY_TRADIER=True),
                                      True, {"stoch_k_5m": 20}) == (False, "")


def test_tf_alignment_count_and_need():
    g = G(MODE="tradier", BACKTEST_VALIDATED_GATES_TRADIER=True, TF_ALIGNMENT_MIN_TOTAL=4)
    ind = {"wt1_1h": 1, "wt2_1h": 0, "wt1_4h": 0, "wt2_4h": 1, "wt1_D": 0, "wt2_D": 1}
    assert T.tf_alignment_ok(g, True, ind) is True  # cnt=1 >= need=1
    g8 = G(MODE="tradier", BACKTEST_VALIDATED_GATES_TRADIER=True, TF_ALIGNMENT_MIN_TOTAL=8)
    assert T.tf_alignment_ok(g8, True, ind) is False  # need=2
    assert T.tradier_entry_score_ok(G(MODE="tradier", TRADIER_ENTRY_SCORE_THRESHOLD=30), True, {"mfi_D": 40}) is True
    assert T.tradier_entry_score_ok(G(MODE="tradier", TRADIER_ENTRY_SCORE_THRESHOLD=23), True, {"mfi_D": 0}) is True


def test_tradier_twins_vec_agreement():
    n = 10
    g = G(MODE="tradier", BASE_TF="5m", K_ZONE_ENTRY_ENABLED=True,
          BACKTEST_VALIDATED_GATES_TRADIER=True, TF_ALIGNMENT_MIN_TOTAL=4,
          TRADIER_ENTRY_SCORE_THRESHOLD=30, TRADIER_RSI_ENTRY_LONG_TRADIER=40)
    npz = {"stoch_k_5m": np.linspace(0, 100, n), "rsi_1h": np.linspace(0, 100, n),
           "mfi_D": np.linspace(0, 100, n), "rel_vol_1h": np.full(n, 2.0),
           "wt1_1h": np.full(n, 1.0), "wt2_1h": np.full(n, 0.0),
           "wt1_4h": np.full(n, 1.0), "wt2_4h": np.full(n, 0.0),
           "wt1_D": np.full(n, 1.0), "wt2_D": np.full(n, 0.0)}
    rows = [{k: float(v[i]) for k, v in npz.items()} for i in range(n)]
    agree(T.kzone_entry_fire_vec(npz, n, True, g), [T.kzone_entry_fire(g, True, r)[0] for r in rows])
    agree(T.tradier_rsi_entry_fire_vec(npz, n, True, g), [T.tradier_rsi_entry_fire(g, True, r)[0] for r in rows])
    agree(T.tradier_stoch_entry_fire_vec(npz, n, False, g), [T.tradier_stoch_entry_fire(g, False, r)[0] for r in rows])
    agree(T.tf_alignment_ok_vec(npz, n, True, g), [T.tf_alignment_ok(g, True, r) for r in rows])
    agree(T.tradier_entry_score_ok_vec(npz, n, True, g), [T.tradier_entry_score_ok(g, True, r) for r in rows])


# ─── Strength (v12:9144-9162) ───
def test_strength_weights_and_gate():
    assert T.strength_ok(G(STRENGTH_FILTER_ENABLED=False), {}) is True
    assert T.strength_score({"B15": True}) == 4
    assert T.strength_score({"B15": True, "B04": True}) == 7
    assert T.strength_ok(G(), {"B15": True}) is False  # 4 < 5
    assert T.strength_ok(G(), {"B15": True, "B04": True}) is True
    assert T.strength_ok(G(STRENGTH_MIN_SCORE=8), {"B15": True, "B04": True}) is False
    n = 5
    blocks = {"B15": np.array([1, 0, 1, 0, 1]), "B02": np.array([0, 1, 0, 0, 0])}
    agree(T.strength_ok_vec(blocks, n, G()),
          [T.strength_ok(G(), {"B15": bool(blocks["B15"][i]), "B02": bool(blocks["B02"][i])}) for i in range(n)])


# ─── TF_FOCUS dead ───
def test_tf_focus_dead_always_passes():
    assert "never applied" in T.tf_focus_note() or "DEAD" in T.tf_focus_note()
    assert T.tf_focus_ok(G(TF_FOCUS_ENTRY_HARD_GATE=True, TF_FOCUS_WEIGHT=999), True, {}) is True
    assert bool(np.all(T.tf_focus_ok_vec(8)))


# ─── Min hold / reentry filter / simple / tier1 / mtf ───
def test_min_hold_math():
    assert T.min_hold_bars(G()) == 3
    assert T.min_hold_bars(G(MIN_HOLD_BARS=10, MIN_HOLD_BARS_BEFORE_EXIT=4)) == 10
    assert T.min_hold_bars(G(MODE="tradier", BASE_TF="5m", MIN_HOLD_MINUTES_TRADIER=30)) == 6
    assert T.min_hold_bars(G(MODE="crypto", MIN_HOLD_MINUTES_TRADIER=999)) == 3  # tradier-only
    assert T.min_hold_ok(G(), 3) is True
    assert T.min_hold_ok(G(), 2) is False


def test_reentry_filter_counting():
    assert T.reentry_filter_need(G(), 0) == 0
    assert T.reentry_filter_need(G(REENTRY_FILTER_MIN_PASS=9), 3) == 3  # clamped
    g = G(REENTRY_ENTRY_FILTER_ENABLED=True, REENTRY_FILTER_MIN_PASS=2)
    assert T.reentry_fire_allowed(g, [True, False, True], True, False) is True
    assert T.reentry_fire_allowed(g, [True, False, False], True, False) is False
    assert T.reentry_fire_allowed(g, [False], True, True) is True  # fresh entries exempt
    assert T.reentry_fire_allowed(g, [False], False, False) is True  # non-reentry exempt


def test_simple_gt0_mirror():
    n = 6
    c = np.array([1, 2, 0, -1, 5, 6.])
    e, x, cd, mh = T.simple_gt0_vec(n, c)
    assert (cd, mh) == (0, 1)
    for i in range(n):
        assert T.simple_gt0(G(SIMPLE_PRICE_GT0_ENABLED=True), float(c[i]), i) == (bool(e[i]), bool(x[i]))
    assert T.simple_gt0(G(), 5, 0) == (False, False)


def test_tier1_mtf_tradier_only():
    assert T.reentry_tier1_mult(G(MODE="tradier", REENTRY_TIER1_SIZE_MULT_TRADIER=1.5), True) == 1.5
    assert T.reentry_tier1_mult(G(MODE="tradier", REENTRY_TIER1_SIZE_MULT_TRADIER=1.5), False) == 1.0
    assert T.reentry_tier1_mult(G(REENTRY_TIER1_SIZE_MULT_TRADIER=1.5), True) == 1.0
    assert T.mtf_atr_trail_tf(G(MODE="tradier", MTF_ATR_TRAIL_TF_TRADIER="4h")) == "4h"
    assert T.mtf_atr_trail_tf(G(MODE="tradier")) == "1h"


# ─── Dead daytrade knobs ───
def test_daytrade_dead_knobs_inert():
    assert "DELETED" in T.daytrade_pct_note()
    assert T.daytrade_pct_fires() is False
    assert T.daytrade_pct_resolve(G()) == (1.5, 1.0)  # crypto 0.015/0.01 *100
    assert T.daytrade_pct_resolve(G(MODE="tradier")) == (0.5, 0.5)
    assert "field defs" in T.daytrade_use_dc_note()
    fl = T.daytrade_use_dc_flags(G(TRADIER_DC_DAYTRADE_STOP_USE_DC_15M=True))
    assert fl == {"STOP_USE_DC_15M": True, "STOP_USE_DC4_15M": False,
                  "TARGET_USE_DC_15M": False, "TARGET_USE_DC4_15M": False}


# ─── PPL reference (tradier_manage:19973-20027) ───
def test_ppl_tp_be_upgrade_sl():
    g = G()
    assert T.ppl_params(g) == (0.5, 0.75, 0.02, 0.5)
    fire, qty, _ = T.ppl_tp_fire(g, 0.6, 10.0, 1.0, 100.0, False)
    assert fire and qty == 5.0  # floor(10*0.5)
    assert T.ppl_tp_fire(g, 0.4, 10.0, 1.0, 100.0, False)[0] is False
    assert T.ppl_tp_fire(g, 0.6, 10.0, 1.0, 100.0, True)[0] is False
    f2, _, why = T.ppl_tp_fire(g, 5.0, 2.0, 1.0, 100.0, False)  # floor(2*.5)=1, keep=1 ok
    assert f2 and qty == 5.0
    f3, _, why3 = T.ppl_tp_fire(G(PARTIAL_PROFIT_LOCK_FRAC_TRADIER=0.9), 5.0, 1.5, 1.0, 100.0, False)
    assert f3 is False and "FULL_CLOSE" in why3  # floor(1.35)=1, keep=0.5<1 -> skip
    assert T.ppl_be_stop(g, True, 100.0) == pytest.approx(100.02)
    assert T.ppl_be_stop(g, False, 100.0) == pytest.approx(99.98)
    assert T.ppl_arm_upgrade(g, 0.8, True, False, 101.0) is True
    assert T.ppl_arm_upgrade(g, 0.7, True, False, 101.0) is False
    assert T.ppl_sl_hit(g, True, 99.0, 100.0, True, 5.0, 1.0) is True
    assert T.ppl_sl_hit(g, True, 101.0, 100.0, True, 5.0, 1.0) is False


# ─── LR ladder + bounce + erosion ───
def test_lr_ladder_stoch_slice():
    g = G()
    assert T.lr_ladder_stoch_ok(g, True, {"stoch_k_1h": 20, "stoch_k_1h_prev": 10}) is True
    assert T.lr_ladder_stoch_ok(g, True, {"stoch_k_1h": 20, "stoch_k_1h_prev": 25}) is False
    assert T.lr_ladder_stoch_ok(g, True, {"stoch_k_1h": 31, "stoch_k_1h_prev": 10}) is False
    assert T.lr_ladder_stoch_ok(g, False, {"stoch_k_1h": 80, "stoch_k_1h_prev": 90}) is True
    assert T.lr_ladder_stoch_ok(g, False, {"stoch_k_1h": 69, "stoch_k_1h_prev": 90}) is False
    assert T.lr_ladder_stoch_ok(g, True, {"_ladder_stoch_k_1h": 5, "_ladder_stoch_k_1h_prev": 1}) is True


def test_bounce_dd_stop_mirror():
    g = G()
    assert T.wt_d_bounce_dd_stop_fire(g, True, 99.0, 100.0, 1.0) is True
    assert T.wt_d_bounce_dd_stop_fire(g, True, 101.0, 100.0, 1.0) is False
    assert T.wt_d_bounce_dd_stop_fire(g, False, 101.0, 100.0, 1.0) is True
    assert T.wt_d_bounce_dd_stop_fire(g, True, 99.0, 100.0, 0.4) is False
    assert T.wt_d_bounce_dd_stop_fire(G(WT_D_BOUNCE_DD_STOP_ENABLED=False), True, 50.0, 100.0, 9.0) is False


def test_win_trail_erosion():
    assert T.win_trail_erosion_fire(G(), 10.0, 0.0) is False  # default 0.0 inert
    g = G(WIN_TRAIL_EROSION_PCT=0.5)
    assert T.win_trail_erosion_fire(g, 10.0, 4.0) is True   # (10-4) >= 10*.5
    assert T.win_trail_erosion_fire(g, 10.0, 5.01) is False
    assert T.win_trail_erosion_fire(g, 0.0, -5.0) is False  # peak>0 required
    assert T.win_trail_erosion_fire(g, 10.0, 4.0, confirm=False) is False
    p = np.array([10.0, 10.0, 0.0])
    lv = np.array([4.0, 9.0, -1.0])
    assert list(T.win_trail_erosion_fire_vec(p, lv, g)) == [True, False, False]


# ─── Alias helper ───
def test_with_aliases_backfills_both_ways():
    assert T.with_aliases({"k_15m": 42})["stoch_k_15m"] == 42
    assert T.with_aliases({"stoch_d_1h": 7})["d_1h"] == 7
    assert "stoch_k_15m_prev" not in T.with_aliases({"stoch_k_15m": 42})  # no prev synth
    assert T.with_aliases(None) == {}

