"""Gap-vector batch 2 — wire-up proofs for 4 reentry switches (parent brief 2026-10-21).

(1) MANDATORY_REENTRY_K_LOW_BLOCK: predicate + dispatcher pre-existed in
    vec_decisions/lane_vec_reentry2.py; this batch adds ONLY the v12 F1
    call-site key. k_3m absent from frozen NPZ -> None path, default-inert.
(2) DELTA_REENTRY_Z_THRESHOLD: live ez_manage.check_reentry_delta_tolerant
    blocks when speed z < thr (master DELTA_REENTRY_FILTER_ENABLED, default
    False). speed_z absent from NPZ -> None path, default-inert.
(3) REENTRY2_DIR_FAV_ENABLED: real EPQ AND-leg (ez_positions_quick 32641-32649)
    as a bar predicate, triple-gated; per-bar immediate-reentry fire hunk +
    ledger-flip proof on real NPZ augmented with stand-in 3m keys.
(4) REENTRY_RALLY_K15M_MAX: live blocks reentry at k_15m>=cap (cap>=100
    disables); k_15m present -> full predicate + EPQ-path veto + flip proof.
    The 2026-10-16 dead twins (reentry_epq_vec/reentry_tiers) are untouched.

No Sharpe/gain/delta numbers anywhere: masks, None-states, ledger
fingerprints (open/close bars + reasons) and trade counts only.
"""
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REAL_NPZ = sorted((ROOT / "backtest_v8" / "indicators").glob("*.npz"))[0]


def _cfg(**kw):
    return SimpleNamespace(**kw)


def _synth(n=12, **cols):
    d = {}
    for k, v in cols.items():
        d[k] = np.full(n, float(v))
    return d


def _fp(ledger):
    rows = []
    for t in ledger:
        if t.get("type") == "OPEN":
            rows.append(("OPEN", int(t.get("bar", -1)), str(t.get("reason", ""))))
        elif t.get("type") == "CLOSE":
            rows.append(("CLOSE", int(t.get("bar_entry", -1)), int(t.get("bar_exit", -1))))
        else:
            rows.append((str(t.get("type")), int(t.get("bar", -1))))
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def _f1_region():
    src = (ROOT / "v12_quick_engine.py").read_text()
    a = src.find("WIRING LANE F1 2026-10-05")
    b = src.find('blocks["B_LANE_F1_REENTRY"]')
    assert a > 0 and b > a
    return src[a:b]


# ── (1) MANDATORY_REENTRY_K_LOW_BLOCK: call-site key only ──

def test_k_low_call_site_key_in_f1_loop():
    assert '"MANDATORY_REENTRY_K_LOW_BLOCK"' in _f1_region()


def test_k_low_none_on_real_npz_binds_synthetic():
    import vec_decisions.lane_vec_reentry2 as L
    z = np.load(str(REAL_NPZ), allow_pickle=True)
    n = len(np.asarray(z["close"]).reshape(-1))
    assert "k_3m" not in z.files
    assert L.get("MANDATORY_REENTRY_K_LOW_BLOCK", z, n, False, _cfg()) is None
    assert L.get("MANDATORY_REENTRY_K_LOW_BLOCK", z, n, True, _cfg()) is None
    syn = _synth(k_3m=10.0)
    m = L.get("MANDATORY_REENTRY_K_LOW_BLOCK", syn, 12, False,
              _cfg(MANDATORY_REENTRY_K_LOW_BLOCK=20.0))
    assert m is not None and bool(m.all())
    assert L.get("MANDATORY_REENTRY_K_LOW_BLOCK", syn, 12, True, _cfg()) is None


def test_k_low_default_value_untouched():
    import v12_quick_engine as V
    assert V.QuickConfig().MANDATORY_REENTRY_K_LOW_BLOCK == 20.0


# ── (2) DELTA_REENTRY_Z_THRESHOLD: predicate + dispatcher + call-site key ──

def test_delta_z_call_site_key_in_f1_loop():
    assert '"DELTA_REENTRY_Z_THRESHOLD"' in _f1_region()


def test_delta_z_dispatcher_no_keyerror_none_paths():
    import vec_decisions.lane_vec_reentry2 as L
    z = np.load(str(REAL_NPZ), allow_pickle=True)
    n = len(np.asarray(z["close"]).reshape(-1))
    assert "speed_z" not in z.files
    assert L.get("DELTA_REENTRY_Z_THRESHOLD", z, n, True, _cfg()) is None
    assert L.get("DELTA_REENTRY_Z_THRESHOLD", z, n, False, _cfg()) is None
    armed_no_key = _cfg(DELTA_REENTRY_FILTER_ENABLED=True,
                        DELTA_REENTRY_Z_THRESHOLD=1.0)
    assert L.get("DELTA_REENTRY_Z_THRESHOLD", z, n, True, armed_no_key) is None
    gated_with_key = _cfg(DELTA_REENTRY_FILTER_ENABLED=False)
    syn = _synth(speed_z=5.0)
    assert L.get("DELTA_REENTRY_Z_THRESHOLD", syn, 12, True, gated_with_key) is None


def test_delta_z_binds_synthetic_master_on():
    import vec_decisions.lane_vec_reentry2 as L
    cfg = _cfg(DELTA_REENTRY_FILTER_ENABLED=True, DELTA_REENTRY_Z_THRESHOLD=1.0)
    hi = _synth(speed_z=2.0)
    lo = _synth(speed_z=0.0)
    assert bool(L.get("DELTA_REENTRY_Z_THRESHOLD", hi, 12, True, cfg).all())
    m = L.get("DELTA_REENTRY_Z_THRESHOLD", lo, 12, True, cfg)
    assert m is not None and not m.any()


def test_delta_z_live_master_default_off():
    import v12_quick_engine as V
    assert V.QuickConfig().DELTA_REENTRY_FILTER_ENABLED is False
    assert V.QuickConfig().DELTA_REENTRY_Z_THRESHOLD == 1.0


# ── (3) REENTRY2_DIR_FAV_ENABLED: real AND-leg + fire hunk + flip ──

def test_dir_fav_dispatch_is_bar_mask_not_all_ones():
    import vec_decisions.lane_vec_reentry2 as L
    n = 8
    on = _cfg(LEGACY_DIRECTION_FAVORABLE=True,
              DIRECTION_FAVORABLE_REENTRY_ENABLED=True,
              REENTRY2_DIR_FAV_ENABLED=True)
    full = {"k_3m": np.full(n, 30.0), "d_3m": np.full(n, 20.0),
            "k_15m": np.full(n, 60.0), "d_15m": np.full(n, 40.0),
            "wt1_15m": np.full(n, 5.0), "wt2_15m": np.full(n, -5.0),
            "wt1_3m": np.full(n, 3.0), "wt2_3m": np.full(n, -3.0)}
    m = L.get("REENTRY2_DIR_FAV_ENABLED", full, n, True, on)
    assert m is not None and bool(m.all())
    partial = dict(full, k_15m=np.full(n, 10.0))
    m2 = L.get("REENTRY2_DIR_FAV_ENABLED", partial, n, True, on)
    assert m2 is not None and not m2.any()


def test_dir_fav_triple_gate_each_leg_kills():
    import vec_decisions.lane_vec_reentry2 as L
    n = 8
    full = {"k_3m": np.full(n, 30.0), "d_3m": np.full(n, 20.0),
            "k_15m": np.full(n, 60.0), "d_15m": np.full(n, 40.0),
            "wt1_15m": np.full(n, 5.0), "wt2_15m": np.full(n, -5.0),
            "wt1_3m": np.full(n, 3.0), "wt2_3m": np.full(n, -3.0)}
    assert L.get("REENTRY2_DIR_FAV_ENABLED", full, n, True, _cfg()) is None
    assert L.get("REENTRY2_DIR_FAV_ENABLED", full, n, True, _cfg(
        LEGACY_DIRECTION_FAVORABLE=True)) is None
    assert L.get("REENTRY2_DIR_FAV_ENABLED", full, n, True, _cfg(
        DIRECTION_FAVORABLE_REENTRY_ENABLED=True)) is None
    assert L.get("REENTRY2_DIR_FAV_ENABLED", full, n, True, _cfg(
        LEGACY_DIRECTION_FAVORABLE=True,
        DIRECTION_FAVORABLE_REENTRY_ENABLED=True,
        REENTRY2_DIR_FAV_ENABLED=False)) is None


def test_dir_fav_none_on_real_npz():
    import vec_decisions.lane_vec_reentry2 as L
    z = np.load(str(REAL_NPZ), allow_pickle=True)
    n = len(np.asarray(z["close"]).reshape(-1))
    on = _cfg(LEGACY_DIRECTION_FAVORABLE=True,
              DIRECTION_FAVORABLE_REENTRY_ENABLED=True,
              REENTRY2_DIR_FAV_ENABLED=True)
    assert L.get("REENTRY2_DIR_FAV_ENABLED", z, n, True, on) is None
    assert L.get("REENTRY2_DIR_FAV_ENABLED", z, n, False, on) is None


def test_dir_fav_k_extreme_excluded():
    import vec_decisions.lane_vec_reentry2 as L
    n = 8
    on = _cfg(LEGACY_DIRECTION_FAVORABLE=True,
              DIRECTION_FAVORABLE_REENTRY_ENABLED=True,
              REENTRY2_DIR_FAV_ENABLED=True)
    extreme = {"k_3m": np.full(n, 90.0), "d_3m": np.full(n, 20.0),
               "k_15m": np.full(n, 60.0), "d_15m": np.full(n, 40.0),
               "wt1_15m": np.full(n, 5.0), "wt2_15m": np.full(n, -5.0),
               "wt1_3m": np.full(n, 3.0), "wt2_3m": np.full(n, -3.0)}
    m = L.get("REENTRY2_DIR_FAV_ENABLED", extreme, n, True, on)
    assert m is not None and not m.any()


def _aug(base, n, is_long):
    aug = dict(base)
    if is_long:
        aug.update(k_3m=np.full(n, 30.0), d_3m=np.full(n, 20.0),
                   wt1_3m=np.full(n, 3.0), wt2_3m=np.full(n, -3.0))
    else:
        aug.update(k_3m=np.full(n, 30.0), d_3m=np.full(n, 50.0),
                   wt1_3m=np.full(n, -3.0), wt2_3m=np.full(n, 3.0))
    return aug


def test_dir_fav_ledger_flip_long():
    import v12_quick_engine as V
    z = np.load(str(REAL_NPZ), allow_pickle=True)
    base = {k: np.asarray(z[k]) for k in z.files}
    n = len(base["close"])
    aug = _aug(base, n, True)
    off = V.QuickConfig()
    r_off = V.simulate_one(dict(aug), "PROBE", True, off)
    on = V.QuickConfig()
    on.LEGACY_DIRECTION_FAVORABLE = True
    on.DIRECTION_FAVORABLE_REENTRY_ENABLED = True
    on.REENTRY2_DIR_FAV_ENABLED = True
    r_on = V.simulate_one(dict(aug), "PROBE", True, on)
    assert _fp(r_off["ledger"]) != _fp(r_on["ledger"])
    r_pure = V.simulate_one(dict(base), "PROBE", True, V.QuickConfig())
    assert _fp(r_pure["ledger"]) == _fp(r_off["ledger"])


def test_dir_fav_ledger_flip_short():
    import v12_quick_engine as V
    z = np.load(str(REAL_NPZ), allow_pickle=True)
    base = {k: np.asarray(z[k]) for k in z.files}
    n = len(base["close"])
    aug = _aug(base, n, False)
    off = V.QuickConfig()
    r_off = V.simulate_one(dict(aug), "PROBE", False, off)
    on = V.QuickConfig()
    on.LEGACY_DIRECTION_FAVORABLE = True
    on.DIRECTION_FAVORABLE_REENTRY_ENABLED = True
    on.REENTRY2_DIR_FAV_ENABLED = True
    r_on = V.simulate_one(dict(aug), "PROBE", False, on)
    assert _fp(r_off["ledger"]) != _fp(r_on["ledger"])
    r_pure = V.simulate_one(dict(base), "PROBE", False, V.QuickConfig())
    assert _fp(r_pure["ledger"]) == _fp(r_off["ledger"])


# ── (4) REENTRY_RALLY_K15M_MAX: predicate + EPQ veto + flip ──

def test_rally_predicate_semantics():
    import vec_decisions.lane_vec_reentry2 as L
    cfg = _cfg(REENTRY_RALLY_K15M_MAX=30.0)
    hi = _synth(k_15m=90.0)
    lo = _synth(k_15m=10.0)
    assert bool(L.get("REENTRY_RALLY_K15M_MAX", hi, 12, True, cfg).all())
    assert not L.get("REENTRY_RALLY_K15M_MAX", lo, 12, True, cfg).any()
    assert bool(L.get("REENTRY_RALLY_K15M_MAX", lo, 12, False, cfg).all())
    assert not L.get("REENTRY_RALLY_K15M_MAX", hi, 12, False, cfg).any()
    off = _cfg(REENTRY_RALLY_K15M_MAX=100.0)
    assert L.get("REENTRY_RALLY_K15M_MAX", hi, 12, True, off) is None
    assert L.get("REENTRY_RALLY_K15M_MAX", {"close": np.full(12, 1.0)}, 12, True, cfg) is None


def test_rally_epq_master_default_off_and_dead_twin_kept():
    import v12_quick_engine as V
    assert V.QuickConfig().REENTRY_EPQ_MODEL_ENABLED is False
    src = (ROOT / "v12_quick_engine.py").read_text()
    assert "if not _E.rally_k15_blocked(_n3_epq, i, cfg)" not in src
    assert "_E.rally_k15_blocked(_n3_epq, i, cfg)" in src


def test_rally_ledger_flip_epq_long():
    import v12_quick_engine as V
    z = np.load(str(REAL_NPZ), allow_pickle=True)
    base = {k: np.asarray(z[k]) for k in z.files}
    fps = {}
    for cap in (30.0, 100.0):
        cfg = V.QuickConfig()
        cfg.REENTRY_EPQ_MODEL_ENABLED = True
        cfg.REENTRY_RALLY_K15M_MAX = cap
        fps[cap] = _fp(V.simulate_one(dict(base), "PROBE", True, cfg)["ledger"])
    assert fps[30.0] != fps[100.0]


def test_rally_ledger_flip_epq_short():
    import v12_quick_engine as V
    z = np.load(str(REAL_NPZ), allow_pickle=True)
    base = {k: np.asarray(z[k]) for k in z.files}
    fps = {}
    for cap in (30.0, 100.0):
        cfg = V.QuickConfig()
        cfg.REENTRY_EPQ_MODEL_ENABLED = True
        cfg.REENTRY_RALLY_K15M_MAX = cap
        fps[cap] = _fp(V.simulate_one(dict(base), "PROBE", False, cfg)["ledger"])
    assert fps[30.0] != fps[100.0]
