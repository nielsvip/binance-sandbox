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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
