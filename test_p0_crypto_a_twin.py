"""test_p0_crypto_a_twin.py — live-twin ⟺ vector-predicate agreement for P0 crypto A.

Each test feeds the SAME synthetic inputs to the twin scalar and the transcribed
vector reference (vec_decisions/twin_p0_crypto_a.py) and asserts equality.
No engine import: v12_quick_engine is unimportable in this checkout (missing
vec_decisions.* modules); vector formulas are transcribed with line citations,
same as prior twin waves.
"""
import os
from types import SimpleNamespace

import numpy as np

import vec_decisions.twin_p0_crypto_a as T


def _cfg(**kw):
    base = dict(MODE='crypto', COOLDOWN_BARS=3)
    base.update(kw)
    return SimpleNamespace(**base)


# ── 1-5. ablation ─────────────────────────────────────────────────────────────
def test_ablation_defaults_inert():
    e, x, a, r = T.vec_apply_ablation(16, _cfg())
    assert not e.any() and not x.any() and not a.any() and not r.any()
    assert T.ablation_ratio_rebalance_kills(_cfg(ABLATION_DISABLE_RATIO_REBALANCE=True)) == (False, False, False)
    assert T.ablation_spike_fade_kills_exit(_cfg(ABLATION_DISABLE_SPIKE_FADE_EXIT=True)) is False


def test_ablation_augment_reduce_kill_all_bars():
    cfg = _cfg(ABLATION_DISABLE_AGGRESSIVE_HEDGE=True, ABLATION_DISABLE_DC_BREACH_REDUCE=True)
    e, x, a, r = T.vec_apply_ablation(8, cfg)
    assert a.all() and r.all() and not e.any() and not x.any()
    assert T.ablation_aggressive_hedge_kills_augment(cfg) is True
    assert T.ablation_dc_breach_kills_reduce(cfg) is True
    cfg2 = _cfg(ABLATION_DISABLE_HIGH_GAIN_AUGMENT=True)
    assert T.vec_apply_ablation(8, cfg2)[2].all()
    assert T.ablation_high_gain_kills_augment(cfg2) is True


# ── 6. ADX trending ────────────────────────────────────────────────────────────
def test_adx_trending_agrees():
    adx = np.array([0.0, 24.9, 25.0, 30.0, 100.0])
    cfg = _cfg()
    np.testing.assert_array_equal(T.vec_trending(adx, cfg), np.array([T.live_trending(v, cfg) for v in adx]))
    assert T.live_trending(25.0, cfg) is True  # >= boundary
    cfg2 = _cfg(ADX_TRENDING_THRESHOLD=30.0)
    np.testing.assert_array_equal(T.vec_trending(adx, cfg2), np.array([T.live_trending(v, cfg2) for v in adx]))
    assert T.live_trending(25.0, cfg2) is False


# ── 7. augment_allowed ─────────────────────────────────────────────────────────
def test_augment_allowed_default_branch():
    cfg = _cfg()
    assert T.augment_allowed(cfg, -0.5) is True   # <= -0.5 boundary
    assert T.augment_allowed(cfg, -0.49) is False
    assert T.augment_allowed(cfg, 5.0) is False
    cfg2 = _cfg(BOUNCE_AUGMENT_MIN_LOSS_PCT=-1.0)
    assert T.augment_allowed(cfg2, -0.9) is False
    assert T.augment_allowed(cfg2, -1.0) is True


def test_augment_allowed_tradier_flag_strict_profit():
    cfg = _cfg(AUGMENT_ONLY_WHEN_PROFITABLE_TRADIER=True)
    assert T.augment_allowed(cfg, 0.0) is False  # strict >
    assert T.augment_allowed(cfg, 0.01) is True
    assert T.augment_allowed(cfg, -5.0) is False
    pnls = np.array([-2.0, -0.5, 0.0, 0.5])
    np.testing.assert_array_equal(T.vec_augment_allowed(cfg, pnls), np.array([False, False, False, True]))
    np.testing.assert_array_equal(T.vec_augment_allowed(_cfg(), pnls), np.array([True, True, False, False]))


# ── 8. validated gates ─────────────────────────────────────────────────────────
def test_validated_gates_agree_and_tradier_only():
    assert T.validated_tf_need(_cfg()) == 1
    assert T.validated_tf_need(_cfg(TF_ALIGNMENT_MIN_TOTAL=0)) == 1
    assert T.validated_tf_need(_cfg(TF_ALIGNMENT_MIN_TOTAL=8)) == 2
    assert T.validated_tf_need(_cfg(TF_ALIGNMENT_MIN_TOTAL=36)) == 3
    assert T.validated_tf_need(_cfg(TF_ALIGNMENT_MIN_TOTAL=400)) == 3
    # crypto mode: gate inert even when flag on
    assert T.validated_gate_pass(_cfg(BACKTEST_VALIDATED_GATES_TRADIER=True), 1, 0, 1, 0, 1, 0, True) is True
    tr = _cfg(MODE='tradier', BACKTEST_VALIDATED_GATES_TRADIER=True, TF_ALIGNMENT_MIN_TOTAL=8)
    assert T.validated_gate_pass(tr, 1, 0, 1, 0, 0, 0, True) is True   # cnt=2 >= 2
    assert T.validated_gate_pass(tr, 1, 0, 0, 1, 0, 1, True) is False  # cnt=1 < 2
    assert T.validated_tf_count(1, 0, 1, 0, 1, 0, True) == 3
    assert T.validated_tf_count(1, 0, 1, 0, 1, 0, False) == 0


# ── 9. BB recovery ─────────────────────────────────────────────────────────────
def test_bb_recovery_agrees():
    n = 20
    rng = np.random.default_rng(7)
    close = rng.uniform(90, 110, n)
    up = rng.uniform(105, 115, n)
    lo = rng.uniform(85, 95, n)
    k = rng.uniform(0, 100, n)
    for is_long in (True, False):
        vec = T.vec_bb_recovery_exit(close, up, lo, k, is_long)
        scal = np.array([T.bb_recovery_exit(close[i], up[i], lo[i], k[i], is_long) for i in range(n)])
        np.testing.assert_array_equal(vec, scal)
    assert T.bb_recovery_exit(100.0, 110.0, 90.0, 70.0, True) is True   # >= mid, >= 70
    assert T.bb_recovery_exit(100.0, 110.0, 90.0, 69.9, True) is False
    assert T.bb_recovery_exit(100.0, 110.0, 90.0, 30.0, False) is True  # <= mid, <= 30
    assert T.bb_recovery_exit(100.1, 110.0, 90.0, 30.0, False) is False


# ── 10-11. CHOP ────────────────────────────────────────────────────────────────
def test_chop_agrees_and_ranging_unused():
    cfg = _cfg()
    rows = [(10.0, 30.0, 1.0), (38.2, 30.0, 1.0), (38.3, 30.0, 1.0),
            (10.0, 24.9, 1.0), (10.0, 30.0, 0.49), (50.0, 10.0, 0.1)]
    for ch, adx, bbw in rows:
        assert T.chop_gate_ok(ch, adx, bbw, cfg) == bool(T.vec_chop_ok([ch], [adx], [bbw], cfg)[0])
    assert T.chop_gate_ok(10.0, 30.0, 1.0, cfg) is True
    assert T.chop_gate_ok(38.3, 30.0, 1.0, cfg) is False   # chop > 38.2
    assert T.chop_gate_ok(38.2, 30.0, 1.0, cfg) is True    # strict >
    assert T.chop_gate_ok(10.0, 24.9, 1.0, cfg) is False   # adx < 25
    assert T.chop_gate_ok(10.0, 30.0, 0.49, cfg) is False  # bbw < 0.5
    # ranging threshold read-but-unused: any value, same output
    for rt in (0.0, 61.8, 999.0):
        assert T.chop_gate_ok(50.0, 10.0, 0.1, _cfg(CHOP_RANGING_THRESHOLD=rt)) is False
    assert T.chop_gate_applies(_cfg()) is False
    assert T.chop_gate_applies(_cfg(CT_CHOP_4H_GATE_ENABLED=True)) is True


# ── 12. combined stoch ─────────────────────────────────────────────────────────
def test_stoch_gate_agrees():
    cfg = _cfg()
    ks = np.array([0.0, 20.0, 80.0, 100.0])
    np.testing.assert_array_equal(T.vec_stoch_gate_pass(ks, cfg, True), np.ones(4, dtype=bool))
    assert T.stoch_gate_pass(0.0, cfg, True) is True  # csg=100 inert
    cfg2 = _cfg(COMBINED_STOCH_GATE_TRADIER=20.0)
    np.testing.assert_array_equal(T.vec_stoch_gate_pass(ks, cfg2, True), np.array([T.stoch_gate_pass(v, cfg2, True) for v in ks]))
    np.testing.assert_array_equal(T.vec_stoch_gate_pass(ks, cfg2, False), np.array([T.stoch_gate_pass(v, cfg2, False) for v in ks]))
    assert T.stoch_gate_pass(19.9, cfg2, True) is True
    assert T.stoch_gate_pass(20.0, cfg2, True) is False
    assert T.stoch_gate_pass(80.1, cfg2, False) is True   # > 100-20
    assert T.stoch_gate_pass(80.0, cfg2, False) is False
    assert T.resolve_csg_k(True, 11.0, 22.0) == 11.0
    assert T.resolve_csg_k(False, 11.0, 22.0) == 22.0


# ── 13-14. cooldown ────────────────────────────────────────────────────────────
def test_cooldown_branches():
    assert T.cooldown_bars(_cfg()) == 3
    assert T.cooldown_bars(_cfg(COOLDOWN_BARS=0)) == 0
    assert T.cooldown_bars(_cfg(MODE='tradier', COOLDOWN_BARS=3)) == 3  # getattr default = base
    assert T.cooldown_bars(_cfg(MODE='tradier', COOLDOWN_BARS=3, COOLDOWN_BARS_TRADIER=9)) == 9
    assert T.cooldown_bars(_cfg(MODE='crypto', COOLDOWN_BARS=3, COOLDOWN_BARS_TRADIER=9)) == 3


# ── 15. DC break align ─────────────────────────────────────────────────────────
def test_htf_align_agrees():
    assert T.wt_bear_count(1, 2, 1, 2, 2, 1) == 2
    assert T.htf_align_ok(2, _cfg()) is True
    assert T.htf_align_ok(1, _cfg()) is False
    assert T.htf_align_ok(1, _cfg(DC_BREAK_LOW_HTF_ALIGN_MIN=1)) is True
    assert T.htf_align_ok(2, _cfg(DC_BREAK_LOW_HTF_ALIGN_MIN='3')) is False  # int(float()) cast


# ── 16-17. daytrade levels ──────────────────────────────────────────────────────
def test_daytrade_levels_and_master():
    assert T.daytrade_on(_cfg()) is False
    assert T.daytrade_on(_cfg(DC_DAYTRADE_ENABLED=True)) is True
    assert T.daytrade_on(_cfg(MODE='tradier', TRADIER_DC_DAYTRADE_ENABLED=True)) is True
    assert T.daytrade_levels(_cfg()) == (1.5, 1.0)  # 0.015*100, 0.01*100
    assert T.daytrade_levels(_cfg(MODE='tradier')) == (0.5, 0.5)
    assert T.daytrade_levels(_cfg(DC_DAYTRADE_STOP_PCT=0.02, DC_DAYTRADE_TARGET_PCT=0.03)) == (2.0, 3.0)


# ── 18. hard-stop cooldown ─────────────────────────────────────────────────────
def test_dc_hardstop_cd():
    assert T.dc_hardstop_cd(3, _cfg(), False, 15) == 3            # crypto: untouched
    assert T.dc_hardstop_cd(3, _cfg(), True, 15) == 16            # 4h*60/15
    assert T.dc_hardstop_cd(99, _cfg(), True, 15) == 99           # max()
    assert T.dc_hardstop_cd(0, _cfg(DC_HARD_STOP_REENTRY_COOLDOWN_HOURS=0), True, 15) == 0
    assert T.dc_hardstop_cd(0, _cfg(DC_HARD_STOP_REENTRY_COOLDOWN_HOURS=None), True, 15) == 0  # `or 0.0`
    assert T.dc_hardstop_cd(0, _cfg(), True, 0) == 240            # max(bmin,1)


# ── 19. dc pos entry ────────────────────────────────────────────────────────────
def test_dcpos_agrees():
    assert T.dcpos_master_on(_cfg()) is False
    assert T.dcpos_master_on(_cfg(DC_DAYTRADE_ENABLED=True)) is True
    assert T.dcpos_thr(_cfg()) == 0.15
    assert T.dcpos_thr(_cfg(MODE='tradier', TRADIER_DC_POSITION_ENTRY_THRESHOLD=0.2)) == 0.2
    a = np.array([0.0, 0.14, 0.15, 0.85, 0.86, 1.0])
    e = np.array([True, True, True, True, True, False])
    np.testing.assert_array_equal(T.vec_daytrade_entry_ok(a, 0.15, True, e),
                                  np.array([T.daytrade_entry_ok(v, 0.15, True, bool(ee)) for v, ee in zip(a, e)]))
    np.testing.assert_array_equal(T.vec_daytrade_entry_ok(a, 0.15, False, e),
                                  np.array([T.daytrade_entry_ok(v, 0.15, False, bool(ee)) for v, ee in zip(a, e)]))
    assert T.daytrade_entry_ok(0.14, 0.15, True) is True
    assert T.daytrade_entry_ok(0.15, 0.15, True) is False
    assert T.daytrade_entry_ok(0.86, 0.15, False) is True   # > 1-0.15
    assert T.daytrade_entry_ok(0.85, 0.15, False) is False
    assert T.daytrade_entry_ok(0.0, 0.15, True, expansion_ok=False) is False


# ── 20. delta ATR filter ───────────────────────────────────────────────────────
def test_atr_filter_agrees():
    assert T.atr_entry_master_on(_cfg()) is False
    assert T.atr_entry_master_on(_cfg(DELTA_ATR_ENTRY_FILTER=True)) is True
    m = np.array([0.0, 0.29, 0.30, 1.0])
    a = np.array([0.0, 1.0, 1.0, 1.0])
    np.testing.assert_array_equal(T.vec_atr_entry_ok(m, a),
                                  np.array([T.atr_entry_ok(mm, aa) for mm, aa in zip(m, a)]))
    assert T.atr_entry_ok(0.0, 0.0) is True    # atr<=0 passes
    assert T.atr_entry_ok(0.29, 1.0) is False
    assert T.atr_entry_ok(0.30, 1.0) is True   # >= atr*0.3


# ── 21. delta exit DC floor gates ──────────────────────────────────────────────
def test_delta_exit_dc_floor_needs_all_three():
    assert T.delta_exit_dc_floor_applies(_cfg()) is False
    assert T.delta_exit_dc_floor_applies(_cfg(MODE='tradier', DELTA_EXIT_DC_FLOOR=True)) is False
    full = _cfg(MODE='tradier', DELTA_EXIT_DC_FLOOR=True, STOCKS_LIVE_TWINS_ENABLED=True)
    assert T.delta_exit_dc_floor_applies(full) is True
    assert T.delta_exit_dc_floor_applies(_cfg(MODE='crypto', DELTA_EXIT_DC_FLOOR=True, STOCKS_LIVE_TWINS_ENABLED=True)) is False


# ── 22-24. delta gates ─────────────────────────────────────────────────────────
def test_delta_gates_honor_locked():
    for fn in (T.delta_gate_open_allows, T.delta_gate_reentry_allows, T.delta_gate_augment_allows):
        assert fn(_cfg()) is True
    # gate False alone changes NOTHING (honor flag required — C2 b4)
    assert T.delta_gate_open_allows(_cfg(DELTA_GATE_OPEN=False)) is True
    assert T.delta_gate_reentry_allows(_cfg(DELTA_GATE_REENTRY=False)) is True
    assert T.delta_gate_augment_allows(_cfg(DELTA_GATE_AUGMENT=False)) is True
    h = _cfg(VEC_HONOR_DEAD_LIVE_DELTA_GATES=True)
    assert T.delta_gate_open_allows(h) is True
    assert T.delta_gate_open_allows(_cfg(VEC_HONOR_DEAD_LIVE_DELTA_GATES=True, DELTA_GATE_OPEN=False)) is False
    assert T.delta_gate_reentry_allows(_cfg(VEC_HONOR_DEAD_LIVE_DELTA_GATES=True, DELTA_GATE_REENTRY=False)) is False
    assert T.delta_gate_augment_allows(_cfg(VEC_HONOR_DEAD_LIVE_DELTA_GATES=True, DELTA_GATE_AUGMENT=False)) is False


# ── 25. delta max hold ─────────────────────────────────────────────────────────
def test_delta_max_hold():
    assert T.delta_max_hold_bars(_cfg()) == 0
    assert T.delta_max_hold_bars(_cfg(DELTA_MAX_HOLD_BARS=50)) == 0  # engine off
    assert T.delta_max_hold_bars(_cfg(DELTA_ENGINE_ENABLED=True, DELTA_MAX_HOLD_BARS=50)) == 50
    assert T.delta_max_hold_bars(_cfg(DELTA_ENGINE_ENABLED=True)) == 0


# ── 26. dyn trail ──────────────────────────────────────────────────────────────
def test_dyn_trail():
    assert T.dyn_trail_applies(_cfg(DYN_STRUCT_TRAIL_ENABLED=True)) is False  # crypto
    assert T.dyn_trail_applies(_cfg(MODE='tradier', DYN_STRUCT_TRAIL_ENABLED=True)) is True
    assert T.dyn_trail_field(True, _cfg()) == 'dc_low_4h'
    assert T.dyn_trail_field(False, _cfg()) == 'dc_high_4h'
    assert T.dyn_trail_field(True, _cfg(DYN_STRUCT_TRAIL_TF='1h')) == 'dc_low_1h'
    assert T.dyn_trail_field(True, _cfg(DYN_STRUCT_TRAIL_TF='')) == 'dc_low_4h'  # `or '4h'`


# ── 27. force min one trade ────────────────────────────────────────────────────
def test_force_first_index():
    close = np.array([0.0, 0.0, 100.0, 101.0])
    assert T.force_first_index(close, np.zeros(4, dtype=bool), _cfg()) is None
    assert T.force_first_index(close, np.zeros(4, dtype=bool), _cfg(FORCE_MIN_ONE_TRADE=True)) == 2
    base = np.zeros(4, dtype=bool)
    base[1] = True
    assert T.force_first_index(close, base, _cfg(FORCE_MIN_ONE_TRADE=True)) is None  # entries exist
    assert T.force_first_index(np.zeros(4), np.zeros(4, dtype=bool), _cfg(FORCE_MIN_ONE_TRADE=True)) == 0


# ── 28-40. gap families ────────────────────────────────────────────────────────
def test_gap_calendar_math():
    cfg = _cfg()
    assert T.gap_bar_min(cfg) == 15
    assert T.gap_bar_min(_cfg(BASE_TF='5m')) == 5
    assert T.gap_bar_min(_cfg(BASE_TF='1h')) == 1
    assert T.gap_bar_min(_cfg(BASE_TF='???')) == 15
    assert T.gap_bars_per_day(cfg) == 26  # max(26, 390/15)
    assert T.gap_bars_90m(cfg) == 6
    assert T.gap_in_window(25, 26, 6) is True
    assert T.gap_in_window(19, 26, 6) is False
    assert T.gap_in_window(20, 26, 6) is True
    assert T.gap_at_deadline(25, 26) is True
    assert T.gap_at_deadline(24, 26) is False


def test_gap_or_fallback_semantics():
    assert T.gap_lookback_days(_cfg()) == 30
    assert T.gap_lookback_days(_cfg(GAP_PER_SYMBOL_LOOKBACK_DAYS=0)) == 20       # 0 falls through
    assert T.gap_lookback_days(_cfg(GAP_PER_SYMBOL_LOOKBACK_DAYS=0, GAP_INVENTORY_LOOKBACK_DAYS=7)) == 7
    assert T.gap_avg_threshold(_cfg()) == 0.10
    assert T.gap_avg_threshold(_cfg(GAP_PER_SYMBOL_AVG_THRESH_PCT=0.0)) == 0.30  # 0.0 falls through
    assert T.gap_avg_threshold(_cfg(GAP_PER_SYMBOL_AVG_THRESH_PCT=0.0, GAP_MOC_HOLD_POSITIVE_BIAS_PCT=0.5)) == 0.5


def test_gap_pct_and_fire_agree():
    o = np.array([0.0, 100.0, 100.0, 100.0])
    p = np.array([100.0, 0.0, 100.0, 99.0])
    np.testing.assert_allclose(T.vec_gap_pct(o, p), np.array([T.gap_pct(x, y) for x, y in zip(o, p)]))
    assert T.gap_pct(100.0, 99.0) == (1.0 / 99.0 * 100.0)
    assert T.gap_should(-0.11, 0.10, True) is True
    assert T.gap_should(-0.10, 0.10, True) is False
    assert T.gap_should(0.11, 0.10, False) is True
    assert T.gap_should(0.10, 0.10, False) is False
    assert T.gap_is_top(1.0, 2.0, True) is True
    assert T.gap_is_top(1.0, 2.0, False) is False
    w = np.array([True, True, False, True])
    s = np.array([True, True, True, False])
    t = np.array([True, False, True, True])
    d = np.array([False, True, False, True])
    np.testing.assert_array_equal(T.vec_gap_fire(w, s, t, d, True),
                                  np.array([T.gap_fire(*x, True) for x in zip(w, s, t, d)]))
    np.testing.assert_array_equal(T.vec_gap_fire(w, s, t, d, False),
                                  np.array([T.gap_fire(*x, False) for x in zip(w, s, t, d)]))
    assert T.gap_fire(False, True, True, True, True) is True    # deadline force w/o window
    assert T.gap_fire(False, True, True, True, False) is False
    assert T.gap_force_moc(_cfg()) is True


def test_gap_vv_override():
    cfg = _cfg()  # prox 0.50
    assert T.gap_vv_override(cfg, 100.4, 100.0, 1.0, 2.0, 5.0, 6.0, True) is True   # near high + WT against
    assert T.gap_vv_override(cfg, 100.4, 100.0, 2.0, 1.0, 6.0, 5.0, True) is False  # WT with
    assert T.gap_vv_override(cfg, 101.0, 100.0, 1.0, 2.0, 5.0, 6.0, True) is False  # not near
    assert T.gap_vv_override(cfg, 99.6, 100.0, 2.0, 1.0, 6.0, 5.0, False) is True
    assert T.gap_vv_override(cfg, 100.4, 0.0, 1.0, 2.0, 5.0, 6.0, True) is False    # px<=0 safe
    assert T.gap_vv_override(_cfg(GAP_MOC_DC_PROXIMITY_PCT=2.0), 101.0, 100.0, 1.0, 2.0, 5.0, 6.0, True) is True


def test_gap_moc_gates_tradier_only():
    g = T.gap_moc_gates(_cfg())
    assert g['enabled'] is True and g['applies'] is False  # crypto
    g2 = T.gap_moc_gates(_cfg(MODE='tradier'))
    assert g2['applies'] is True
    assert T.gap_moc_gates(_cfg(MODE='tradier', GAP_MOC_EXIT_ENABLED=False))['applies'] is False
    assert T.gap_moc_gates(_cfg(MODE='tradier', PARITY_DISABLE_NON_VECTORIZABLE=True))['applies'] is False
    os.environ['V12_PARITY_MIN_DECISION_TF'] = '15m'
    try:
        assert T.gap_moc_gates(_cfg(MODE='tradier'))['applies'] is False
    finally:
        del os.environ['V12_PARITY_MIN_DECISION_TF']


def test_close_gap_gates_and_should():
    g = T.close_gap_gates(_cfg(), False)
    assert g['applies'] is False  # only_stocks default
    g2 = T.close_gap_gates(_cfg(MODE='tradier'), False)
    assert g2['applies'] is True
    assert T.close_gap_gates(_cfg(MODE='tradier'), True)['applies'] is False  # only shorts
    assert T.close_gap_gates(_cfg(MODE='crypto', GAP_CLOSE_MOC_ONLY_STOCKS=False), False)['applies'] is True
    assert T.close_gap_gates(_cfg(MODE='tradier', GAP_CLOSE_MOC_EXIT_ENABLED=False), False)['applies'] is False
    assert T.close_gap_pct(101.0, 100.0) == 1.0
    assert T.close_gap_pct(0.0, 100.0) == 0.0
    assert T.close_gap_lookback_days(_cfg()) == 30
    assert T.close_gap_thr(_cfg()) == 0.10
    assert T.close_gap_should(0.11, 0.10, False, True) is True
    assert T.close_gap_should(0.10, 0.10, False, True) is False
    assert T.close_gap_should(-0.11, 0.10, True, True) is False   # longs blocked by only_shorts
    assert T.close_gap_should(-0.11, 0.10, True, False) is True
    assert T.close_gap_force_moc(_cfg()) is True


# ── 41-47. gap risk exit ───────────────────────────────────────────────────────
def _gr_cfg(**kw):
    base = dict(GAP_RISK_EXIT_ENABLED=True, GAP_RISK_EXIT_SHORT_ENABLED=True,
                GAP_RISK_EXIT_LONG_ENABLED=True, GAP_RISK_EXIT_COND_A_ENABLED=True)
    base.update(kw)
    return SimpleNamespace(**base)


def test_gap_risk_gates_or_semantics():
    assert T.gap_risk_gates(_gr_cfg(), False)['applies'] is True
    g = T.gap_risk_gates(_gr_cfg(GAP_RISK_EXIT_COND_A_ENABLED=False), False)
    assert g['a'] is False and g['applies'] is False
    g2 = T.gap_risk_gates(_gr_cfg(GAP_RISK_EXIT_COND_A_ENABLED=False, GAP_RISK_EXIT_OPEN_RECLAIM_ENABLED=True), False)
    assert g2['a'] is True and g2['applies'] is True      # OPEN_RECLAIM OR COND_A
    g3 = T.gap_risk_gates(_gr_cfg(GAP_RISK_EXIT_COND_A_ENABLED=False, GAP_RISK_EXIT_COND_B_ENABLED=True), False)
    assert g3['b'] is True and g3['applies'] is True      # COND_B alone suffices
    g4 = T.gap_risk_gates(_gr_cfg(GAP_RISK_EXIT_COND_A_ENABLED=False, GAP_RISK_EXIT_STRUCTURE_BREAK_ENABLED=True), False)
    assert g4['b'] is True
    assert T.gap_risk_gates(_gr_cfg(GAP_RISK_EXIT_ENABLED=False), False)['applies'] is False
    assert T.gap_risk_gates(_gr_cfg(GAP_RISK_EXIT_SHORT_ENABLED=False), False)['applies'] is False
    assert T.gap_risk_gates(_gr_cfg(GAP_RISK_EXIT_LONG_ENABLED=False), True)['applies'] is False
    assert T.gap_risk_gates(SimpleNamespace(), False)['applies'] is False  # bare ns: False defaults


def _gr_series_short():
    # short gap: open_D > prev_close; bar1 retraces (low<=prev) w/o trigger,
    # bar3 COND_A fires (close>open). State resets on fire.
    return dict(open_D=np.array([0.0, 105.0, 105.0, 105.0, 105.0]),
                close_D_prev=np.array([0.0, 100.0, 100.0, 100.0, 100.0]),
                high_D=np.array([0.0, 104.0, 104.5, 107.0, 104.0]),
                low_D=np.array([0.0, 99.0, 101.0, 102.0, 103.0]),
                close_D=np.array([0.0, 103.0, 104.0, 106.0, 103.5]),
                close=np.array([50.0, 103.0, 104.0, 106.0, 103.5]))


def test_gap_risk_scalar_steps_match_vec_short():
    npz, n, is_long = _gr_series_short(), 5, False
    gates = T.gap_risk_gates(_gr_cfg(), is_long)
    vec = T.vec_gap_risk_exit(npz, n, _gr_cfg(), is_long)
    st = T.gap_risk_new_state()
    close = np.where(npz['close_D'] > 0, npz['close_D'], npz['close'])
    scal = np.array([T.gap_risk_step(st, npz['open_D'][i], npz['close_D_prev'][i], npz['high_D'][i],
                                     npz['low_D'][i], close[i], gates, is_long) for i in range(n)])
    np.testing.assert_array_equal(vec, scal)
    assert vec.tolist() == [False, False, False, True, False]  # retrace bar1, COND_A fires bar3
    # COND_B variant: high makes new ext high above prev+open (close kept below open)
    npz2 = dict(_gr_series_short())
    npz2['close_D'] = np.array([0.0, 103.0, 104.0, 104.0, 103.5])
    npz2['close'] = np.array([50.0, 103.0, 104.0, 104.0, 103.5])
    cfg_b = _gr_cfg(GAP_RISK_EXIT_COND_A_ENABLED=False, GAP_RISK_EXIT_COND_B_ENABLED=True)
    vec_b = T.vec_gap_risk_exit(npz2, n, cfg_b, is_long)
    st2 = T.gap_risk_new_state()
    gates_b = T.gap_risk_gates(cfg_b, is_long)
    close2 = np.where(npz2['close_D'] > 0, npz2['close_D'], npz2['close'])
    scal_b = np.array([T.gap_risk_step(st2, npz2['open_D'][i], npz2['close_D_prev'][i], npz2['high_D'][i],
                                       npz2['low_D'][i], close2[i], gates_b, is_long) for i in range(n)])
    np.testing.assert_array_equal(vec_b, scal_b)
    assert vec_b.tolist() == [False, False, False, True, False]  # ext-high break fires bar3


def test_gap_risk_long_and_nogap_reset():
    npz = dict(open_D=np.array([95.0, 95.0, 95.0, 0.0, 95.0]),
               close_D_prev=np.array([100.0, 100.0, 100.0, 100.0, 100.0]),
               high_D=np.array([101.0, 99.0, 96.0, 0.0, 96.0]),
               low_D=np.array([94.0, 93.0, 92.0, 0.0, 92.0]),
               close_D=np.array([96.0, 94.0, 93.0, 0.0, 93.0]),
               close=np.array([96.0, 94.0, 93.0, 50.0, 93.0]))
    vec = T.vec_gap_risk_exit(npz, 5, _gr_cfg(), True)
    st = T.gap_risk_new_state()
    gates = T.gap_risk_gates(_gr_cfg(), True)
    close = np.where(npz['close_D'] > 0, npz['close_D'], npz['close'])
    scal = np.array([T.gap_risk_step(st, npz['open_D'][i], npz['close_D_prev'][i], npz['high_D'][i],
                                     npz['low_D'][i], close[i], gates, True) for i in range(5)])
    np.testing.assert_array_equal(vec, scal)
    assert vec[0] == True   # retraced (high>=prev) + COND_A (close<open) same bar
    assert vec[3] == False  # o=0: state reset, no fire
    # no-retrace => never fires
    npz3 = dict(open_D=np.array([105.0, 105.0]), close_D_prev=np.array([100.0, 100.0]),
                high_D=np.array([106.0, 106.0]), low_D=np.array([101.0, 101.0]),
                close_D=np.array([106.0, 106.0]), close=np.array([106.0, 106.0]))
    assert T.vec_gap_risk_exit(npz3, 2, _gr_cfg(), False).tolist() == [False, False]


# ── 48-51. gap risk reentry config-only ─────────────────────────────────────────
def test_gap_risk_reentry_defaults():
    d = T.gap_risk_reentry_cfg(SimpleNamespace())
    assert d == {'enabled': True, 'max_days': 5, 'on_fill': True, 'require_trend': False}
    d2 = T.gap_risk_reentry_cfg(SimpleNamespace(GAP_RISK_REENTRY_ENABLED=False, GAP_RISK_REENTRY_MAX_DAYS=3))
    assert d2['enabled'] is False and d2['max_days'] == 3


# ── 52. guaranteed dead read ───────────────────────────────────────────────────
def test_guaranteed_dead_read():
    assert T.guaranteed_hedge_required_value(SimpleNamespace()) is True
    assert T.guaranteed_hedge_required_value(SimpleNamespace(GUARANTEED_REENTRY_REQUIRE_HEDGE_OPEN=False)) is False
    assert T.guaranteed_hedge_affects_vector() is False


# ── 53. rally bypass ───────────────────────────────────────────────────────────
def test_rally_gate_and_fire():
    assert T.rally_bypass_gate(_cfg(), True, True, True, 3) is True
    assert T.rally_bypass_gate(_cfg(), True, True, True, 0) is False      # cd>0 strict
    assert T.rally_bypass_gate(_cfg(), False, True, True, 3) is False
    assert T.rally_bypass_gate(_cfg(), True, False, True, 3) is False
    assert T.rally_bypass_gate(_cfg(), True, True, False, 3) is False
    assert T.rally_bypass_gate(_cfg(HARDCODED_RALLY_REENTRY_ENABLED=False), True, True, True, 3) is False
    assert T.rally_bypass_gate(_cfg(HARDCODED_RALLY_REENTRY_BYPASS_COOLDOWN=False), True, True, True, 3) is False
    # fires: strict cross + optional WT
    assert T.rally_fires(101.0, 100.0, 5.0, 4.0, False, True) is True
    assert T.rally_fires(100.0, 100.0, 5.0, 4.0, False, True) is False   # strict >
    assert T.rally_fires(99.0, 100.0, 4.0, 5.0, False, False) is True
    assert T.rally_fires(101.0, 100.0, 4.0, 5.0, True, True) is False    # WT required, falling
    assert T.rally_fires(101.0, 100.0, 6.0, 5.0, True, True) is True
    assert T.rally_fires(101.0, 0.0, 6.0, 5.0, True, True) is False      # exit_px<=0 guard
    assert T.rally_fires(101.0, 100.0, 6.0, 5.0, True, True, hrf_ok=False) is False
