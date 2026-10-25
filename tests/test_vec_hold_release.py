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


def test_prioritize_vec_first_capped_no_starvation():
    keys = ["a", "b", "c", "d"]
    assert prioritize(keys, {"c", "d"}) == ["c", "d", "a", "b"]
    assert prioritize(keys, {"b", "c", "d"}) == ["b", "c", "a", "d"]
    assert prioritize(keys, set()) == keys
    assert prioritize([], {"a"}) == []


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
