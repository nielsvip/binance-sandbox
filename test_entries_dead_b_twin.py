"""Pack-B entry/exit twin parity (2026-10-04 wiring mandate).

Proves vec_decisions.twin_entries_dead_b reproduces the wired semantics exactly:
  STOCH_XTREME ENTRY long K<=20 / short K>=80 ; EXIT long K>=80 / short K<=20
  SMFI_DIV ENTRY long bull_div / short bear_div ; EXIT opposing flag
  VWAP_STRETCH ENTRY long stretched-below>=PCT / short stretched-above>=PCT ;
    EXIT the mirror (long exits stretched-above, short exits stretched-below)
Defaults/OFF/unknown-TF = inert; missing/NaN data never fires.
Known delta (documented, not tested): vec 3m/5m/1m leg uses base stoch_k while
live reads the TF-suffixed key; live vwap_npz vs NPZ vwap_D differ by bar source.
"""
from types import SimpleNamespace

import numpy as np

import vec_decisions.twin_entries_dead_b as T


def _get(d):
    return lambda k, default: d.get(k, default)


def _safe(npz, key, n, default):
    v = npz.get(key)
    if v is None:
        return np.full(n, default, dtype=float)
    return np.asarray(v, dtype=float)


def _resolvers():
    return [T.resolve_stoch_xtreme_entry, T.resolve_stoch_xtreme_exit, T.resolve_smfi_div_entry, T.resolve_smfi_div_exit, T.resolve_vwap_stretch_entry, T.resolve_vwap_stretch_exit]


def test_all_inert_by_default():
    for r in _resolvers():
        assert r(_get({})) == {"enabled": False}


def test_off_and_unknown_tf_inert():
    assert T.resolve_stoch_xtreme_entry(_get({"STOCH_XTREME_ENTRY_ENABLED": True, "STOCH_XTREME_ENTRY_TF": "OFF"})) == {"enabled": False}
    assert T.resolve_stoch_xtreme_entry(_get({"STOCH_XTREME_ENTRY_ENABLED": True, "STOCH_XTREME_ENTRY_TF": ""})) == {"enabled": False}
    assert T.resolve_stoch_xtreme_entry(_get({"STOCH_XTREME_ENTRY_ENABLED": True, "STOCH_XTREME_ENTRY_TF": "W"})) == {"enabled": False}
    assert T.resolve_smfi_div_exit(_get({"SMFI_DIV_EXIT_ENABLED": True, "SMFI_DIV_EXIT_TF": "M"})) == {"enabled": False}
    assert T.resolve_vwap_stretch_entry(_get({"VWAP_STRETCH_ENTRY_ENABLED": True, "VWAP_STRETCH_ENTRY_PCT": 0})) == {"enabled": False}
    assert T.resolve_vwap_stretch_exit(_get({"VWAP_STRETCH_EXIT_ENABLED": True, "VWAP_STRETCH_EXIT_PCT": -2})) == {"enabled": False}
    assert T.resolve_vwap_stretch_entry(_get({"VWAP_STRETCH_ENTRY_ENABLED": True, "VWAP_STRETCH_ENTRY_PCT": "junk"})) == {"enabled": False}


def test_tf_resolution():
    s = T.resolve_stoch_xtreme_entry(_get({"STOCH_XTREME_ENTRY_ENABLED": True, "STOCH_XTREME_ENTRY_TF": "1h"}))
    assert (s["live_field"], s["vec_field"]) == ("stoch_k_1h", "stoch_k_1h")
    s = T.resolve_stoch_xtreme_exit(_get({"STOCH_XTREME_EXIT_ENABLED": True, "STOCH_XTREME_EXIT_TF": "3m"}))
    assert (s["live_field"], s["vec_field"]) == ("stoch_k_3m", "stoch_k")
    s = T.resolve_smfi_div_entry(_get({"SMFI_DIV_ENTRY_ENABLED": True, "SMFI_DIV_ENTRY_TF": "D"}))
    assert (s["live_field_long"], s["vec_field_short"]) == ("smfi_bull_div_D", "smfi_bear_div_D")


def test_stoch_boundaries():
    le = T.resolve_stoch_xtreme_entry(_get({"STOCH_XTREME_ENTRY_ENABLED": True, "STOCH_XTREME_ENTRY_TF": "1h"}))
    lx = T.resolve_stoch_xtreme_exit(_get({"STOCH_XTREME_EXIT_ENABLED": True, "STOCH_XTREME_EXIT_TF": "1h"}))
    assert T.stoch_xtreme_entry_fire(le, True, lambda f: 20.0, 100.0)[0] is True
    assert T.stoch_xtreme_entry_fire(le, True, lambda f: 20.01, 100.0)[0] is False
    assert T.stoch_xtreme_entry_fire(le, False, lambda f: 80.0, 100.0)[0] is True
    assert T.stoch_xtreme_entry_fire(le, False, lambda f: 79.99, 100.0)[0] is False
    assert T.stoch_xtreme_exit_fire(lx, True, lambda f: 80.0, 100.0)[0] is True
    assert T.stoch_xtreme_exit_fire(lx, True, lambda f: 79.99, 100.0)[0] is False
    assert T.stoch_xtreme_exit_fire(lx, False, lambda f: 20.0, 100.0)[0] is True
    assert T.stoch_xtreme_exit_fire(lx, False, lambda f: 20.01, 100.0)[0] is False
    assert T.stoch_xtreme_entry_fire(le, True, lambda f: 0.0, 100.0)[1].startswith("STOCH_XTREME_ENTRY LONG")
    assert T.stoch_xtreme_exit_fire(lx, False, lambda f: 0.0, 100.0)[1].startswith("STOCH_XTREME_EXIT SHORT")


def test_missing_data_never_fires():
    le = T.resolve_stoch_xtreme_entry(_get({"STOCH_XTREME_ENTRY_ENABLED": True, "STOCH_XTREME_ENTRY_TF": "1h"}))
    de = T.resolve_smfi_div_entry(_get({"SMFI_DIV_ENTRY_ENABLED": True, "SMFI_DIV_ENTRY_TF": "1h"}))
    ve = T.resolve_vwap_stretch_entry(_get({"VWAP_STRETCH_ENTRY_ENABLED": True, "VWAP_STRETCH_ENTRY_PCT": 1.0}))
    for is_long in (True, False):
        assert T.stoch_xtreme_entry_fire(le, is_long, lambda f: 50.0, 100.0)[0] is False
        assert T.stoch_xtreme_entry_fire(le, is_long, lambda f: float("nan"), 100.0)[0] is False
        assert T.smfi_div_entry_fire(de, is_long, lambda f: 50.0, 100.0)[0] is False
        assert T.smfi_div_entry_fire(de, is_long, lambda f: 0.0, 100.0)[0] is False
        assert T.vwap_stretch_entry_fire(ve, is_long, lambda f: 0.0, 100.0)[0] is False
        assert T.vwap_stretch_entry_fire(ve, is_long, lambda f: 100.0, 0.0)[0] is False
        assert T.vwap_stretch_entry_fire(ve, is_long, lambda f: float("nan"), 100.0)[0] is False


def test_smfi_flags():
    de = T.resolve_smfi_div_entry(_get({"SMFI_DIV_ENTRY_ENABLED": True, "SMFI_DIV_ENTRY_TF": "1h"}))
    dx = T.resolve_smfi_div_exit(_get({"SMFI_DIV_EXIT_ENABLED": True, "SMFI_DIV_EXIT_TF": "1h"}))
    assert T.smfi_div_entry_fire(de, True, lambda f: 1 if f == "smfi_bull_div_1h" else 0, 100.0)[0] is True
    assert T.smfi_div_entry_fire(de, True, lambda f: 1 if f == "smfi_bear_div_1h" else 0, 100.0)[0] is False
    assert T.smfi_div_entry_fire(de, False, lambda f: 1 if f == "smfi_bear_div_1h" else 0, 100.0)[0] is True
    assert T.smfi_div_exit_fire(dx, True, lambda f: 1 if f == "smfi_bear_div_1h" else 0, 100.0)[0] is True
    assert T.smfi_div_exit_fire(dx, True, lambda f: 1 if f == "smfi_bull_div_1h" else 0, 100.0)[0] is False
    assert T.smfi_div_exit_fire(dx, False, lambda f: 1 if f == "smfi_bull_div_1h" else 0, 100.0)[0] is True
    assert T.smfi_div_entry_fire(de, True, lambda f: 2.0, 100.0)[0] is False


def test_vwap_boundaries():
    ve = T.resolve_vwap_stretch_entry(_get({"VWAP_STRETCH_ENTRY_ENABLED": True, "VWAP_STRETCH_ENTRY_PCT": 1.0}))
    vx = T.resolve_vwap_stretch_exit(_get({"VWAP_STRETCH_EXIT_ENABLED": True, "VWAP_STRETCH_EXIT_PCT": 1.0}))
    assert T.vwap_stretch_entry_fire(ve, True, lambda f: 100.0, 99.0)[0] is True
    assert T.vwap_stretch_entry_fire(ve, True, lambda f: 100.0, 99.01)[0] is False
    assert T.vwap_stretch_entry_fire(ve, False, lambda f: 100.0, 101.0)[0] is True
    assert T.vwap_stretch_entry_fire(ve, False, lambda f: 100.0, 100.99)[0] is False
    assert T.vwap_stretch_exit_fire(vx, True, lambda f: 100.0, 101.0)[0] is True
    assert T.vwap_stretch_exit_fire(vx, True, lambda f: 100.0, 100.99)[0] is False
    assert T.vwap_stretch_exit_fire(vx, False, lambda f: 100.0, 99.0)[0] is True
    assert T.vwap_stretch_exit_fire(vx, False, lambda f: 100.0, 99.01)[0] is False
    assert T.vwap_stretch_entry_fire(ve, True, lambda f: 100.0, 99.0)[1].startswith("VWAP_STRETCH_ENTRY LONG")


def _cfg(**kw):
    d = {"STOCH_XTREME_ENTRY_ENABLED": False, "STOCH_XTREME_ENTRY_TF": "1h", "STOCH_XTREME_EXIT_ENABLED": False, "STOCH_XTREME_EXIT_TF": "1h", "SMFI_DIV_ENTRY_ENABLED": False, "SMFI_DIV_ENTRY_TF": "1h", "SMFI_DIV_EXIT_ENABLED": False, "SMFI_DIV_EXIT_TF": "1h", "VWAP_STRETCH_ENTRY_ENABLED": False, "VWAP_STRETCH_ENTRY_PCT": 1.0, "VWAP_STRETCH_EXIT_ENABLED": False, "VWAP_STRETCH_EXIT_PCT": 1.0}
    d.update(kw)
    return SimpleNamespace(**d)


def test_vec_none_when_inert():
    n = 8
    npz = {"stoch_k_1h": np.zeros(n), "smfi_bull_div_1h": np.ones(n), "vwap_D": np.full(n, 100.0)}
    close = np.full(n, 90.0)
    assert T.stoch_xtreme_entry_vec(npz, n, True, _cfg(), close, _safe) is None
    assert T.stoch_xtreme_exit_vec(npz, n, True, _cfg(), close, _safe) is None
    assert T.smfi_div_entry_vec(npz, n, True, _cfg(), close, _safe) is None
    assert T.smfi_div_exit_vec(npz, n, True, _cfg(), close, _safe) is None
    assert T.vwap_stretch_entry_vec(npz, n, True, _cfg(), close, _safe) is None
    assert T.vwap_stretch_exit_vec(npz, n, True, _cfg(), close, _safe) is None
    assert T.stoch_xtreme_entry_vec(npz, n, True, _cfg(STOCH_XTREME_ENTRY_ENABLED=True, STOCH_XTREME_ENTRY_TF="OFF"), close, _safe) is None
    assert T.smfi_div_entry_vec(npz, n, True, _cfg(SMFI_DIV_ENTRY_ENABLED=True), close, _safe) is not None


def test_smfi_vec_honest_nonbinding_without_keys():
    n = 8
    close = np.full(n, 90.0)
    m = T.smfi_div_entry_vec({}, n, True, _cfg(SMFI_DIV_ENTRY_ENABLED=True), close, _safe)
    assert m is not None and not m.any()
    m = T.smfi_div_exit_vec({}, n, False, _cfg(SMFI_DIV_EXIT_ENABLED=True), close, _safe)
    assert m is not None and not m.any()


def test_vec_formula_agreement_grid():
    import random
    random.seed(11)
    n = 64
    k = np.array([random.uniform(0, 100) for _ in range(n)])
    bull = np.array([random.choice([0, 0, 0, 1]) for _ in range(n)], dtype=float)
    bear = np.array([random.choice([0, 0, 0, 1]) for _ in range(n)], dtype=float)
    vwap = np.array([random.uniform(80, 120) for _ in range(n)])
    close = np.array([random.uniform(80, 120) for _ in range(n)])
    npz = {"stoch_k_1h": k, "smfi_bull_div_1h": bull, "smfi_bear_div_1h": bear, "vwap_D": vwap}
    pct = 1.0
    cfg = _cfg(STOCH_XTREME_ENTRY_ENABLED=True, STOCH_XTREME_EXIT_ENABLED=True, SMFI_DIV_ENTRY_ENABLED=True, SMFI_DIV_EXIT_ENABLED=True, VWAP_STRETCH_ENTRY_ENABLED=True, VWAP_STRETCH_EXIT_ENABLED=True)
    for is_long in (True, False):
        assert (T.stoch_xtreme_entry_vec(npz, n, is_long, cfg, close, _safe) == ((k <= 20.0) if is_long else (k >= 80.0))).all()
        assert (T.stoch_xtreme_exit_vec(npz, n, is_long, cfg, close, _safe) == ((k >= 80.0) if is_long else (k <= 20.0))).all()
        assert (T.smfi_div_entry_vec(npz, n, is_long, cfg, close, _safe) == (((bull > 0.5) & (bull <= 1.5)) if is_long else (((bear > 0.5) & (bear <= 1.5))))).all()
        assert (T.smfi_div_exit_vec(npz, n, is_long, cfg, close, _safe) == (((bear > 0.5) & (bear <= 1.5)) if is_long else (((bull > 0.5) & (bull <= 1.5))))).all()
        below = (vwap - close) / vwap * 100.0 >= pct
        above = (close - vwap) / vwap * 100.0 >= pct
        assert (T.vwap_stretch_entry_vec(npz, n, is_long, cfg, close, _safe) == (below if is_long else above)).all()
        assert (T.vwap_stretch_exit_vec(npz, n, is_long, cfg, close, _safe) == (above if is_long else below)).all()
        for i in range(n):
            lvl = lambda f, _i=i: {"stoch_k_1h": k[_i], "smfi_bull_div_1h": bull[_i], "smfi_bear_div_1h": bear[_i], "vwap": vwap[_i]}[f]
            le = T.resolve_stoch_xtreme_entry(_get({"STOCH_XTREME_ENTRY_ENABLED": True, "STOCH_XTREME_ENTRY_TF": "1h"}))
            lx = T.resolve_stoch_xtreme_exit(_get({"STOCH_XTREME_EXIT_ENABLED": True, "STOCH_XTREME_EXIT_TF": "1h"}))
            de = T.resolve_smfi_div_entry(_get({"SMFI_DIV_ENTRY_ENABLED": True, "SMFI_DIV_ENTRY_TF": "1h"}))
            dx = T.resolve_smfi_div_exit(_get({"SMFI_DIV_EXIT_ENABLED": True, "SMFI_DIV_EXIT_TF": "1h"}))
            ve = T.resolve_vwap_stretch_entry(_get({"VWAP_STRETCH_ENTRY_ENABLED": True, "VWAP_STRETCH_ENTRY_PCT": pct}))
            vx = T.resolve_vwap_stretch_exit(_get({"VWAP_STRETCH_EXIT_ENABLED": True, "VWAP_STRETCH_EXIT_PCT": pct}))
            assert T.stoch_xtreme_entry_fire(le, is_long, lvl, close[i])[0] == bool(T.stoch_xtreme_entry_vec(npz, n, is_long, cfg, close, _safe)[i])
            assert T.stoch_xtreme_exit_fire(lx, is_long, lvl, close[i])[0] == bool(T.stoch_xtreme_exit_vec(npz, n, is_long, cfg, close, _safe)[i])
            assert T.smfi_div_entry_fire(de, is_long, lvl, close[i])[0] == bool(T.smfi_div_entry_vec(npz, n, is_long, cfg, close, _safe)[i])
            assert T.smfi_div_exit_fire(dx, is_long, lvl, close[i])[0] == bool(T.smfi_div_exit_vec(npz, n, is_long, cfg, close, _safe)[i])
            assert T.vwap_stretch_entry_fire(ve, is_long, lvl, close[i])[0] == bool(T.vwap_stretch_entry_vec(npz, n, is_long, cfg, close, _safe)[i])
            assert T.vwap_stretch_exit_fire(vx, is_long, lvl, close[i])[0] == bool(T.vwap_stretch_exit_vec(npz, n, is_long, cfg, close, _safe)[i])
