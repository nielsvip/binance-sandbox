from types import SimpleNamespace

import numpy as np

from vec_paths.v12_exit_reduce_gap_batch6 import consume_lifecycle_actions, evaluate_gap_batch6


def _data(n=4):
    d = {}
    for tf in ("1m", "3m", "15m", "1h", "4h", "D"):
        d[f"wt1_{tf}"] = np.ones(n)
        d[f"wt2_{tf}"] = np.zeros(n)
        d[f"stoch_k_{tf}"] = np.full(n, 50.0)
    for tf in ("15m", "1h", "4h"):
        d[f"dc_position_{tf}"] = np.full(n, .5)
    for edge in ("high", "low"):
        for tf in ("1h", "4h"):
            d[f"dc_{edge}_{tf}"] = np.full(n, 110.0 if edge == "high" else 90.0)
    d["dc_low_3m"] = np.full(n, 90.0); d["dc_high_3m"] = np.full(n, 110.0)
    for tf in ("3m", "1h", "4h"): d[f"wt_velocity_{tf}"] = np.ones(n)
    d["oi_change_1h_pct"] = np.zeros(n); d["close_1h_prev"] = np.full(n, 100.0)
    d["funding_rate_3m"] = np.zeros(n)
    d["ob_ask_wall_pct"] = np.ones(n); d["ob_bid_wall_pct"] = np.ones(n)
    d["ob_ask_wall_size"] = np.full(n, 100000.0); d["ob_bid_wall_size"] = np.full(n, 100000.0)
    for edge in ("high", "low"):
        for tf in ("1h", "4h"):
            d[f"{edge}_{tf}"] = np.full(n, 100.0)
            d[f"{edge}_{tf}_prev"] = np.full(n, 100.0)
    d["stoch_k_1m_prev"] = np.full(n, 50.0)
    return d


def _cfg(**u):
    v = dict(
        NOLOSS_MIN_PROFIT_PCT=0.5,
        OPPOSITE_LOSER_DEEP_LOSS_PCT=-5.0,
        PARTIAL_PROFIT_LOCK_FRAC=0.5,
        HEDGE_DC_RESISTANCE_GATE_ENABLED=False,
        HEDGE_WT_VEL_GATE_ENABLED=False,
        HEDGE_STRICT_WT_ALL_TFS_ENABLED=False,
        HEDGE_TRIGGER_GR_SCORE_ENABLED=True,
        GR_HEDGE_SCORE_FLOOR=15,
        HEDGE_DETERIORATING_GAIN_ENABLED=False,
        HEDGE_NEWBORN_GRACE_MINUTES=0,
        HEDGE_ALL_POSITIONS=True,
        OI_CONFIRM_ENABLED=False,
        OI_HEDGE_GATE_ENABLED=False,
        FUNDING_GATE_ENABLED=False,
        FUNDING_HEDGE_GATE_ENABLED=True,
        RED_ZONE_GATE_ENABLED=False,
        RED_ZONE_HEDGE_GATE_ENABLED=False,
    ); v.update(u); return SimpleNamespace(**v)


def _run(cfg=None, data=None, side="LONG", **s):
    defaults = dict(gain_pct=np.zeros(4), position_age_minutes=np.full(4, 20.0), current_price=np.full(4, 100.0), previous_gain_pct=np.ones(4))
    defaults.update(s)
    return evaluate_gap_batch6(data or _data(), cfg or _cfg(), position_side=side, **defaults)


def test_noloss_exposes_strict_and_inclusive_source_consumers():
    r = _run(gain_pct=np.array([.49, .5, .51, 1]))
    assert r.masks["noloss_profit_gt_filter"].tolist() == [False, False, True, True]
    assert r.masks["noloss_profit_ge_filter"].tolist() == [False, True, True, True]


def test_opposite_deep_loser_excludes_ppl_and_fraction_changes_payload():
    state = dict(ppl_enabled=True, ppl_account_allowed=True, gain_pct=1, position_qty=10, position_min_qty=1, entry_price=100)
    r = _run(_cfg(PARTIAL_PROFIT_LOCK_FRAC=.25), opposite_position_qty=np.array([0, 1, 1, 1]), opposite_position_gain_pct=np.array([-9, -4.9, -5.1, -9]), is_known_hedge=np.array([0, 0, 0, 1]), **state)
    assert r.masks["opposite_deep_loser_hedge_filter"].tolist() == [False, False, True, True]
    assert r.masks["partial_profit_lock_reduce"].tolist() == [True, True, False, False]
    assert r.values["partial_profit_lock_reduce_qty"].tolist() == [2.5, 2.5, 0, 0]


def test_ppl_fraction_drives_exact_reentry_effective_gain():
    r = _run(_cfg(PARTIAL_PROFIT_LOCK_FRAC=.25), gain_pct=3, ppl_fired=np.array([0, 1, 0, 1]))
    assert r.values["ppl_effective_gain_pct"].tolist() == [3, 4, 3, 4]


def test_rank_dc_and_wt_velocity_guards_are_target_side_mirrors():
    d = _data(); d["dc_position_1h"][:] = .1
    # Origin LONG means target hedge SHORT; low DC position rejects it.
    r = _run(_cfg(HEDGE_DC_RESISTANCE_GATE_ENABLED=True), d)
    assert r.masks["hedge_rank_config_block"].all()
    d["dc_position_1h"][:] = .5; d["wt_velocity_1h"][:] = 1; d["wt_velocity_4h"][:] = 1
    wt = _run(_cfg(HEDGE_WT_VEL_GATE_ENABLED=True), d)
    assert wt.masks["hedge_rank_config_block"].all()


def test_rank_strict_wt_count_threshold_changes_admission():
    d = _data()  # all bullish; target SHORT has zero aligned
    r = _run(_cfg(HEDGE_STRICT_WT_ALL_TFS_ENABLED=True, HEDGE_STRICT_WT_MIN_TFS_AGAINST=1), d)
    assert r.values["hedge_strict_wt_aligned_count"].tolist() == [0] * 4
    assert r.masks["hedge_rank_config_block"].all()
    for tf in ("3m", "15m", "1h", "4h", "D"): d[f"wt1_{tf}"][:] = -1
    ok = _run(_cfg(HEDGE_STRICT_WT_ALL_TFS_ENABLED=True, HEDGE_STRICT_WT_MIN_TFS_AGAINST=5), d)
    assert not ok.masks["hedge_rank_config_block"].any()


def test_scan_deterioration_gr_and_newborn_filters_compose():
    d = _data()
    # Against origin LONG on 3m+15m, so technical trigger bypasses deterioration.
    for tf in ("3m", "15m"): d[f"wt1_{tf}"][:] = -1
    cfg = _cfg(HEDGE_DETERIORATING_GAIN_ENABLED=True, HEDGE_NEWBORN_GRACE_MINUTES=10)
    r = _run(cfg, d, gain_pct=-1, previous_gain_pct=-1, position_age_minutes=np.array([5, 5, 11, 11]), current_price=np.array([100, 89, 100, 100]))
    assert r.masks["hedge_newborn_grace_block"].tolist() == [True, False, False, False]
    assert r.masks["hedge_scan_filter_admit"].tolist() == [False, True, True, True]
    gr = _run(_cfg(HEDGE_ALL_POSITIONS=False), d, gain_pct=1, gr_proactive_passes=True)
    assert gr.masks["hedge_scan_filter_admit"].all()


def test_oi_hedge_switch_applies_exact_four_quadrant_gate():
    d = _data(); d["oi_change_1h_pct"] = np.array([1, -1, 1, -1.]);
    r = _run(_cfg(OI_CONFIRM_ENABLED=True, OI_HEDGE_GATE_ENABLED=True, OI_CONFIRM_MIN_CHANGE_PCT=.5, OI_CONFIRM_MIN_PRICE_PCT=.3), d, current_price=np.array([99, 99, 101, 101.]))
    # Target hedge SHORT blocks down+OI-down and up+OI-up.
    assert r.masks["hedge_oi_entry_block"].tolist() == [False, True, True, False]


def test_funding_hedge_switch_uses_shared_gate():
    d = _data(); d["funding_rate_3m"][:] = -.001
    cfg = _cfg(FUNDING_GATE_ENABLED=True, FUNDING_HEDGE_GATE_ENABLED=True, FUNDING_GATE_SHORT_MIN=-.0005, FUNDING_GATE_MTF_REQUIRED=False)
    assert _run(cfg, d).masks["hedge_funding_entry_block"].all()
    assert not _run(_cfg(FUNDING_GATE_ENABLED=True, FUNDING_HEDGE_GATE_ENABLED=False), d).masks["hedge_funding_entry_block"].any()


def test_red_zone_hedge_switch_primary_and_native_fallback():
    d = _data(); d["ob_bid_wall_pct"][:] = .1
    cfg = _cfg(RED_ZONE_GATE_ENABLED=True, RED_ZONE_HEDGE_GATE_ENABLED=True, RED_ZONE_MIN_DISTANCE_PCT=.4, RED_ZONE_MIN_WALL_NOTIONAL_USD=50000, RED_ZONE_GATE_FALLBACK_ENABLED=True)
    assert _run(cfg, d).masks["hedge_red_zone_entry_block"].all()
    # Remove OB recipe: target SHORT fallback wants oversold+rising K and higher lows.
    for k in ("ob_ask_wall_pct", "ob_ask_wall_size", "ob_bid_wall_pct", "ob_bid_wall_size"): d.pop(k)
    d["stoch_k_15m"][:] = 10; d["stoch_k_1m"][:] = 20; d["stoch_k_1m_prev"][:] = 10
    for tf in ("1h", "4h"): d[f"low_{tf}"][:] = 101; d[f"low_{tf}_prev"][:] = 100
    assert _run(cfg, d).masks["hedge_red_zone_entry_block"].all()


def test_missing_rank_recipe_fails_closed_and_reports():
    d = _data(); d.pop("dc_position_4h")
    r = _run(data=d)
    assert r.masks["hedge_rank_config_block"].all()
    assert "dc_position_4h" in r.missing_arrays["hedge_rank_config_guards"]


def test_lifecycle_consumers_are_real_and_explicit():
    d = _data()
    for tf in ("3m", "15m"): d[f"wt1_{tf}"][:] = -1
    r = _run(data=d, gain_pct=-1, ppl_enabled=True, ppl_account_allowed=True)
    c = consume_lifecycle_actions(r)
    assert {"REDUCE", "HEDGE_OPEN", "EXIT_FILTER_GT", "EXIT_FILTER_GE", "REDUCE_FILTER_GT"} <= set(c.masks)
    assert c.masks["HEDGE_OPEN"].all()
