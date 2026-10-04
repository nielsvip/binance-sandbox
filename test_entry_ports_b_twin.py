"""Entry-ports-B live-twin parity (2026-10-04 wiring mandate).

Proves vec_decisions.twin_entry_ports_b reproduces v12_quick_engine entry
semantics exactly: WT_15M_BOUNCE family, WT/STOCH/SATOSHIT/MFI/SMA200/EMA20/
BB_PCTB/VWAP/BAND_ARROW/WT_DC_DETAILED entries, BB_BOUNCE_ENTRY_TF (both
venues), WT_CROSSUNDER_FINAL exit (both venues). Inert-default + boundaries +
formula agreement vs the vec expressions.
"""
import random

import vec_decisions.twin_entry_ports_b as T


def _get(d):
    return lambda k, default: d.get(k, default)


def _fires(fn, d, is_long, ind, *a):
    fire, _reason = fn(_get(d), is_long, ind, *a)
    return fire


def test_masters_off_is_inert():
    ind = {"wt1_15m": 10.0, "wt2_15m": 0.0, "wt1_15m_prev": -5.0, "wt2_15m_prev": 5.0, "bb_pct_b_15m": 0.5,
           "wt_cross_rising_1h": True, "wt_cross_rising_4h": True, "k_1h": 10.0, "d_1h": 20.0,
           "wt1_1h": -60.0, "wt2_1h": -70.0, "bb_pct_b_1h": -0.5, "mfi_1h": 10.0,
           "rsi_15m": 10.0, "stoch_k_15m": 10.0, "mfi_15m": 10.0, "ha_15m": "red",
           "mfi_D": 50.0, "relative_volume_1h": 2.0, "current_price": 100.0,
           "sma_200_1h": 110.0, "ema_20_1h": 101.0, "ema_20_1h_prev": 100.0,
           "vwap_D": 99.9, "lrL_slope_D": 2.0, "lrL_pct_b_D": 0.2,
           "wt1_3m": -5.0, "wt2_3m": 5.0, "wt1_3m_prev": 6.0, "k_3m": 80.0,
           "bb_pct_b_1h_prev": 0.1, "bb_lower_1h": 90.0, "bb_upper_1h": 110.0}
    for fn, a in ((T.wt_15m_bounce, ()), (T.stoch_entry, ()), (T.wt_entry, ()),
                  (T.bb_pctb_entry, ()), (T.satoshit_entry, ()), (T.band_arrow_entry, ()),
                  (T.wt_dc_detailed_entry, ()), (T.bb_bounce_entry, ())):
        assert fn(_get({}), True, ind, *a) == (False, "")
        assert fn(_get({}), False, ind, *a) == (False, "")
    assert T.wt_15m_bounce(_get({"WT_15M_BOUNCE_OPEN_ENABLED": False}), True, ind) == (False, "")
    assert T.bb_bounce_entry(_get({"BB_BOUNCE_ENTRY_TF": "OFF"}), True, ind) == (False, "")
    assert T.bb_bounce_entry(_get({"BB_BOUNCE_ENTRY_TF": "3m"}), True, ind) == (False, "")
    assert T.bb_bounce_entry(_get({"BB_BOUNCE_ENTRY_TF": ""}), False, ind) == (False, "")
    assert T.stoch_entry(_get({}), True, None) == (False, "")
    assert T.wt_entry(_get({}), False, None) == (False, "")
    assert T.mfi_gate(_get({"MFI_ENTRY_ENABLED": False}), True, {"mfi_1h": 99.0}) is True
    assert T.mfi_gate(_get({"MFI_ENTRY_ENABLED": False}), False, {"mfi_1h": 1.0}) is True


def test_default_true_twins_no_fire_without_data():
    for fn, a in ((T.ema20_slope_entry, ()), (T.sma200_dist_entry, ()), (T.vwap_bounce_entry, ()),
                  (T.wt_crossunder_final_exit, ("3m",)), (T.wt_crossunder_final_exit, ("5m",))):
        assert fn(_get({}), True, {}, *a) == (False, "")
        assert fn(_get({}), False, {}, *a) == (False, "")
        assert fn(_get({}), True, None, *a) == (False, "")


def test_fail_open_on_garbage():
    bad = {"k_1h": "zzz", "d_1h": object(), "wt1_1h": "NaN-ok", "current_price": "nope"}
    boom = lambda k, d: 1 / 0
    for fn, a in ((T.wt_15m_bounce, ()), (T.stoch_entry, ()), (T.wt_entry, ()),
                  (T.ema20_slope_entry, ()), (T.sma200_dist_entry, ()), (T.bb_pctb_entry, ()),
                  (T.vwap_bounce_entry, ()), (T.satoshit_entry, ()), (T.band_arrow_entry, ()),
                  (T.wt_dc_detailed_entry, ()), (T.bb_bounce_entry, ())):
        assert fn(_get({}), True, bad, *a) == (False, "")
        assert fn(boom, True, bad, *a) == (False, "")
    assert T.wt_crossunder_final_exit(boom, True, bad, "3m") == (False, "")
    assert T.mfi_gate(boom, True, bad) is True


def _bounce_base():
    return {"WT_15M_BOUNCE_OPEN_ENABLED": True}


def test_bounce_cross_and_still_trigger():
    d = _bounce_base()
    fire_ind = {"wt1_15m": 5.0, "wt2_15m": 0.0, "wt1_15m_prev": -1.0, "wt2_15m_prev": 1.0,
                "bb_pct_b_15m": 0.5, "wt_cross_rising_1h": True, "wt_cross_rising_4h": False}
    assert _fires(T.wt_15m_bounce, d, True, fire_ind) is True
    assert _fires(T.wt_15m_bounce, d, False, fire_ind) is False
    no_cross = dict(fire_ind, wt1_15m_prev=6.0, wt2_15m_prev=0.0)
    assert _fires(T.wt_15m_bounce, d, True, no_cross) is False
    d2 = dict(d, WT_15M_BOUNCE_LOW_1H_GT_PREV=True)
    still_ind = dict(no_cross, dc_low_1h=101.0, dc_low_1h_prev=100.0)
    assert _fires(T.wt_15m_bounce, d2, True, still_ind) is True
    flat_ind = dict(still_ind, dc_low_1h=100.0, dc_low_1h_prev=100.0)
    assert _fires(T.wt_15m_bounce, d2, True, flat_ind) is False


def test_bounce_bb_htf_boundaries():
    d = _bounce_base()
    base = {"wt1_15m": 5.0, "wt2_15m": 0.0, "wt1_15m_prev": -1.0, "wt2_15m_prev": 1.0,
            "wt_cross_rising_1h": True, "wt_cross_rising_4h": False}
    assert _fires(T.wt_15m_bounce, d, True, dict(base, bb_pct_b_15m=0.05)) is True
    assert _fires(T.wt_15m_bounce, d, True, dict(base, bb_pct_b_15m=0.95)) is True
    assert _fires(T.wt_15m_bounce, d, True, dict(base, bb_pct_b_15m=0.049)) is False
    assert _fires(T.wt_15m_bounce, d, True, dict(base, bb_pct_b_15m=0.951)) is False
    assert _fires(T.wt_15m_bounce, dict(d, WT_15M_BOUNCE_BB_MIN=0.5), True, dict(base, bb_pct_b_15m=0.5)) is True
    htf_none = dict(base, bb_pct_b_15m=0.5, wt_cross_rising_1h=False, wt_cross_rising_4h=False)
    assert _fires(T.wt_15m_bounce, d, True, htf_none) is False
    d_both = dict(d, WT_15M_BOUNCE_REQUIRE_BOTH_HTF=True)
    one_htf = dict(base, bb_pct_b_15m=0.5, wt_cross_rising_1h=True, wt_cross_rising_4h=False)
    assert _fires(T.wt_15m_bounce, d_both, True, one_htf) is False
    both_htf = dict(one_htf, wt_cross_rising_4h=True)
    assert _fires(T.wt_15m_bounce, d_both, True, both_htf) is True
    short_ind = {"wt1_15m": -5.0, "wt2_15m": 0.0, "wt1_15m_prev": 1.0, "wt2_15m_prev": -1.0,
                 "bb_pct_b_15m": 0.5, "wt_cross_rising_1h": False, "wt_cross_rising_4h": False}
    assert _fires(T.wt_15m_bounce, d, False, short_ind) is True


def test_bounce_hl_hh_modes():
    d = _bounce_base()
    base = {"wt1_15m": 5.0, "wt2_15m": 0.0, "wt1_15m_prev": 6.0, "wt2_15m_prev": 0.0,
            "bb_pct_b_15m": 0.5, "wt_cross_rising_1h": True, "wt_cross_rising_4h": True,
            "dc_low_1h": 101.0, "dc_low_1h_prev": 100.0, "dc_high_1h": 100.0, "dc_high_1h_prev": 100.0}
    d_hl = dict(d, WT_15M_BOUNCE_LOW_1H_GT_PREV=True)
    assert _fires(T.wt_15m_bounce, d_hl, True, base) is True
    d_hh = dict(d, WT_15M_BOUNCE_HIGH_1H_GT_PREV=True)
    assert _fires(T.wt_15m_bounce, d_hh, True, base) is False
    d_and = dict(d, WT_15M_BOUNCE_LOW_1H_GT_PREV=True, WT_15M_BOUNCE_HIGH_1H_GT_PREV=True)
    assert _fires(T.wt_15m_bounce, d_and, True, base) is False
    d_or = dict(d_and, WT_15M_BOUNCE_FILTER_MODE="OR")
    assert _fires(T.wt_15m_bounce, d_or, True, base) is True
    d_alias = dict(d, WT_15M_BOUNCE_FILTER_HL_ENABLED=True)
    assert _fires(T.wt_15m_bounce, d_alias, True, base) is True


def test_bounce_volume_modes():
    d = _bounce_base()
    base = {"wt1_15m": 5.0, "wt2_15m": 0.0, "wt1_15m_prev": -1.0, "wt2_15m_prev": 1.0,
            "bb_pct_b_15m": 0.5, "wt_cross_rising_1h": True, "wt_cross_rising_4h": True}
    d_rel = dict(d, WT_15M_BOUNCE_REL_VOL_GT_1=True)
    assert _fires(T.wt_15m_bounce, d_rel, True, dict(base, relative_volume_15m=1.01)) is True
    assert _fires(T.wt_15m_bounce, d_rel, True, dict(base, relative_volume_15m=1.0)) is False
    assert _fires(T.wt_15m_bounce, d_rel, True, dict(base, relative_volume_1h=1.5)) is True
    assert _fires(T.wt_15m_bounce, d_rel, True, dict(base)) is False
    d_ema = dict(d_rel, WT_15M_BOUNCE_VOLUME_MODE="ema")
    assert _fires(T.wt_15m_bounce, d_ema, True, dict(base, volume_15m=11.0, volume_sma_15m=10.0)) is True
    assert _fires(T.wt_15m_bounce, d_ema, True, dict(base, volume_15m=10.0, volume_sma_15m=10.0)) is False
    assert _fires(T.wt_15m_bounce, d_ema, True, dict(base, volume_1h=11.0, volume_sma_1h=10.0)) is True
    d_raw = dict(d_rel, WT_15M_BOUNCE_VOLUME_MODE="raw")
    assert _fires(T.wt_15m_bounce, d_raw, True, dict(base, volume_15m=10.01, volume_sma_15m=10.0)) is True
    assert _fires(T.wt_15m_bounce, d_raw, True, dict(base, volume_15m=10.0, volume_sma_15m=10.0)) is False


def test_bounce_htf_level_proxy():
    d = _bounce_base()
    base = {"wt1_15m": 5.0, "wt2_15m": 0.0, "wt1_15m_prev": -1.0, "wt2_15m_prev": 1.0, "bb_pct_b_15m": 0.5}
    agree = dict(base, wt1_1h=3.0, wt2_1h=1.0, wt1_4h=-2.0, wt2_4h=-1.0)
    assert _fires(T.wt_15m_bounce, d, True, agree) is True
    disagree = dict(base, wt1_1h=-3.0, wt2_1h=1.0, wt1_4h=-2.0, wt2_4h=-1.0)
    assert _fires(T.wt_15m_bounce, d, True, disagree) is False


def test_simple_entries_boundaries():
    assert _fires(T.stoch_entry, {"STOCH_ENTRY_ENABLED": True}, True, {"k_1h": 29.9, "d_1h": 29.0}) is True
    assert _fires(T.stoch_entry, {"STOCH_ENTRY_ENABLED": True}, True, {"k_1h": 30.0, "d_1h": 29.0}) is False
    assert _fires(T.stoch_entry, {"STOCH_ENTRY_ENABLED": True}, True, {"k_1h": 10.0, "d_1h": 10.0}) is False
    assert _fires(T.stoch_entry, {"STOCH_ENTRY_ENABLED": True}, False, {"k_1h": 70.1, "d_1h": 71.0}) is True
    assert _fires(T.stoch_entry, {"STOCH_ENTRY_ENABLED": True}, False, {"k_1h": 70.0, "d_1h": 71.0}) is False
    assert _fires(T.stoch_entry, {"STOCH_ENTRY_ENABLED": True}, True, {"stoch_k_1h": 10.0, "stoch_d_1h": 5.0}) is True
    assert _fires(T.wt_entry, {"WT_ENTRY_ENABLED": True}, True, {"wt1_1h": -50.1, "wt2_1h": -60.0}) is True
    assert _fires(T.wt_entry, {"WT_ENTRY_ENABLED": True}, True, {"wt1_1h": -50.0, "wt2_1h": -60.0}) is False
    assert _fires(T.wt_entry, {"WT_ENTRY_ENABLED": True}, False, {"wt1_1h": 50.1, "wt2_1h": 60.0}) is True
    assert _fires(T.wt_entry, {"WT_ENTRY_ENABLED": True}, False, {"wt1_1h": 50.0, "wt2_1h": 60.0}) is False
    assert _fires(T.wt_entry, {"WT_ENTRY_ENABLED": True}, True, {"wt1_15m": -60.0, "wt2_15m": -70.0}) is True
    assert _fires(T.bb_pctb_entry, {"BB_PCTB_ENTRY_ENABLED": True}, True, {"bb_pct_b_1h": -0.21}) is True
    assert _fires(T.bb_pctb_entry, {"BB_PCTB_ENTRY_ENABLED": True}, True, {"bb_pct_b_1h": -0.2}) is False
    assert _fires(T.bb_pctb_entry, {"BB_PCTB_ENTRY_ENABLED": True}, False, {"bb_pct_b_1h": 1.01}) is True
    assert _fires(T.bb_pctb_entry, {"BB_PCTB_ENTRY_ENABLED": True}, False, {"bb_pct_b_1h": 1.0}) is False
    assert T.mfi_gate(_get({"MFI_ENTRY_ENABLED": True}), True, {"mfi_1h": 59.9}) is True
    assert T.mfi_gate(_get({"MFI_ENTRY_ENABLED": True}), True, {"mfi_1h": 60.0}) is False
    assert T.mfi_gate(_get({"MFI_ENTRY_ENABLED": True}), False, {"mfi_1h": 40.1}) is True
    assert T.mfi_gate(_get({"MFI_ENTRY_ENABLED": True}), False, {"mfi_1h": 40.0}) is False


def test_dist_slope_vwap_boundaries():
    assert _fires(T.sma200_dist_entry, {"SMA200_DIST_ENTRY_ENABLED": True}, True, {"current_price": 96.9, "sma_200_1h": 100.0}) is True
    assert _fires(T.sma200_dist_entry, {"SMA200_DIST_ENTRY_ENABLED": True}, True, {"current_price": 97.0, "sma_200_1h": 100.0}) is False
    assert _fires(T.sma200_dist_entry, {"SMA200_DIST_ENTRY_ENABLED": True}, False, {"current_price": 103.1, "sma_200_1h": 100.0}) is True
    assert _fires(T.sma200_dist_entry, {"SMA200_DIST_ENTRY_ENABLED": True}, False, {"current_price": 103.0, "sma_200_1h": 100.0}) is False
    assert _fires(T.ema20_slope_entry, {"EMA20_SLOPE_ENTRY_ENABLED": True}, True, {"ema_20_1h": 100.06, "ema_20_1h_prev": 100.0}) is True
    assert _fires(T.ema20_slope_entry, {"EMA20_SLOPE_ENTRY_ENABLED": True}, True, {"ema_20_1h": 100.05, "ema_20_1h_prev": 100.0}) is False
    assert _fires(T.ema20_slope_entry, {"EMA20_SLOPE_ENTRY_ENABLED": True}, False, {"ema_20_1h": 99.94, "ema_20_1h_prev": 100.0}) is True
    assert _fires(T.vwap_bounce_entry, {"VWAP_BOUNCE_ENTRY_ENABLED": True}, True, {"current_price": 100.2, "vwap_D": 100.0}) is True
    assert _fires(T.vwap_bounce_entry, {"VWAP_BOUNCE_ENTRY_ENABLED": True}, True, {"current_price": 100.31, "vwap_D": 100.0}) is False
    assert _fires(T.vwap_bounce_entry, {"VWAP_BOUNCE_ENTRY_ENABLED": True}, True, {"current_price": 99.9, "vwap_D": 100.0}) is False
    assert _fires(T.vwap_bounce_entry, {"VWAP_BOUNCE_ENTRY_ENABLED": True}, False, {"current_price": 99.8, "vwap_D": 100.0}) is True
    assert _fires(T.vwap_bounce_entry, {"VWAP_BOUNCE_ENTRY_ENABLED": True}, True, {"current_price": 100.2, "vwap": 100.0}) is True


def test_satoshit_votes_and_htf():
    d = {"SATOSHIT_ENTRY_ENABLED": True}
    full = {"rsi_15m": 10.0, "stoch_k_15m": 10.0, "mfi_15m": 10.0, "bb_pct_b_1h": 0.1, "ha_15m": "red",
            "mfi_D": 50.0, "relative_volume_1h": 2.0}
    fire, reason = T.satoshit_entry(_get(d), True, full)
    assert fire and reason.startswith("SATOSHIT_LONG_v5of5")
    two = dict(full, rsi_15m=90.0, stoch_k_15m=90.0, mfi_15m=90.0)
    assert _fires(T.satoshit_entry, d, True, two) is False
    no_htf = dict(full, mfi_D=10.0)
    assert _fires(T.satoshit_entry, d, True, no_htf) is False
    no_rvol = dict(full, relative_volume_1h=0.1)
    assert _fires(T.satoshit_entry, d, True, no_rvol) is False
    sfull = {"rsi_15m": 90.0, "stoch_k_15m": 90.0, "mfi_15m": 90.0, "bb_pct_b_1h": 0.9, "ha_15m": "green",
             "mfi_D": 50.0, "relative_volume_1h": 2.0}
    fire, reason = T.satoshit_entry(_get(d), False, sfull)
    assert fire and reason.startswith("SATOSHIT_SHORT_v5of5")
    assert _fires(T.satoshit_entry, dict(d, SATOSHIT_MIN_VOTES=5), True, dict(full, rsi_15m=90.0)) is False


def test_band_arrow():
    d = {"BAND_ARROW_ENABLED": True}
    fire, reason = T.band_arrow_entry(_get(d), True, {"lrL_slope_D": 2.0, "lrL_pct_b_D": 0.2})
    assert fire and reason == "BAND_ARROW_D_sl2.00_pb0.20"
    assert _fires(T.band_arrow_entry, d, True, {"lrL_slope_D": 2.0, "lrL_pct_b_D": 0.5}) is False
    assert _fires(T.band_arrow_entry, d, True, {"lrL_slope_D": 0.0, "lrL_pct_b_D": 0.2}) is False
    assert _fires(T.band_arrow_entry, d, False, {"lrL_slope_D": -2.0, "lrL_pct_b_D": 0.8}) is True
    assert _fires(T.band_arrow_entry, d, False, {"lrL_slope_D": -2.0, "lrL_pct_b_D": 0.5}) is False
    assert _fires(T.band_arrow_entry, d, True, {"lrL_slope_D": 2.0}) is False
    assert _fires(T.band_arrow_entry, dict(d, BAND_ARROW_SLOPE_DEADBAND=3.0), True, {"lrL_slope_D": 2.0, "lrL_pct_b_D": 0.2}) is False
    assert _fires(T.band_arrow_entry, dict(d, BAND_ARROW_ENTRY_TFS="1h"), True, {"lrL_slope_1h": 1.0, "lrL_pct_b_1h": 0.1}) is True
    assert _fires(T.band_arrow_entry, dict(d, BAND_ARROW_ENTRY_TFS="1h"), True, {"lrL_slope_D": 1.0, "lrL_pct_b_D": 0.1}) is False


def test_crossunder_exit_both_bases():
    d = {"WT_CROSSUNDER_FINAL_ENABLED": True}
    l3 = {"wt1_3m": -1.0, "wt2_3m": 0.0, "wt1_3m_prev": 1.0, "k_3m": 70.0}
    assert _fires(T.wt_crossunder_final_exit, d, True, l3, "3m") is True
    assert _fires(T.wt_crossunder_final_exit, d, True, dict(l3, k_3m=69.9), "3m") is False
    assert _fires(T.wt_crossunder_final_exit, d, True, dict(l3, wt1_3m_prev=-2.0), "3m") is False
    assert _fires(T.wt_crossunder_final_exit, d, False, l3, "3m") is False
    s3 = {"wt1_3m": 1.0, "wt2_3m": 0.0, "wt1_3m_prev": -1.0, "k_3m": 30.0}
    assert _fires(T.wt_crossunder_final_exit, d, False, s3, "3m") is True
    assert _fires(T.wt_crossunder_final_exit, d, False, dict(s3, k_3m=30.1), "3m") is False
    l5 = {"wt1_5m": -1.0, "wt2_5m": 0.0, "wt1_5m_prev": 1.0, "k_5m": 75.0}
    assert _fires(T.wt_crossunder_final_exit, d, True, l5, "5m") is True
    assert _fires(T.wt_crossunder_final_exit, d, True, dict(l5, k_5m=69.9), "5m") is False
    assert _fires(T.wt_crossunder_final_exit, d, False, {"wt1_5m": 1.0, "wt2_5m": 0.0, "wt1_5m_prev": -1.0, "stoch_k_5m": 25.0}, "5m") is True
    assert _fires(T.wt_crossunder_final_exit, {"WT_CROSSUNDER_FINAL_ENABLED": False}, True, l3, "3m") is False


def test_bb_bounce_reclaim():
    d = {"BB_BOUNCE_ENTRY_TF": "1h"}
    assert _fires(T.bb_bounce_entry, d, True, {"bb_pct_b_1h": 0.26, "bb_pct_b_1h_prev": 0.19, "bb_lower_1h": 90.0}) is True
    assert _fires(T.bb_bounce_entry, d, True, {"bb_pct_b_1h": 0.25, "bb_pct_b_1h_prev": 0.19, "bb_lower_1h": 90.0}) is False
    assert _fires(T.bb_bounce_entry, d, True, {"bb_pct_b_1h": 0.26, "bb_pct_b_1h_prev": 0.20, "bb_lower_1h": 90.0}) is False
    assert _fires(T.bb_bounce_entry, d, True, {"bb_pct_b_1h": 0.26, "bb_pct_b_1h_prev": 0.19, "bb_lower_1h": 0.0}) is False
    assert _fires(T.bb_bounce_entry, d, False, {"bb_pct_b_1h": 0.74, "bb_pct_b_1h_prev": 0.81, "bb_upper_1h": 110.0}) is True
    assert _fires(T.bb_bounce_entry, d, False, {"bb_pct_b_1h": 0.75, "bb_pct_b_1h_prev": 0.81, "bb_upper_1h": 110.0}) is False
    assert _fires(T.bb_bounce_entry, d, False, {"bb_pct_b_1h": 0.74, "bb_pct_b_1h_prev": 0.80, "bb_upper_1h": 110.0}) is False
    d15 = {"BB_BOUNCE_ENTRY_TF": "15m"}
    assert _fires(T.bb_bounce_entry, d15, True, {"bb_pct_b_15m": 0.3, "bb_pct_b_15m_prev": 0.1, "bb_lower_15m": 5.0}) is True
    assert _fires(T.bb_bounce_entry, d15, True, {"bb_pct_b_1h": 0.3, "bb_pct_b_1h_prev": 0.1, "bb_lower_1h": 5.0}) is False


def test_wt_dc_detailed_threshold_math():
    from wt_dc_entry_scorer import score_entry as se
    ind = {"wt1_15m": 50.0, "wt2_15m": 40.0, "wt1_1h": 60.0, "wt2_1h": 30.0, "wt1_4h": 55.0, "wt2_4h": 20.0,
           "dc_position_1h": 0.7, "stoch_k_5m": 60.0, "wt_cross_1h": "BULL", "mfi_1h": 60.0,
           "relative_volume_1h": 1.5, "bb_pct_b_1h": 0.6, "close": 100.0}
    for is_long in (True, False):
        for tf_entry, adj in (("1h", 0), ("15m", -10), ("4h", 10), ("D", 15)):
            score, _r = se(dict(ind), is_long, 100.0, detailed=True)
            thr = 43.0 + adj
            thr = max(20.0, min(85.0, thr)) if adj else thr
            d = {"WT_DC_DETAILED_SCORER_ENABLED": True, "WT_DC_TF_ENTRY": tf_entry}
            assert _fires(T.wt_dc_detailed_entry, d, is_long, dict(ind)) == (float(score) >= thr)
    assert T.wt_dc_detailed_entry(_get({}), True, ind) == (False, "")


def _vec_bounce(cfg, is_long, v):
    bb_min = float(cfg.get("WT_15M_BOUNCE_BB_MIN", 0.05))
    bb_max = float(cfg.get("WT_15M_BOUNCE_BB_MAX", 0.95))
    req_both = bool(cfg.get("WT_15M_BOUNCE_REQUIRE_BOTH_HTF", False))
    w1, w2, w1p, w2p = v["w1"], v["w2"], v["w1p"], v["w2p"]
    up = (w1p <= w2p) and (w1 > w2)
    down = (w1p >= w2p) and (w1 < w2)
    cross = up if is_long else down
    still = (w1 > w2) if is_long else (w1 < w2)
    bb_ok = (v["bb"] >= bb_min) and (v["bb"] <= bb_max)
    r1, r4 = v["r1"], v["r4"]
    h1 = r1 if is_long else (not r1)
    h4 = r4 if is_long else (not r4)
    htf_ok = (h1 and h4) if req_both else (h1 or h4)
    hl_on = bool(cfg.get("WT_15M_BOUNCE_FILTER_HL_ENABLED", False) or cfg.get("WT_15M_BOUNCE_LOW_1H_GT_PREV", False))
    hh_on = bool(cfg.get("WT_15M_BOUNCE_FILTER_HH_ENABLED", False) or cfg.get("WT_15M_BOUNCE_HIGH_1H_GT_PREV", False))
    hl_ok, hh_ok = True, True
    if hl_on or hh_on:
        if hl_on:
            hl_ok = v["dl"] > v["dlp"]
        if hh_on:
            hh_ok = v["dh"] > v["dhp"]
        if str(cfg.get("WT_15M_BOUNCE_FILTER_MODE", "AND")).upper() == "OR":
            if hl_on and not hh_on:
                hlhh = hl_ok
            elif hh_on and not hl_on:
                hlhh = hh_ok
            else:
                hlhh = hl_ok or hh_ok
        else:
            hlhh = hl_ok and hh_ok
    else:
        hlhh = True
    trigger = still if (hl_on or hh_on) else cross
    vol_on = bool(cfg.get("WT_15M_BOUNCE_VOLUME_FILTER_ENABLED", False) or cfg.get("WT_15M_BOUNCE_REL_VOL_GT_1", False))
    vol_ok = True
    if vol_on:
        vmode = str(cfg.get("WT_15M_BOUNCE_VOLUME_MODE", "relvol")).lower()
        vthr = float(cfg.get("WT_15M_BOUNCE_VOLUME_THRESHOLD", 1.0))
        if vmode == "relvol":
            vol_ok = v["rel"] > vthr
        elif vmode == "ema":
            sma_s = v["sma"] if v["sma"] != 0 else 1.0
            vol_ok = v["vol"] > (sma_s * vthr)
        else:
            sma_s = v["sma"] if v["sma"] != 0 else 1.0
            vol_ok = v["vol"] > sma_s
    return bool(trigger and bb_ok and htf_ok and hlhh and vol_ok)


def test_vec_formula_agreement_grid():
    random.seed(11)
    cfgs = [
        {"WT_15M_BOUNCE_OPEN_ENABLED": True},
        {"WT_15M_BOUNCE_OPEN_ENABLED": True, "WT_15M_BOUNCE_LOW_1H_GT_PREV": True},
        {"WT_15M_BOUNCE_OPEN_ENABLED": True, "WT_15M_BOUNCE_HIGH_1H_GT_PREV": True, "WT_15M_BOUNCE_FILTER_MODE": "OR"},
        {"WT_15M_BOUNCE_OPEN_ENABLED": True, "WT_15M_BOUNCE_LOW_1H_GT_PREV": True, "WT_15M_BOUNCE_HIGH_1H_GT_PREV": True, "WT_15M_BOUNCE_REQUIRE_BOTH_HTF": True},
        {"WT_15M_BOUNCE_OPEN_ENABLED": True, "WT_15M_BOUNCE_REL_VOL_GT_1": True},
        {"WT_15M_BOUNCE_OPEN_ENABLED": True, "WT_15M_BOUNCE_REL_VOL_GT_1": True, "WT_15M_BOUNCE_VOLUME_MODE": "ema", "WT_15M_BOUNCE_VOLUME_THRESHOLD": 1.2},
        {"WT_15M_BOUNCE_OPEN_ENABLED": True, "WT_15M_BOUNCE_REL_VOL_GT_1": True, "WT_15M_BOUNCE_VOLUME_MODE": "raw"},
        {"WT_15M_BOUNCE_OPEN_ENABLED": True, "WT_15M_BOUNCE_BB_MIN": 0.2, "WT_15M_BOUNCE_BB_MAX": 0.8},
    ]
    for is_long in (True, False):
        for cfg in cfgs:
            for _ in range(150):
                v = {"w1": random.uniform(-100, 100), "w2": random.uniform(-100, 100),
                     "w1p": random.uniform(-100, 100), "w2p": random.uniform(-100, 100),
                     "bb": random.uniform(-0.1, 1.1), "r1": random.random() < 0.5, "r4": random.random() < 0.5,
                     "dl": random.uniform(90, 110), "dlp": random.uniform(90, 110),
                     "dh": random.uniform(90, 110), "dhp": random.uniform(90, 110),
                     "rel": random.uniform(0.5, 2.0), "vol": random.uniform(0, 200), "sma": random.uniform(0, 200)}
                ind = {"wt1_15m": v["w1"], "wt2_15m": v["w2"], "wt1_15m_prev": v["w1p"], "wt2_15m_prev": v["w2p"],
                       "bb_pct_b_15m": v["bb"], "wt_cross_rising_1h": v["r1"], "wt_cross_rising_4h": v["r4"],
                       "dc_low_1h": v["dl"], "dc_low_1h_prev": v["dlp"], "dc_high_1h": v["dh"], "dc_high_1h_prev": v["dhp"],
                       "relative_volume_15m": v["rel"], "volume_15m": v["vol"], "volume_sma_15m": v["sma"]}
                assert _fires(T.wt_15m_bounce, cfg, is_long, ind) == _vec_bounce(cfg, is_long, v), (is_long, cfg, v)
                k, d = random.uniform(0, 100), random.uniform(0, 100)
                w1, w2 = random.uniform(-100, 100), random.uniform(-100, 100)
                ind2 = {"k_1h": k, "d_1h": d, "wt1_1h": w1, "wt2_1h": w2,
                        "bb_pct_b_1h": random.uniform(-0.5, 1.5), "mfi_1h": random.uniform(0, 100)}
                exp_stoch = ((k < 30 and k > d) if is_long else (k > 70 and k < d))
                assert _fires(T.stoch_entry, {"STOCH_ENTRY_ENABLED": True}, is_long, ind2) == exp_stoch
                exp_wt = ((w1 < -50 and w1 > w2) if is_long else (w1 > 50 and w1 < w2))
                assert _fires(T.wt_entry, {"WT_ENTRY_ENABLED": True}, is_long, ind2) == exp_wt
                b = ind2["bb_pct_b_1h"]
                exp_bb = (b < -0.2) if is_long else (b > 1.0)
                assert _fires(T.bb_pctb_entry, {"BB_PCTB_ENTRY_ENABLED": True}, is_long, ind2) == exp_bb
                m = ind2["mfi_1h"]
                exp_mfi = (m < 60.0) if is_long else (m > 40.0)
                assert T.mfi_gate(_get({"MFI_ENTRY_ENABLED": True}), is_long, ind2) == exp_mfi
                px = random.uniform(50, 150)
                sma = random.uniform(50, 150)
                dist = (px - sma) / sma * 100
                exp_sma = (dist < -3.0) if is_long else (dist > 3.0)
                assert _fires(T.sma200_dist_entry, {"SMA200_DIST_ENTRY_ENABLED": True}, is_long, {"current_price": px, "sma_200_1h": sma}) == exp_sma
                e, p = random.uniform(50, 150), random.uniform(50, 150)
                slope = (e - p) / max(p, 1e-9) * 100
                exp_ema = (slope > 0.05) if is_long else (slope < -0.05)
                assert _fires(T.ema20_slope_entry, {"EMA20_SLOPE_ENTRY_ENABLED": True}, is_long, {"ema_20_1h": e, "ema_20_1h_prev": p}) == exp_ema
                vw = random.uniform(50, 150)
                near = abs(px - vw) / max(vw, 1e-9) * 100 <= 0.3
                exp_vw = near and ((px > vw) if is_long else (px < vw))
                assert _fires(T.vwap_bounce_entry, {"VWAP_BOUNCE_ENTRY_ENABLED": True}, is_long, {"current_price": px, "vwap_D": vw}) == exp_vw
                cw1, cw2, cw1p = random.uniform(-100, 100), random.uniform(-100, 100), random.uniform(-100, 100)
                kk = random.uniform(0, 100)
                ind3 = {"wt1_3m": cw1, "wt2_3m": cw2, "wt1_3m_prev": cw1p, "k_3m": kk}
                exp_xu = ((cw1p >= cw2) and (cw1 < cw2) and (kk >= 70)) if is_long else ((cw1p <= cw2) and (cw1 > cw2) and (kk <= 30))
                assert _fires(T.wt_crossunder_final_exit, {"WT_CROSSUNDER_FINAL_ENABLED": True}, is_long, ind3, "3m") == exp_xu
                pct, prev = random.uniform(-0.2, 1.2), random.uniform(-0.2, 1.2)
                band = random.uniform(0, 120)
                ind4 = {"bb_pct_b_4h": pct, "bb_pct_b_4h_prev": prev, "bb_lower_4h": band, "bb_upper_4h": band}
                exp_b4 = ((prev < 0.20) and (pct > 0.25) and (band > 0)) if is_long else ((prev > 0.80) and (pct < 0.75) and (band > 0))
                assert _fires(T.bb_bounce_entry, {"BB_BOUNCE_ENTRY_TF": "4h"}, is_long, ind4) == exp_b4
