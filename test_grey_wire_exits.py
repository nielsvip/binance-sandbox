"""Unit tests for vec_decisions/grey_wire_exits.py (2026-09-30 grey-switch wiring)."""
import vec_decisions.grey_wire_exits as GW


def _c(over=None):
    over = over or {}
    return lambda k, d: over.get(k, d)


def _g(d):
    return lambda k, default=None: d.get(k, default)


def test_defaults_inert():
    assert GW.active_exits(_c()) == []
    assert GW.active_exits(_c({"E_3_USE_WT_STRUCTURE_EXIT_MODE": 1})) == []  # shadow mode never trades


def test_dc_hopeless():
    st = {"entry_px": 110.0, "age_s": 1000, "gain": -3.0, "px": 100.0}
    assert GW.dc_hopeless(_c(), _g({"dc_high_4h": 105.0, "dc_low_4h": 95.0}), st, True)[0]
    assert not GW.dc_hopeless(_c(), _g({"dc_high_4h": 105.0, "dc_low_4h": 95.0}), {**st, "age_s": 800}, True)[0]
    assert not GW.dc_hopeless(_c(), _g({"dc_high_4h": 115.0, "dc_low_4h": 95.0}), st, True)[0]
    assert GW.dc_hopeless(_c(), _g({"dc_high_4h": 105.0, "dc_low_4h": 95.0}), {**st, "entry_px": 90.0}, False)[0]


def test_wt_4h_vel():
    st = {"gain": 1.0, "age_s": 400}
    g = _g({"wt_velocity_4h": -3.0, "k_15m": 85.0})
    assert GW.wt_4h_vel(_c(), g, st, True)[0]
    assert not GW.wt_4h_vel(_c(), g, {**st, "gain": 0.05}, True)[0]  # below commission buffer
    assert not GW.wt_4h_vel(_c(), _g({"wt_velocity_4h": -3.0, "k_15m": 50.0}), st, True)[0]  # K not extreme
    assert GW.wt_4h_vel(_c({"WT_4H_VEL_EXIT_REQUIRE_K_EXTREME": False}), _g({"wt_velocity_4h": -3.0}), st, True)[0]
    assert not GW.wt_4h_vel(_c(), g, {**st, "age_s": 300}, True)[0]


def test_percentile_needs_15m_cross():
    d = {"wt_percentile_D": 95, "wt_percentile_4h": 80, "wt1_15m": 10, "wt2_15m": 20}
    assert GW.wt_percentile(_c(), _g(d), {}, True)[0]
    assert not GW.wt_percentile(_c(), _g({**d, "wt1_15m": 30}), {}, True)[0]


def test_e1_e3_decoding():
    assert GW.e1_wt_delta(_c(), _g({"wt_composite_delta": -60}), {}, True)[0]
    assert not GW.e1_wt_delta(_c(), _g({}), {}, True)[0]
    live = {"wt_structure_15m": "LH", "wt_structure_1h": "LL", "wt_structure_4h": "HH"}
    npz = {"wt_structure_15m": -1, "wt_structure_1h": -1, "wt_structure_4h": 1}
    assert GW.e3_structure(_c(), _g(live), {}, True)[0]
    assert GW.e3_structure(_c(), _g(npz), {}, True)[0]
    assert not GW.e3_structure(_c(), _g(npz), {}, False)[0]


def test_htf_against():
    d = {"wt1_1h": 10, "wt2_1h": 20, "wt1_15m": 5, "wt2_15m": 6, "wt1_4h": 1, "wt2_4h": 2}
    st = {"age_s": 3600, "px": 100.0}
    assert GW.htf_against_force_close(_c({"HTF_AGAINST_FORCE_CLOSE_CONFIRM_4H": 1.0}), _g(d), st, True)[0]
    assert not GW.htf_against_force_close(_c(), _g({**d, "wt1_15m": 9}), st, True)[0]
    assert not GW.htf_against_force_close(_c(), _g(d), {**st, "age_s": 60}, True)[0]  # min-hold 10x3m
    assert GW.htf_against_force_close(_c(), _g({**d, "dc_low_15m": 101.0}), {**st, "age_s": 60}, True)[0]  # dc_15m override


def test_entry_gates():
    import numpy as np
    import vec_decisions.grey_wire_entries as GE
    c = lambda over: (lambda k, d: over.get(k, d))
    g = lambda d: (lambda k, default=None: d.get(k, default))
    assert GE.live_open_pass(g({}), c({}), 100.0, True) == (True, "")
    assert not GE.wt_percentile_entry_live_pass(g({"wt_percentile_D": 95}), c({"WT_PERCENTILE_ENTRY_GATE_ENABLED": True}), 1.0, True)[0]
    assert GE.wt_percentile_entry_live_pass(g({"wt_percentile_D": 95}), c({"WT_PERCENTILE_ENTRY_GATE_ENABLED": True}), 1.0, False)[0]
    ind = {"wt1_D": 2, "wt2_D": 1, "wt1_4h": 2, "wt2_4h": 1, "wt1_1h": 1, "wt2_1h": 2, "sma_200_D": 90.0}
    on = {"HTF_DIRECTION_GATE_ENABLED": True, "HTF_GATE_MIN_CONFIRMATIONS": 2, "HTF_GATE_D_MANDATORY": 0.0}
    assert GE.htf_direction_live_pass(g(ind), c(on), 100.0, True)[0]
    assert not GE.htf_direction_live_pass(g(ind), c(on), 100.0, False)[0]
    # scalar == vec on the same bar
    npz = {k: np.array([float(v)]) for k, v in ind.items()}
    safe = lambda z, k, n, d=0.0: z.get(k, np.full(n, d))
    cfg = type("C", (), on)()
    for L in (True, False):
        assert bool(GE.htf_direction_gate(npz, 1, L, cfg, np.array([100.0]), safe)[0]) == GE.htf_direction_live_pass(g(ind), c(on), 100.0, L)[0]
    cfg2 = type("C", (), {"WT_PERCENTILE_ENTRY_GATE_ENABLED": True})()
    for p in (5.0, 50.0, 95.0):
        z = {"wt_percentile_D": np.array([p])}
        for L in (True, False):
            assert bool(GE.wt_percentile_entry_gate(z, 1, L, cfg2, None, safe)[0]) == GE.wt_percentile_entry_live_pass(g({"wt_percentile_D": p}), c({"WT_PERCENTILE_ENTRY_GATE_ENABLED": True}), 1.0, L)[0]


def test_hlr_live_matches_vec_core():
    ind = {"wt_velocity_1h": -2.0, "wt_velocity_4h": -1.0, "wt_velocity_D": 0.5, "wt_velocity_W": 0.1,
           "wt_acceleration_4h": -1.0, "wt_acceleration_D": 0.2, "wt_acceleration_W": 0.0}
    on = {"HLR_TOP_EXIT_ENABLED": True, "HLR_TOP_MIN_TFS": 2}
    c = lambda k, d: on.get(k, d)
    g = lambda k, d=None: ind.get(k, d)
    assert GW.active_live_only_exits(lambda k, d: d) == []
    assert GW.hlr_top_exit(c, g, {"gain": 2.0}, True)[0]
    assert not GW.hlr_top_exit(c, g, {"gain": 1.0}, True)[0]  # below HLR_TOP_MIN_GAIN_PCT 1.5


def test_hlr_scalar_equals_vec_random():
    import numpy as np
    import vec_decisions.quick_reduce_strong as Q
    rng = np.random.default_rng(7)
    n = 400
    a = {k: rng.normal(0, 2, n) for k in ("v1", "v4", "vD", "vW", "a4", "aD", "aW")}
    div4, divD, pk4 = rng.integers(-1, 2, n), rng.integers(-1, 2, n), rng.integers(-1, 2, n)
    gain = rng.uniform(0, 4, n)
    on = {"HLR_TOP_EXIT_ENABLED": True, "HLR_TOP_MIN_TFS": 2}
    cfg = type("C", (), on)()
    for L in (True, False):
        m = Q.check_quick_reduce_strong_vec(cfg, gain, L, a["v1"], a["v4"], a["vD"], a["vW"], a["a4"], a["aD"], a["aW"], div4, divD, pk4)
        for i in range(n):
            ind = {"wt_velocity_1h": a["v1"][i], "wt_velocity_4h": a["v4"][i], "wt_velocity_D": a["vD"][i], "wt_velocity_W": a["vW"][i],
                   "wt_acceleration_4h": a["a4"][i], "wt_acceleration_D": a["aD"][i], "wt_acceleration_W": a["aW"][i],
                   "wt_divergence_4h": int(div4[i]), "wt_divergence_D": int(divD[i]), "wt_peak_structure_4h": int(pk4[i])}
            live = GW.hlr_top_exit(lambda k, d: on.get(k, d), lambda k, d=None: ind.get(k, d), {"gain": gain[i]}, L)[0]
            assert live == bool(m[i]), (L, i)


def test_breakeven_gain_erosion():
    on = {"BREAKEVEN_GAIN_EROSION_ENABLED": True, "BREAKEVEN_GAIN_EROSION_REQUIRE_PROFIT": False}
    c = lambda k, d: on.get(k, d)
    against = {"wt1_1h": 1, "wt2_1h": 2, "wt1_4h": 1, "wt2_4h": 2, "wt1_D": 1, "wt2_D": 2}
    st = {"gain": -0.5, "age_s": 3600, "max_gain": 1.0}
    assert GW.breakeven_gain_erosion(c, lambda k, d=None: against.get(k, d), st, True)[0]
    assert not GW.breakeven_gain_erosion(c, lambda k, d=None: against.get(k, d), {**st, "max_gain": 0.2}, True)[0]  # HARD_BREAKEVEN peak gate
    withpos = {k: (2 if k.startswith("wt1") else 1) for k in against}
    assert not GW.breakeven_gain_erosion(c, lambda k, d=None: withpos.get(k, d), st, True)[0]  # trend veto
    assert not GW.breakeven_gain_erosion(lambda k, d: {"BREAKEVEN_GAIN_EROSION_ENABLED": True}.get(k, d), lambda k, d=None: against.get(k, d), st, True)[0]  # default window [50,50.5)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
