"""Hold-before-judgement + verified-identical release + sweep priority pins."""

from pathlib import Path

from live_twins import vec_pending as vp
from live_twins.vec_priority import prioritize

ROOT = Path(__file__).resolve().parents[1]


def test_reason_family_strips_tags_and_bar_params():
    assert vp.reason_family("B_KZONE |VEC_EXACT") == "B_KZONE"
    assert vp.reason_family("MTF_DC_REJECT_1h_px1.3630") == vp.reason_family(
        "MTF_DC_REJECT_1h_px1.3620 |VEC_EXACT"
    )
    assert vp.reason_family("") == ""


def test_reasons_match_same_path_param_drift_ok():
    assert (
        vp.reasons_match("REENTRY_HIGHER_LOW", "REENTRY_HIGHER_LOW |VEC_EXACT") is True
    )
    assert vp.reasons_match("B_KZONE", "B_BOUNCE_DONCHIAN") is False
    assert vp.reasons_match("", "B_KZONE") is False
    assert vp.reasons_match("B_KZONE", "") is False


def test_preemptive_hold_files_and_releases_same_direction():
    vp._HOLDS.clear()
    hold, _, is_new = vp.file_preemptive_hold(
        "crypto", "ang:BNBUSDC_LONG", "OPEN", "B_KZONE"
    )
    assert hold is not None and is_new is True
    assert vp.release_for("ang:BNBUSDC_LONG", "OPEN") is not None
    assert vp.release_for("ang:BNBUSDC_LONG", "OPEN") is None
    vp._HOLDS.clear()


def test_preemptive_hold_fail_open_on_protective():
    vp._HOLDS.clear()
    assert (
        vp.file_preemptive_hold("crypto", "ang:BNBUSDC_LONG", "OPEN", "FIXED_STOP_HIT")
        is None
    )
    assert vp.pending_count() == 0


def test_ratio_sizing_never_held():
    from live_twins.vec_pending import protective

    vp._HOLDS.clear()
    assert protective("RATIO_REDUCE_SHORT_L60_S40_tgt50/50_breadth10%_gain1.0%") is True
    assert protective("RATIO_REBALANCE_AUGMENT_LONG_L40_S60_tgt50/50_4x_score7") is True
    assert protective("RATIO_CLOSE_OW_SHORT_L70_S30_tgt50/50_wt15d1.2_g2.00%") is False
    assert vp.pending_count() == 0


def test_ratio_sizing_exempt_from_dictate_only():
    from vec_decisions.vec_parity_gates import dictate_only_exempt

    assert (
        dictate_only_exempt(
            "REDUCE", "RATIO_REDUCE_SHORT_L60_S40_tgt50/50_breadth10%_gain1.0%"
        )
        is True
    )
    assert (
        dictate_only_exempt(
            "AUGMENT", "RATIO_REBALANCE_AUGMENT_LONG_L40_S60_tgt50/50_4x_score7"
        )
        is True
    )
    assert (
        dictate_only_exempt(
            "CLOSE", "RATIO_CLOSE_OW_SHORT_L70_S30_tgt50/50_wt15d1.2_g2.00%"
        )
        is False
    )


def test_prioritize_vec_first_capped_no_starvation():
    keys = ["a", "b", "c", "d"]
    assert prioritize(keys, {"c", "d"}) == ["c", "d", "a", "b"]
    assert prioritize(keys, {"b", "c", "d"}) == ["b", "c", "a", "d"]
    assert prioritize(keys, set()) == keys
    assert prioritize([], {"a"}) == []


def test_hedging_forbidden_no_loops_no_engine():
    import asyncio

    ez = (ROOT / "ez_manage.py").read_text()
    tr = (ROOT / "tradier_manage.py").read_text()
    assert "create_task(monitor_hedges_continuously" not in ez
    assert "create_task(obligatory_sector_hedge_or_close_loop" not in tr
    assert "create_task(overnight_gap_hedge_loop" not in tr
    from ez_positions_quick import HedgeEngine

    async def _go():
        return await HedgeEngine.execute_same_symbol_hedge(
            None, None, None, None, None, None, None
        )

    assert asyncio.run(_go()) is False
    import config_tradier

    assert config_tradier.TradierConfig.OVERNIGHT_GAP_HEDGE_ENABLED is False


def test_both_inners_wire_hold_verify_priority():
    ez = (ROOT / "ez_manage.py").read_text()
    tr = (ROOT / "tradier_manage.py").read_text()
    for src in (ez, tr):
        assert "file_preemptive_hold" in src
        assert "VEC_HOLD_BEFORE_JUDGEMENT" in src
        assert "reasons_match" in src
        assert "VERIFIED_IDENTICAL" in src
        assert "VEC_PRIORITY_ENABLED" in src
        assert "vec_priority" in src
