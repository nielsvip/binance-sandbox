"""E7 2026-10-20 — FIXED_STOP reference as a tested switch family (USER "fixed stop can get switches, the
dc_basis/bb_basis/low are supposed to be earlier stops not later ones, but if the numbers prove otherwise I go with
the numbers"; doc 17 §3.7). Level math per reference, short mirroring, fallback, defaults identity, tighten ratchet,
vector level == core level == live freeze level, and the crypto venue leg staying ~0.15% beyond the software leg.
"""
import types

import numpy as np
import pytest

import vec_decisions.fixed_stop as fx

IND = {
    "dc_low_15m": 100.0, "dc_high_15m": 110.0, "dc_basis_15m": 105.0,
    "bb_lower_15m": 101.0, "bb_upper_15m": 109.0, "bb_middle_15m": 105.5,
    "dc_low_1h": 96.0, "dc_high_1h": 114.0, "dc_basis_1h": 105.0,
    "dc_low_4h": 90.0, "dc_high_4h": 120.0, "dc_basis_4h": 105.0,
    "bb_lower_4h": 97.0, "bb_upper_4h": 113.0,
}


def _spec(field="dc_low", tf="15m", buf=0.25, mode="off", trig=1.0):
    return fx.normalize_spec(field, tf, buf, mode, trig)


def test_defaults_identity_byte_for_byte():
    for is_long, ep in ((True, 104.0), (False, 106.0)):
        lvl, src = fx.freeze_level_spec(is_long, fx.DEFAULT_SPEC, IND.get, ep)
        assert src == "DEFAULT"
        assert lvl == fx.freeze_software_level(is_long, IND["dc_low_15m"], IND["dc_high_15m"], ep)
    assert fx.freeze_level_spec(True, fx.DEFAULT_SPEC, {}.get, 100.0)[0] == fx.freeze_software_level(True, 0.0, 0.0, 100.0) == 99.75
    assert fx.spec_from(lambda k, d: d) == fx.DEFAULT_SPEC
    assert fx.is_default_spec(fx.DEFAULT_SPEC) and fx.is_default_reference(fx.DEFAULT_SPEC)
    assert fx.tightened(True, 99.75, fx.tighten_candidate(True, fx.DEFAULT_SPEC, 100.0, 50.0, 120.0)) == 99.75


@pytest.mark.parametrize("field,key_long,key_short", [
    ("dc_low", "dc_low_1h", "dc_high_1h"), ("dc_basis", "dc_basis_15m", "dc_basis_15m"),
    ("bb_low", "bb_lower_15m", "bb_upper_15m"), ("bb_basis", "bb_middle_15m", "bb_middle_15m"),
])
def test_level_math_per_reference_and_short_mirror(field, key_long, key_short):
    tf = "1h" if field == "dc_low" else "15m"
    lvl, src = fx.freeze_level_spec(True, _spec(field, tf, 0.35), IND.get, 108.0)
    assert src == f"REF:{key_long}" and abs(lvl - IND[key_long] * (1 - 0.0035)) < 1e-12
    lvl_s, src_s = fx.freeze_level_spec(False, _spec(field, tf, 0.35), IND.get, 102.0)
    assert src_s == f"REF:{key_short}" and abs(lvl_s - IND[key_short] * (1 + 0.0035)) < 1e-12


def test_short_aliases_are_the_mirror_not_a_new_reference():
    assert _spec("dc_high") == _spec("dc_low") and _spec("bb_high") == _spec("bb_low") and _spec("bb_upper") == _spec("bb_low")
    assert _spec("bb_mid") == _spec("bb_basis") and _spec("BB_MIDDLE")[0] == "bb_basis"


def test_invalid_components_fall_back_to_default():
    assert fx.normalize_spec("nonsense", "3m", "abc", "maybe", -1) == fx.DEFAULT_SPEC
    assert fx.normalize_spec("dc_basis", "D", 9.0, "OFF", None) == ("dc_basis", "15m", 0.25, "off", 1.0)


def test_basis_is_earlier_than_low_for_longs_and_shorts():
    """The operator's expectation, as geometry: a basis stop sits closer to entry than the channel edge."""
    ep_l, ep_s = 108.0, 102.0
    low = fx.freeze_level_spec(True, _spec("dc_low", "15m", 0.15), IND.get, ep_l)[0]
    basis = fx.freeze_level_spec(True, _spec("dc_basis", "15m", 0.15), IND.get, ep_l)[0]
    assert ep_l - basis < ep_l - low
    high = fx.freeze_level_spec(False, _spec("dc_low", "15m", 0.15), IND.get, ep_s)[0]
    basis_s = fx.freeze_level_spec(False, _spec("dc_basis", "15m", 0.15), IND.get, ep_s)[0]
    assert basis_s - ep_s < high - ep_s


def test_fallback_missing_and_wrong_side():
    lvl, src = fx.freeze_level_spec(True, _spec("bb_low", "1h"), IND.get, 104.0)
    assert src == "FALLBACK_MISSING:bb_low_1h" and lvl == 99.75
    lvl, src = fx.freeze_level_spec(True, _spec("dc_basis", "15m"), IND.get, 104.0)  # basis 105 above a 104 dip entry
    assert src.startswith("FALLBACK_WRONG_SIDE") and lvl == 99.75
    lvl, src = fx.freeze_level_spec(False, _spec("dc_basis", "15m"), IND.get, 106.0)
    assert src.startswith("FALLBACK_WRONG_SIDE") and abs(lvl - 110.0 * 1.0025) < 1e-12


def test_bb_basis_falls_back_to_band_midpoint():
    ind = {"bb_upper_4h": 113.0, "bb_lower_4h": 97.0, "dc_low_15m": 100.0, "dc_high_15m": 110.0}
    v, key = fx.ref_value(True, "bb_basis", "4h", ind.get)
    assert v == 105.0 and key.startswith("(bb_upper_4h")
    v2, k2 = fx.ref_value(True, "bb_basis", "15m", {"bb_mid_15m": 104.0}.get)
    assert v2 == 104.0 and k2 == "bb_mid_15m"


def test_tighten_modes_ratchet_never_loosen():
    s_be = _spec(mode="breakeven_after_pct", trig=1.0)
    assert fx.tighten_candidate(True, s_be, 100.0, 0.9, 0.0) == 0.0
    assert fx.tighten_candidate(True, s_be, 100.0, 1.0, 0.0) == 100.0
    assert fx.tightened(True, 99.75, 100.0) == 100.0 and fx.tightened(True, 100.0, 99.0) == 100.0
    assert fx.tightened(False, 100.25, 100.0) == 100.0 and fx.tightened(False, 100.0, 101.0) == 100.0
    s_tr = _spec("dc_basis", "15m", 0.25, "trail_ref", 0.5)
    assert abs(fx.tighten_candidate(True, s_tr, 100.0, 0.6, 104.0) - 104.0 * 0.9975) < 1e-12
    assert abs(fx.tighten_candidate(False, s_tr, 100.0, 0.6, 96.0) - 96.0 * 1.0025) < 1e-12


def test_venue_leg_stays_015_beyond_software_for_every_buffer():
    for buf in (0.15, 0.25, 0.35, 0.50):
        soft = fx.level_from_ref(True, 100.0, buf)
        venue = fx.crypto_exchange_level(True, soft)
        assert abs((soft - venue) / soft * 100.0 - 0.1504) < 0.001
        soft_s = fx.level_from_ref(False, 100.0, buf)
        assert abs((fx.crypto_exchange_level(False, soft_s) - soft_s) / soft_s * 100.0 - 0.1496) < 0.001
    assert abs(fx.crypto_exchange_level(True, fx.level_from_ref(True, 100.0, 0.25)) - 99.60) < 1e-9


def _mk(n, close, extra):
    ts = np.arange(1700000000, 1700000000 + n * 900, 900)
    z = np.zeros(n)
    d = {"close_3m": close, "close_15m": close, "close_5m": close, "close": close, "timestamps": ts, "timestamp_3m": ts, "timestamp_15m": ts,
         "dc_low_4h": np.full(n, 50.0), "dc_high_4h": np.full(n, 150.0), "dc_low_1h": np.full(n, 50.0), "dc_high_1h": np.full(n, 150.0)}
    for k in ("wt1_3m", "wt2_3m", "wt1_15m", "wt2_15m", "wt1_1h", "wt2_1h", "wt1_4h", "wt2_4h", "wt1_D", "wt2_D"):
        d[k] = z
    d.update(extra)
    return d


def _cfg(**kw):
    import v12_quick_engine as vq
    cfg = vq.QuickConfig()
    cfg.MODE = "crypto"; cfg.BASE_TF = "15m"; cfg.START_POSITION_SIZE = 500
    cfg.COOLDOWN_BARS = 0; cfg.MIN_HOLD_BARS = 100; cfg.NOLOSS_ENABLED = True
    cfg.PROFIT_TARGET_ENABLED = False; cfg.STOP_LOSS_ENABLED = False
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def _first_stop(r):
    return next((t for t in r["ledger"] if t.get("exit_reason") == "FIXED_STOP"), None)


def test_vec_level_equals_core_level_for_each_reference():
    import v12_quick_engine as vq
    n = 150
    close = np.linspace(101.0, 90.0, n)
    ex = {"dc_low_15m": np.full(n, 95.0), "dc_high_15m": np.full(n, 150.0), "dc_basis_15m": np.full(n, 99.0),
          "bb_lower_15m": np.full(n, 97.0), "bb_upper_15m": np.full(n, 140.0), "bb_middle_15m": np.full(n, 98.0)}
    npz = _mk(n, close, ex)
    seen = {}
    for field in ("dc_low", "dc_basis", "bb_low", "bb_basis"):
        cfg = _cfg(FIXED_STOP_REF_FIELD=field)
        want = fx.freeze_level_spec(True, fx.spec_from(lambda k, d: getattr(cfg, k, d)), lambda k: float(npz[k][0]) if k in npz else None, float(close[0]))[0]
        r = vq.simulate_one(npz, "FX_FAM", True, cfg, force_initial_seed=True)
        t = _first_stop(r)
        if field == "dc_low":  # today's 94.76 level: another exit (REDUCE_TO_FLAT) closes the trade before the stop is reached
            seen[field] = next(x for x in r["ledger"] if x.get("exit_reason"))["bar_exit"]
            assert seen[field] < int(np.where(close <= want)[0][0]) or (t is not None and t["bar_exit"] == int(np.where(close <= want)[0][0]))
            continue
        assert t is not None, field
        assert t["bar_exit"] == int(np.where(close <= want)[0][0]), (field, t["bar_exit"], want)
        seen[field] = t["bar_exit"]
    assert seen["dc_basis"] < seen["dc_low"] and seen["bb_basis"] < seen["dc_low"] and seen["bb_low"] < seen["dc_low"]


def test_vec_short_mirror():
    import v12_quick_engine as vq
    n = 150
    close = np.linspace(99.0, 112.0, n)
    ex = {"dc_low_15m": np.full(n, 50.0), "dc_high_15m": np.full(n, 105.0), "dc_basis_15m": np.full(n, 101.0)}
    ex["dc_high_15m"][0] = 100.0
    npz = _mk(n, close, ex)
    t0 = _first_stop(vq.simulate_one(npz, "FX_S", False, _cfg(), force_initial_seed=True))
    t1 = _first_stop(vq.simulate_one(npz, "FX_S", False, _cfg(FIXED_STOP_REF_FIELD="dc_basis"), force_initial_seed=True))
    assert t0["bar_exit"] == int(np.where(close >= 100.0 * 1.0025)[0][0])
    assert t1["bar_exit"] == int(np.where(close >= 101.0 * 1.0025)[0][0])


def test_vec_breakeven_and_trail_ratchet():
    import v12_quick_engine as vq
    n = 150
    close = np.r_[np.linspace(100.0, 101.2, 20), np.linspace(101.2, 95.0, 130)]
    ex = {"dc_low_15m": np.full(n, 96.0), "dc_high_15m": np.full(n, 150.0), "dc_basis_15m": np.r_[np.full(20, 99.0), np.full(130, 100.5)]}
    npz = _mk(n, close, ex)
    r0 = vq.simulate_one(npz, "FX_BE", True, _cfg(), force_initial_seed=True)
    t0 = next(t for t in r0["ledger"] if t.get("exit_reason"))
    t1 = _first_stop(vq.simulate_one(npz, "FX_BE", True, _cfg(FIXED_STOP_TIGHTEN_MODE="breakeven_after_pct", FIXED_STOP_TIGHTEN_TRIGGER_PCT=0.5), force_initial_seed=True))
    assert t1 is not None and t1["bar_exit"] < t0["bar_exit"]
    assert t1["bar_exit"] == int(np.where(close <= 100.0)[0][1])  # first close back at/below the 100.0 entry after the peak
    t2 = _first_stop(vq.simulate_one(npz, "FX_TR", True, _cfg(FIXED_STOP_TIGHTEN_MODE="trail_ref", FIXED_STOP_TIGHTEN_TRIGGER_PCT=0.5, FIXED_STOP_REF_FIELD="dc_basis"), force_initial_seed=True))
    assert t2 is not None and t2["bar_exit"] < t1["bar_exit"]  # trails dc_basis_15m 100.5 * 0.9975 = 100.249 once in profit
    assert t2["exit_price"] <= 100.5 * 0.9975 + 1e-9


def _live_cfg_proxy(values):
    return lambda symbol, side, knob, default: values.get(knob, default)


def test_live_ez_freeze_equals_vec_reference(monkeypatch):
    import ez_manage as EM
    vals = {"FIXED_STOP_REF_FIELD": "bb_basis", "FIXED_STOP_REF_TF": "15m", "FIXED_STOP_BUFFER_PCT": 0.35}
    monkeypatch.setattr(EM, "_psym_get", _live_cfg_proxy(vals))
    ind = {"dc_low_15m": 100.0, "dc_high_15m": 110.0, "bb_mid_15m": 104.0}
    lvl, src = EM._fixed_stop_freeze_level_spec("XUSDT", "LONG", True, ind, 106.0)
    assert src == "REF:bb_mid_15m" and abs(lvl - 104.0 * (1 - 0.0035)) < 1e-12
    p = fx.build_stop_params(True, lvl, 0.0001)
    assert abs(p["stopPrice"] - fx.round_to_tick(True, fx.crypto_exchange_level(True, lvl), 0.0001)) < 1e-12
    monkeypatch.setattr(EM, "_psym_get", _live_cfg_proxy({}))
    assert EM._fixed_stop_freeze_level_spec("XUSDT", "LONG", True, ind, 106.0) == EM._fixed_stop_freeze_level(True, 100.0, 110.0, 106.0)
    assert EM._fixed_stop_ref_tag("XUSDT", "LONG") == ""


def test_live_ez_tighten_ratchet(monkeypatch):
    import ez_manage as EM
    monkeypatch.setattr(EM, "_psym_get", _live_cfg_proxy({"FIXED_STOP_TIGHTEN_MODE": "breakeven_after_pct", "FIXED_STOP_TIGHTEN_TRIGGER_PCT": 1.0}))
    tm = types.SimpleNamespace()
    a = EM._fixed_stop_effective_level(tm, "men:X_LONG", "X", "LONG", True, 99.75, 100.0, 1.5, {}, 1.0)
    b = EM._fixed_stop_effective_level(tm, "men:X_LONG", "X", "LONG", True, 99.75, 100.0, 0.2, {}, 1.0)
    assert a == 99.75 and b == 100.0  # armed on the first check, applied from the next one, kept when max_gain is lower
    assert EM._fixed_stop_effective_level(tm, "men:X_LONG", "X", "LONG", True, 99.75, 100.0, 1.5, {}, 2.0) == 99.75  # new lifecycle
    assert EM._fixed_stop_ref_tag("X", "LONG") == "_REFdc_low.15m.b0.25.Tbreakeven_after_pct"
    monkeypatch.setattr(EM, "_psym_get", _live_cfg_proxy({}))
    assert EM._fixed_stop_effective_level(tm, "men:Y_LONG", "Y", "LONG", True, 99.75, 100.0, 50.0, {}, 1.0) == 99.75


def test_live_tradier_freeze_equals_vec_reference(monkeypatch):
    import tradier_manage as TM
    vals = {"FIXED_STOP_REF_FIELD": "dc_basis", "FIXED_STOP_REF_TF": "1h", "FIXED_STOP_BUFFER_PCT": 0.15}
    monkeypatch.setattr(TM, "_cfg", lambda k, d=None, *a, **kw: vals.get(k, d))
    ind = {"dc_low_15m": 100.0, "dc_high_15m": 110.0, "dc_low_1h": 98.0, "dc_high_1h": 112.0}
    assert TM._tr_fixed_stop_ref_default("trb", "X", "LONG") is False
    lvl = TM._tr_fixed_stop_freeze("trb", "X", "LONG", True, ind, 106.0)
    assert abs(lvl - 105.0 * (1 - 0.0015)) < 1e-12  # dc_basis_1h absent -> (dc_high_1h + dc_low_1h) / 2
    monkeypatch.setattr(TM, "_cfg", lambda k, d=None, *a, **kw: d)
    assert TM._tr_fixed_stop_ref_default("trb", "X", "LONG") is True
    assert TM._tr_fixed_stop_ref_tag("trb", "X", "LONG") == ""


def test_history_prov_carries_reference():
    import vec_decisions.history_prov as hp
    b = hp.classify_event("FIXED_STOP_LONG_px1.0_lvl1.0_g-2.00_REFdc_basis.1h.b0.15", "CLOSE")
    assert b["src"] == "FIXED_STOP" and b["ref"] == {"field": "dc_basis", "tf": "1h", "buffer_pct": 0.15, "tighten": "off"}
    assert "ref" not in hp.classify_event("FIXED_STOP_LONG_px1.0_lvl1.0_g-2.00", "CLOSE")
