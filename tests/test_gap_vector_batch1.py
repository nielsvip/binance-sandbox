"""Gap-vector batch 1 — verdict-pinning tests for the 24 tA+tB switches.

Verify-first outcome (parent brief 2026-10-21): ZERO of the 24 warrant new
vec_decisions predicates, because every candidate is already twin-covered
(imported AND called), dead-gated in live, sizing/infra (brief exclusions),
missing its NPZ key, or killed by user ruling. Wiring anything more would
duplicate an existing twin, violate the 2026-10-16 stoch_k ruling, or
fabricate. These tests pin each verdict's evidence so a future delta-log
re-audit cannot silently re-open them:

- QUEUED (twin staged, master off): SHORT_RSI_MIN_1H, ENTRY_ATR_PCT_MIN,
  WT_COMPOSITE_DELTA_SHORT_MAX, BASIS_CONDITION, ENTRY_VOL_MIN_RATIO,
  MANDATORY_REENTRY_K_LOW_BLOCK, DELTA_REENTRY_Z_THRESHOLD.
- PARITY-OK (twin imported + called): BEAR_MARKET_MODE,
  BOUNCE_AUGMENT_K_D_CROSSING_UP, REENTRY_RALLY_K15M_MAX (deliberately False
  per ruling), STDEV_BREAKOUT_RETEST_PCTB_MIN, WT_COMPOSITE_SCORING_ENABLED_TRADIER,
  SCALP_V3_K_OB_EXIT_K3M_HI/LO (3m legs honestly inert, 15m legs live).
- EXCLUDED-sizing: LR_BAND_LADDER_TOP_MULT, REENTRY_B16_SIZE_MULT_WEAK,
  STDEV_BOUNCE_SCORE, STDEV_SLOPE_SIZING_1H_MAX.
- EXCLUDED-infra: BB_SQUEEZE_COOLDOWN, REENTRY_COOLDOWN_S, VOL_SPIKE_COOLDOWN,
  EXECUTE_NOW_SINGLE_GATE_ENFORCE.
- FALSE-POSITIVE (live masters off): REENTRY2_DIR_FAV_ENABLED.

No Sharpe/gain/delta numbers anywhere: masks, None-states, and defaults only.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config as _live_config

REAL_NPZ = sorted((ROOT / "backtest_v8" / "indicators").glob("*.npz"))[0]


def _safe(npz, key, n, default=0.0):
    try:
        a = np.asarray(npz[key], dtype=float).reshape(-1)
    except Exception:
        return np.full(n, float(default))
    if a.size < n:
        return np.full(n, float(default))
    return a[:n]


def _cfg(**kw):
    return SimpleNamespace(**kw)


def _synth(n=12, **cols):
    d = {}
    for k, v in cols.items():
        d[k] = np.full(n, float(v))
    return d


# ── QUEUED: LIVE_ENTRY_HARD_GATES master off; twin works, staging owns it ──

def test_hard_gates_master_default_off():
    import v12_quick_engine as V
    assert V.QuickConfig().LIVE_ENTRY_HARD_GATES_ENABLED is False


def test_hard_gates_crypto_default_inert():
    import vec_decisions.entry_hard_gates as H
    npz = _synth(rsi_1h=10.0, atr_1h=0.0, close=100.0, wt_composite_delta=0.0,
                 dc_basis_15m=1.0, dc_basis_1h=1.0, dc_basis_4h=1.0, dc_basis_D=1.0)
    cfg = _cfg(SHORT_RSI_MIN_1H=0, ENTRY_ATR_PCT_MIN=0,
               WT_COMPOSITE_DELTA_GATE_ENABLED=True,
               WT_COMPOSITE_DELTA_LONG_MIN=-100.0,
               WT_COMPOSITE_DELTA_SHORT_MAX=100.0, BASIS_CONDITION=False)
    for is_long in (True, False):
        blk = H.crypto_block(npz, 12, is_long, cfg, npz["close"], _safe,
                             np.full(12, 50.0))
        assert blk.dtype == bool and not blk.any()


def test_hard_gates_short_rsi_binds_when_set():
    import vec_decisions.entry_hard_gates as H
    rsi = np.array([50.0] * 6 + [10.0] * 6)
    npz = _synth(close=100.0)
    npz["rsi_1h"] = rsi
    cfg = _cfg(SHORT_RSI_MIN_1H=40.0, ENTRY_ATR_PCT_MIN=0,
               WT_COMPOSITE_DELTA_GATE_ENABLED=False, BASIS_CONDITION=False)
    blk_short = H.crypto_block(npz, 12, False, cfg, npz["close"], _safe,
                               np.full(12, 50.0))
    assert blk_short.tolist() == [False] * 6 + [True] * 6
    blk_long = H.crypto_block(npz, 12, True, cfg, npz["close"], _safe,
                              np.full(12, 50.0))
    assert not blk_long.any()


def test_hard_gates_wt_cdelta_short_binds_when_set():
    import vec_decisions.entry_hard_gates as H
    cd = np.array([-50.0] * 6 + [60.0] * 6)
    npz = _synth(close=100.0)
    npz["wt_composite_delta"] = cd
    cfg = _cfg(SHORT_RSI_MIN_1H=0, ENTRY_ATR_PCT_MIN=0,
               WT_COMPOSITE_DELTA_GATE_ENABLED=True,
               WT_COMPOSITE_DELTA_SHORT_MAX=50.0, BASIS_CONDITION=False)
    blk = H.crypto_block(npz, 12, False, cfg, npz["close"], _safe,
                         np.full(12, 50.0))
    assert blk.tolist() == [False] * 6 + [True] * 6


def test_hard_gates_basis_default_inert_despite_violation():
    import vec_decisions.entry_hard_gates as H
    npz = _synth(close=1.0, dc_basis_15m=100.0, dc_basis_1h=100.0,
                 dc_basis_4h=100.0, dc_basis_D=100.0)
    cfg = _cfg(SHORT_RSI_MIN_1H=0, ENTRY_ATR_PCT_MIN=0,
               WT_COMPOSITE_DELTA_GATE_ENABLED=False, BASIS_CONDITION=False)
    blk = H.crypto_block(npz, 12, True, cfg, npz["close"], _safe,
                         np.full(12, 50.0))
    assert not blk.any()


def test_hard_gates_call_site_master_gated():
    src = (ROOT / "v12_quick_engine.py").read_text()
    i = src.find("import vec_decisions.entry_hard_gates as _ehg")
    assert i > 0
    assert 'getattr(cfg, "LIVE_ENTRY_HARD_GATES_ENABLED", False)' in src[max(0, i - 400):i]


# ── QUEUED: mandatory K needs k_3m (absent); delta-Z needs speed_z + master ──

def test_mandatory_k_low_none_on_real_npz_binds_on_synthetic():
    import vec_decisions.lane_vec_reentry2 as L
    z = np.load(str(REAL_NPZ), allow_pickle=True)
    n = len(np.asarray(z["close"]).reshape(-1))
    assert "k_3m" not in z.files
    assert L.mandatory_k_low_block_mask(z, n, False, _cfg()) is None
    syn = _synth(k_3m=10.0)
    m = L.mandatory_k_low_block_mask(syn, 12, False,
                                     _cfg(MANDATORY_REENTRY_K_LOW_BLOCK=20.0))
    assert m is not None and bool(m.all())
    assert L.mandatory_k_low_block_mask(syn, 12, True, _cfg()) is None


def test_delta_z_queued_master_off_and_no_npz_key():
    assert _live_config.Config().DELTA_REENTRY_FILTER_ENABLED is False
    z = np.load(str(REAL_NPZ), allow_pickle=True)
    assert "speed_z" not in z.files


# ── FALSE-POSITIVE: direction-favorable masters off in live ──

def test_dir_fav_masters_off_in_live_config():
    c = _live_config.Config()
    assert c.DIRECTION_FAVORABLE_REENTRY_ENABLED is False
    assert c.LEGACY_DIRECTION_FAVORABLE is False


# ── PARITY-OK: rally twins deliberately False per 2026-10-16 ruling ──

def test_rally_twins_always_false():
    import vec_decisions.reentry_epq_vec as E
    import vec_decisions.reentry_tiers as T
    assert E.rally_k15_blocked(None, 0, _cfg(REENTRY_RALLY_K15M_MAX=30.0)) is False
    assert T.rally_k15_blocked(99.0, True, _cfg(REENTRY_RALLY_K15M_MAX=30.0)) is False
    assert T.rally_k15_blocked(1.0, False, _cfg(REENTRY_RALLY_K15M_MAX=30.0)) is False


# ── PARITY-OK: stdev retest consumes the switch ──

def test_stdev_retest_pctb_min_consumed():
    import vec_decisions.stdev_breakout_crypto as S
    n = 4
    npz = _synth(bb_pct_b_D=1.5, bb_pct_b_4h=1.5, bb_pct_b_1h=0.90,
                 bb_pct_b_15m=0.90, relative_volume_D=2.0,
                 relative_volume_4h=2.0, relative_volume_1h=2.0,
                 relative_volume_15m=2.0, stoch_k_1h=50.0, stoch_d_1h=40.0,
                 stoch_k_15m=50.0, stoch_d_15m=40.0)
    base = dict(STDEV_BREAKOUT_ENABLED=True, STDEV_BREAKOUT_PCTB_LONG=1.125,
                STDEV_BREAKOUT_RVOL_MIN=1.2, STDEV_BREAKOUT_MAX_RETESTS=3,
                STDEV_BREAKOUT_MAX_AGE_BARS=50,
                STDEV_BREAKOUT_RETEST_PCTB_MAX=1.05)
    lo = S.fires(npz, n, True, _cfg(STDEV_BREAKOUT_RETEST_PCTB_MIN=0.85, **base),
                 _safe)
    hi = S.fires(npz, n, True, _cfg(STDEV_BREAKOUT_RETEST_PCTB_MIN=0.95, **base),
                 _safe)
    assert lo is not None and hi is not None
    assert lo.any() and hi.sum() == 1 and lo.sum() > hi.sum()


# ── PARITY-OK: composite scoring flag consumed by vec gate ──

def test_wt_composite_scoring_tradier_flag_consumed():
    import vec_decisions.wt_composite_gate as W
    npz = _synth(wt_bear_alignment=0.0, wt_composite_short=-50.0)
    off = W.block_mask(npz, 12, False, _cfg(WT_COMPOSITE_HTF_GATE=True,
                                            WT_COMPOSITE_SCORING_ENABLED=False,
                                            WT_COMPOSITE_SCORING_ENABLED_TRADIER=False),
                       _safe)
    assert off is None
    on = W.block_mask(npz, 12, False, _cfg(WT_COMPOSITE_HTF_GATE=True,
                                           WT_COMPOSITE_SCORING_ENABLED=False,
                                           WT_COMPOSITE_SCORING_ENABLED_TRADIER=True),
                      _safe)
    assert on is not None and bool(on.all())


# ── PARITY-OK: bear twin mirrors the live LONG block ──

def test_bear_twin_blocks_long_only_when_enabled():
    import vec_decisions.lane_vec_gates2 as G2
    npz = _synth(k_15m=50.0)
    m = G2.bear_mode_block_mask(npz, 12, True, _cfg(BEAR_MARKET_MODE=True))
    assert m is not None and bool(m.all())
    assert G2.bear_mode_block_mask(npz, 12, False, _cfg(BEAR_MARKET_MODE=True)) is None
    assert G2.bear_mode_block_mask(npz, 12, True, _cfg(BEAR_MARKET_MODE=False)) is None


# ── PARITY-OK: bounce augment dead on both sides at defaults ──

def test_bounce_augment_both_dead_at_default():
    import v12_quick_engine as V
    assert V.QuickConfig().BOUNCE_AUGMENT_ENABLED is False
    assert _live_config.Config().BOUNCE_AUGMENT_ENABLED is False


# ── PARITY-OK: scalp K3M legs honestly inert, K15M legs live ──

def test_scalp_k3m_none_on_real_npz_k15m_binds():
    import vec_decisions.lane_vec_scalp_v3 as S3
    z = np.load(str(REAL_NPZ), allow_pickle=True)
    n = len(np.asarray(z["close"]).reshape(-1))
    assert S3.scalp_v3_k_ob_k3m_hi_mask(z, n, True, _cfg()) is None
    assert S3.scalp_v3_k_ob_k3m_lo_mask(z, n, False, _cfg()) is None
    syn = _synth(k_15m=90.0)
    m = S3.scalp_v3_k_ob_k15m_hi_mask(syn, 12, True,
                                      _cfg(SCALP_V3_K_OB_EXIT_K15M_HI=80.0))
    assert m is not None and bool(m.all())


# ── EXCLUDED-sizing: score modulates qty, never the fire ──

def test_stdev_bounce_score_absent_from_vec_fire():
    import vec_decisions.stdev_bounce_crypto as S
    npz = _synth(bb_pct_b_D=0.01, bb_pct_b_4h=0.01,
                 relative_volume_D=2.0, relative_volume_4h=2.0,
                 relative_volume_15m=2.0)
    a = S.fires(npz, 12, True, _cfg(STDEV_BOUNCE_ENABLED=True,
                                    STDEV_BOUNCE_SCORE=22), _safe)
    b = S.fires(npz, 12, True, _cfg(STDEV_BOUNCE_ENABLED=True,
                                    STDEV_BOUNCE_SCORE=999), _safe)
    assert a is not None and b is not None
    assert (np.asarray(a) == np.asarray(b)).all()


def test_stdev_bounce_score_flows_to_quantity_in_live_source():
    src = (ROOT / "ez_positions_quick.py").read_text()
    assert 'score = max(score, _sboun_result["score"])' in src
    assert "qty = calculate_dynamic_quantity(" in src


def test_b16_weak_mult_is_quantity_multiplier_in_live_source():
    assert "re_qty * _mult" in (ROOT / "ez_positions_quick.py").read_text()


def test_lr_ladder_top_mult_is_ladder_sizing_in_live_source():
    assert "_strongest_ladder_target" in (ROOT / "tradier_manage.py").read_text()


def test_slope_sizing_max_inside_quantity_function_in_live_source():
    src = (ROOT / "ez_positions_quick.py").read_text()
    i = src.find("STDEV_SLOPE_SIZING_1H_MAX")
    assert i > 0
    assert "def calculate_dynamic_quantity(" in src[src.rfind("\ndef ", 0, i):i]


# ── EXCLUDED-infra: wall-clock cooldowns; webhook-route gate ──

def test_cooldowns_are_wall_clock_in_live_source():
    eq = (ROOT / "ez_positions_quick.py").read_text()
    ez = (ROOT / "ez_manage.py").read_text()
    assert "time.time() - _vol_spike_last.get(_vs_sym_key, 0.0)" in eq
    assert "time.time() - _bb_squeeze_last.get(_bbs_sym_key, 0.0)" in eq
    assert "(time.time() - _last) < REENTRY_COOLDOWN" in eq
    assert "(time.time() - _last) < REENTRY_COOLDOWN" in ez


def test_execute_now_gate_lives_in_webhook_alert_path():
    src = (ROOT / "ez_positions_quick.py").read_text()
    i = src.find("def send_webhook")
    assert i > 0
    assert 'getattr(config, "EXECUTE_NOW_SINGLE_GATE_ENFORCE", True)' in src[i:i + 1500]
