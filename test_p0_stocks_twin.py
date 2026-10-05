"""test_p0_stocks_twin.py — parity tests for vec_decisions/twin_p0_stocks.py.

Proves live-twin (scalar) ⟺ vector agreement per switch, mirroring cited
vector formulas exactly. Standalone: no engine import; cfg is a namespace/dict.
"""
import numpy as np

import vec_decisions.twin_p0_stocks as T


def _cfg(**kw):
    from types import SimpleNamespace
    return SimpleNamespace(**kw)


# ── DD_BOUNCE (v12:612-616) ──
def test_dd_bounce_inert_when_off():
    assert T.dd_bounce_fire(99, True, False) is True
    assert T.dd_bounce_fire(0, False, False) is True
    assert bool(np.all(T.dd_bounce_fire_vec([0, 50, 100], True, False)))


def test_dd_bounce_formula_and_agreement():
    k = np.array([39.9, 40.0, 60.0, 60.1, 50.0])
    for is_long in (True, False):
        vec = T.dd_bounce_fire_vec(k, is_long, True)
        for idx, kv in enumerate(k):
            assert T.dd_bounce_fire(float(kv), is_long, True) == bool(vec[idx])
    assert T.dd_bounce_fire(39.9, True, True) is True
    assert T.dd_bounce_fire(40.0, True, True) is False  # strict <
    assert T.dd_bounce_fire(60.1, False, True) is True
    assert T.dd_bounce_fire(60.0, False, True) is False  # strict >


# ── FG thresholds (fv2:10903-10908) ──
def test_fg_inert_when_equal_default():
    assert T.fg_fear_fire(0, 25, 25, True) is True
    assert T.fg_greed_fire(100, 75, 75, False) is True
    assert bool(np.all(T.fg_fear_fire_vec([0, 100], 25, 25, True)))
    assert bool(np.all(T.fg_greed_fire_vec([0, 100], 75, 75, False)))


def test_fg_formula_and_agreement():
    rsi = np.array([29.9, 30.0, 30.1, 69.9, 70.0, 70.1])
    for is_long in (True, False):
        vf = T.fg_fear_fire_vec(rsi, 31, 25, is_long)
        vg = T.fg_greed_fire_vec(rsi, 69, 75, is_long)
        for idx, v in enumerate(rsi):
            assert T.fg_fear_fire(float(v), 31, 25, is_long) == bool(vf[idx])
            assert T.fg_greed_fire(float(v), 69, 75, is_long) == bool(vg[idx])
    # fear thr=31: long rsi>thr / short rsi<thr
    assert T.fg_fear_fire(31.1, 31, 25, True) is True
    assert T.fg_fear_fire(31.0, 31, 25, True) is False
    assert T.fg_fear_fire(30.9, 31, 25, False) is True
    # greed thr=69: long rsi<thr / short rsi>thr
    assert T.fg_greed_fire(68.9, 69, 75, True) is True
    assert T.fg_greed_fire(69.1, 69, 75, False) is True
    assert T.fg_greed_fire(69.0, 69, 75, False) is False


# ── FROZEN_STOP_FILTER_TF ──
def test_frozen_eff_tf():
    assert T.frozen_eff_tf("15m", "1h") == "15m"
    assert T.frozen_eff_tf("OFF", "1h") == "1h"
    assert T.frozen_eff_tf("", "4h") == "4h"
    assert T.frozen_eff_tf("off", "D") == "D"


# ── GR_TIGHT_STOP (ez:50528-50565) ──
def test_gr_tight_stop():
    kw = dict(enabled=True, stop_pct=0.5, min_age_s=60.0, max_age_s=1800.0, is_gr_position=True)
    assert T.gr_tight_stop_fire(gain_pct=-0.5, age_s=60.0, **kw) is True  # boundary inclusive
    assert T.gr_tight_stop_fire(gain_pct=-0.49, age_s=60.0, **kw) is False
    assert T.gr_tight_stop_fire(gain_pct=-5.0, age_s=59.9, **kw) is False
    assert T.gr_tight_stop_fire(gain_pct=-5.0, age_s=1800.1, **kw) is False
    assert T.gr_tight_stop_fire(gain_pct=-5.0, age_s=100.0, **{**kw, "is_gr_position": False}) is False
    assert T.gr_tight_stop_fire(gain_pct=-5.0, age_s=100.0, **{**kw, "enabled": False}) is False
    v = T.gr_tight_stop_fire_vec([-0.5, -0.49], [60.0, 100.0], **{k: v for k, v in kw.items() if k != "is_gr_position"})
    assert [bool(x) for x in v] == [True, False]
    for g, a, want in [(-0.5, 60.0, True), (-0.49, 100.0, False)]:
        assert T.gr_tight_stop_fire(gain_pct=g, age_s=a, **kw) == want


# ── RALLY_BYPASS (v12:12960-12970) ──
def test_rally_bypass():
    base = dict(enabled=True, bypass=True, pos_none=True, closed_before=True, cd=5,
                px=101.0, last_exit=100.0, wt1=1.0, wt1_prev=0.0, require_wt=False,
                is_long=True, hrf_ok=True)
    assert T.rally_bypass_fire(**base) is True
    assert T.rally_bypass_fire(**{**base, "bypass": False}) is False
    assert T.rally_bypass_fire(**{**base, "cd": 0}) is False
    assert T.rally_bypass_fire(**{**base, "px": 99.0}) is False  # no cross
    assert T.rally_bypass_fire(**{**base, "hrf_ok": False}) is False
    # require_wt=True: falling WT blocks long
    assert T.rally_bypass_fire(**{**base, "require_wt": True, "wt1": -1.0}) is False
    assert T.rally_bypass_fire(**{**base, "require_wt": True}) is True
    # short mirror
    sh = dict(base, is_long=False, px=99.0)
    assert T.rally_bypass_fire(**sh) is True
    assert T.rally_bypass_fire(**{**sh, "px": 101.0}) is False
    assert T.rally_bypass_fire(**{**sh, "last_exit": 0.0}) is False


# ── MIN_HOLD_BARS (v12:12507) ──
def test_min_hold():
    assert T.min_hold_bars(_cfg(MIN_HOLD_BARS=3, MIN_HOLD_BARS_BEFORE_EXIT=10)) == 10
    assert T.min_hold_bars(_cfg(MIN_HOLD_BARS=12, MIN_HOLD_BARS_BEFORE_EXIT=10)) == 12
    assert T.min_hold_bars(_cfg(MIN_HOLD_BARS=3)) == 3  # getattr default 0
    assert T.min_hold_bars({"MIN_HOLD_BARS": 3, "MIN_HOLD_BARS_BEFORE_EXIT": 0}) == 3
    cfg = _cfg(MIN_HOLD_BARS=3, MIN_HOLD_BARS_BEFORE_EXIT=10)
    assert T.min_hold_ok(10.0, cfg) is True  # held >= min
    assert T.min_hold_ok(9.99, cfg) is False


# ── MTF_DC_USE_DC4 (v12:12668) ──
def test_mtf_dc_band_key():
    assert T.mtf_dc_band_key("1h", True, False) == "dc_high_1h"
    assert T.mtf_dc_band_key("1h", True, True) == "dc_high4_1h"
    assert T.mtf_dc_band_key("1h", False, False) == "dc_low_1h"
    assert T.mtf_dc_band_key("4h", False, True) == "dc_low4_4h"


# ── STOCH_CROSS (v12:9248-9256) ──
def test_stoch_cross_formula_and_agreement():
    # long: (k_prev<=d)&(k>d); short: (k_prev>=d)&(k<d); k_prev vs CURRENT d
    assert T.stoch_cross_fire(51, 50, 50, True) is True
    assert T.stoch_cross_fire(50, 50, 50, True) is False
    assert T.stoch_cross_fire(49, 50, 50, False) is True
    assert T.stoch_cross_fire(50, 50, 50, False) is False
    k = np.array([48.0, 52.0, 51.0, 49.0, 50.0])
    d = np.array([50.0, 50.0, 50.0, 50.0, 50.0])
    for is_long in (True, False):
        vec = T.stoch_cross_fire_vec(k, d, is_long)
        kp = np.roll(k, 1)
        kp[0] = k[0]
        for idx in range(len(k)):
            assert T.stoch_cross_fire(float(k[idx]), float(d[idx]), float(kp[idx]), is_long) == bool(vec[idx])
    # boundary: prev==d counts as crossed-from (<= / >=)
    assert T.stoch_cross_fire(50.1, 50, 50, True) is True
    assert T.stoch_cross_fire(49.9, 50, 50, False) is True


# ── DAYTRADE expansion + band (v12:8677-8679) ──
def test_daytrade():
    assert T.daytrade_expansion_ok(2.0, 1.9, True) is True
    assert T.daytrade_expansion_ok(1.9, 1.9, True) is False  # strict >
    assert T.daytrade_expansion_ok(0.0, 9.9, False) is True  # require off → ones
    assert T.daytrade_entry_fire(0.10, 2.0, 1.9, 0.15, True, True) is True
    assert T.daytrade_entry_fire(0.15, 2.0, 1.9, 0.15, True, True) is False  # strict <
    assert T.daytrade_entry_fire(0.10, 1.9, 1.9, 0.15, True, True) is False  # no expansion
    assert T.daytrade_entry_fire(0.90, 2.0, 1.9, 0.15, True, False) is True
    assert T.daytrade_entry_fire(0.85, 2.0, 1.9, 0.15, True, False) is False  # 1-thr strict >
    dc = np.array([0.10, 0.15, 0.90])
    w = np.array([2.0, 2.0, 2.1])
    for is_long in (True, False):
        vec = T.daytrade_entry_fire_vec(dc, w, is_long, 0.15, True)
        wp = np.roll(w, 1)
        wp[0] = w[0]
        for idx in range(3):
            assert T.daytrade_entry_fire(float(dc[idx]), float(w[idx]), float(wp[idx]), 0.15, True, is_long) == bool(vec[idx])


# ── TRADIER_STOCH band (v12:8800-8804) ──
def test_stoch_threshold_defaults():
    assert T.STOCH_VEC_DEFAULTS["TRADIER_STOCH_ENTRY_LONG_TRADIER"] == 30
    assert T.STOCH_VEC_DEFAULTS["TRADIER_STOCH_ENTRY_SHORT_TRADIER"] == 70
    assert T.STOCH_VEC_DEFAULTS["TRADIER_STOCH_EXTREME_LONG_TRADIER"] == 15
    assert T.STOCH_VEC_DEFAULTS["TRADIER_STOCH_EXTREME_SHORT_TRADIER"] == 85
    got = T.stoch_threshold(lambda n, d: d, "TRADIER_STOCH_ENTRY_SHORT_TRADIER")
    assert got == 70
    got2 = T.stoch_threshold(lambda n, d: 99, "TRADIER_STOCH_ENTRY_LONG_TRADIER")
    assert got2 == 99


def test_stoch_band_formula_and_agreement():
    assert T.stoch_band_fire(20, 30, 15, True) is True
    assert T.stoch_band_fire(30, 30, 15, True) is False  # strict <
    assert T.stoch_band_fire(15, 30, 15, True) is False  # strict >
    assert T.stoch_band_fire(80, 70, 85, False) is True
    assert T.stoch_band_fire(70, 70, 85, False) is False
    assert T.stoch_band_fire(85, 70, 85, False) is False
    k = np.array([10.0, 20.0, 30.0, 75.0, 90.0])
    for is_long, e, x in ((True, 30, 15), (False, 70, 85)):
        vec = T.stoch_band_fire_vec(k, e, x, is_long)
        for idx, kv in enumerate(k):
            assert T.stoch_band_fire(float(kv), e, x, is_long) == bool(vec[idx])


# ── WT_SIMPLE (v12:9698-9701, 10219-10222) ──
def test_wt_simple():
    assert T.wt_simple_entry_fire(1.0, 0.0, True) is True
    assert T.wt_simple_entry_fire(0.0, 0.0, True) is False  # strict >
    assert T.wt_simple_entry_fire(0.0, 1.0, False) is True
    assert T.wt_simple_exit_fire(0.0, 1.0, True) is True  # opposite WT
    assert T.wt_simple_exit_fire(1.0, 0.0, False) is True
    assert T.wt_simple_exit_fire(1.0, 1.0, True) is False
    # sub-gates only when explicit
    assert T.wt_simple_entry_fire(1.0, 0.0, True, hl_hh_ok=False, sub_explicit=False) is True
    assert T.wt_simple_entry_fire(1.0, 0.0, True, hl_hh_ok=False, sub_explicit=True) is False
    w1 = np.array([1.0, 0.0, 0.5])
    w2 = np.array([0.0, 1.0, 0.5])
    for is_long in (True, False):
        ve = T.wt_simple_entry_fire_vec(w1, w2, is_long)
        vx = T.wt_simple_exit_fire_vec(w1, w2, is_long)
        for idx in range(3):
            assert T.wt_simple_entry_fire(float(w1[idx]), float(w2[idx]), is_long) == bool(ve[idx])
            assert T.wt_simple_exit_fire(float(w1[idx]), float(w2[idx]), is_long) == bool(vx[idx])


# ── FORCE_MIN_ONE_TRADE (v12:9870-9873) ──
def test_force_min_one_trade():
    assert T.force_first_index(100, False, True, 7) == 7
    assert T.force_first_index(100, True, True, 7) is None  # entries exist → no-op
    assert T.force_first_index(100, False, False, 7) is None  # disabled → no-op
    assert T.force_first_index(100, False, True, 100) is None  # out of range


# ── CRYPTO_SPIKE_FADE (v12:551-556) ──
def test_spike_fade():
    assert T.spike_fade_exit_fire(0.6, 0.5, 0.0) is True
    assert T.spike_fade_exit_fire(0.4, 0.5, 0.0) is False
    assert T.spike_fade_exit_fire(99.0, 0.0, 0.0) is False  # thr==dflt → inert
    assert T.spike_fade_exit_fire(0.6, -1.0, 0.0) is True  # thr<=0 → 0.5 fallback
    assert T.spike_fade_exit_fire(0.4, -1.0, 0.0) is False
    v = T.spike_fade_exit_fire_vec(np.array([0.6, 0.4]), 0.5, 0.0)
    assert [bool(x) for x in v] == [True, False]
    v0 = T.spike_fade_exit_fire_vec(np.array([99.0]), 0.0, 0.0)
    assert [bool(x) for x in v0] == [False]
