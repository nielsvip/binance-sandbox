"""vector_enforce (USER 2026-10-21): bar-boundary cross-confirmed execution; violations kill the family."""

import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live_twins import vector_enforce as ve


def _iso(tmp_path, monkeypatch, enforce=True):
    k = tmp_path / "kills.json"
    v = tmp_path / "viol.jsonl"
    q = tmp_path / "retrace.jsonl"
    monkeypatch.setattr(ve, "KILLS", k)
    monkeypatch.setattr(ve, "VIOLATIONS", v)
    monkeypatch.setattr(ve, "RETRACE_QUEUE", q)
    ve._KILLS.update(loaded=0.0, killed={})
    mod = types.ModuleType("config")
    mod.Config = type(
        "Config", (), {"VECTOR_ENFORCE_ENABLED": enforce, "VECTOR_BAR_WINDOW_S": 180.0}
    )
    monkeypatch.setitem(sys.modules, "config", mod)
    monkeypatch.setattr(ve, "in_bar_window", lambda now=None: True)
    return k, v


def test_bar_window_math(tmp_path, monkeypatch):
    import types as _t
    import sys as _s

    mod = _t.ModuleType("config")
    mod.Config = type(
        "Config", (), {"VECTOR_ENFORCE_ENABLED": True, "VECTOR_BAR_WINDOW_S": 180.0}
    )
    monkeypatch.setitem(_s.modules, "config", mod)
    assert ve.in_bar_window(1800 * 900 + 10) is True
    assert ve.in_bar_window(1800 * 900 + 400) is False
    assert ve.in_bar_window(1800 * 900 + 899) is False


def test_cross_exits_allowed(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    for reason in (
        "WT_LOWER_CROSS_EXIT wt_1h",
        "DC_BREACH_REDUCE_UNHEDGED_LOW_15m",
        "R3_HTF_FLIP_DAILY_g1%",
        "VEC_DRIVEN_OPEN_RECONCILE_",
        "FIXED_STOP_ADOPTED 123",
        "BROKER_RECONCILED order=1",
        "LH_LL_TOP_EXIT_arm=4h",
        "OBLIGATORY_REENTRY_X",
    ):
        ok, verdict = ve.check("CLOSE", "ang:X_LONG", reason)
        assert ok is True, (reason, verdict)


def test_non_cross_exit_kills_family(tmp_path, monkeypatch):
    k, v = _iso(tmp_path, monkeypatch)
    ok, verdict = ve.check("REDUCE", "ang:X_LONG", "MAKER_PROFIT_EXIT_px1_g0.05")
    assert ok is False and verdict == "VIOLATION_FAMILY_KILLED"
    assert json.loads(k.read_text())["MAKER_PROFIT_EXIT"]["why"].startswith(
        "non-cross exit"
    )
    ok2, v2 = ve.check("CLOSE", "ang:Y_LONG", "MAKER_PROFIT_EXIT_px2_g0.1")
    assert ok2 is False and v2 == "KILLED_FAMILY_MAKER_PROFIT_EXIT"
    ok3, _ = ve.check("OPEN", "ang:Z_LONG", "QUICK_OPEN_STRONG_BUY_k1")
    assert ok3 is True, "killing an exit family must not touch entries"


def test_vel_exit_not_exempt_until_twin_faithful(tmp_path, monkeypatch):
    """2026-10-21 bar replay: the vector leg exists and switches agree, yet live
    fired where the vector would not on 4/5 bars — (A) forming-bar 4h velocity
    sign-flips vs completed-bar NPZ, (B) no live twin of the STRUCTURAL_EXIT_GATE
    veto. Modeled is not enough; re-exempt ONLY with a faithful twin (completed-bar
    velocity + scalar structural veto, proven by replay). Until then it kills."""
    assert "VEL_EXIT" not in ve.VEC_SWITCH_EXIT_FAMILIES
    k, v = _iso(tmp_path, monkeypatch)
    assert ve.family_of("VEL_EXIT_v4h-10_g-0.05") == "VEL_EXIT"
    ok, verdict = ve.check("CLOSE", "ang:X_LONG", "VEL_EXIT_v4h-10_g-0.05")
    assert ok is False and verdict == "VIOLATION_FAMILY_KILLED"
    assert "VEL_EXIT" in json.loads(k.read_text())
    ok2, v2 = ve.check("CLOSE", "ang:Y_LONG", "VEL_EXIT_v4h-9_g0.1")
    assert ok2 is False and v2 == "KILLED_FAMILY_VEL_EXIT"


def test_new_kill_files_retrace_order(tmp_path, monkeypatch):
    """Every newly killed family files one TODO retrace work order (BIBLE §126.1)."""
    k, v = _iso(tmp_path, monkeypatch)
    q = ve.RETRACE_QUEUE
    ok, _ = ve.check("CLOSE", "ang:X_LONG", "MAKER_PROFIT_EXIT_px1_g0.05")
    assert ok is False
    rows = [json.loads(line) for line in q.read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["family"] == "MAKER_PROFIT_EXIT"
    assert rows[0]["status"] == "TODO" and "retrace" in rows[0]
    ok2, _ = ve.check("CLOSE", "ang:Y_LONG", "MAKER_PROFIT_EXIT_px2_g0.1")
    assert ok2 is False
    rows2 = [json.loads(line) for line in q.read_text().splitlines()]
    assert len(rows2) == 1, "one TODO row per family, no per-bar spam"


def test_off_bar_entries_refused_without_kill(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    import time as _t

    now = int(_t.time() // 900) * 900 + 400
    monkeypatch.setattr(ve, "in_bar_window", lambda now=None: False)
    ok, verdict = ve.check("QUICK_OPEN", "ang:X_LONG", "QUICK_OPEN_STRONG_BUY_k1")
    assert (ok, verdict) == (False, "OFF_BAR_WINDOW")
    assert not ve.KILLS.exists(), "timing refusal must not kill"


def test_enforce_off_allows_all(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch, enforce=False)
    assert ve.check("REDUCE", "a", "VEL_EXIT_x") == (True, "ENFORCE_OFF")


def test_execute_now_calls_enforcer():
    src = (ROOT / "ez_manage.py").read_text()
    assert "vector_enforce" in src and "VECTOR_EXEC_REFUSED" in src


def test_quick_category_never_killed(tmp_path, monkeypatch):
    """USER 2026-10-21 retrace: QUICK_* is the whole twin-lane dispatcher — the family
    is the embedded rec token, and the category itself can never be disabled."""
    k, v = _iso(tmp_path, monkeypatch)
    assert (
        ve.family_of(
            "QUICK_REDUCE_SCALP_REDUCE_k_1m:89/d_1m:89_TIGHT_LEASH_PROFIT_SAVE_REDUCE"
        )
        == "SCALP_REDUCE"
    )
    ok, verdict = ve.check(
        "REDUCE", "inf:X_LONG", "QUICK_REDUCE_SCALP_REDUCE_k_1m:89_TIGHT_LEASH_REDUCE"
    )
    assert (ok, verdict) == (False, "VIOLATION_FAMILY_KILLED")
    assert "SCALP_REDUCE" in json.loads(
        k.read_text()
    ), "sub-rec family killed, not QUICK_*"
    assert "QUICK_REDUCE" not in json.loads(
        k.read_text()
    ) and "QUICK" not in json.loads(k.read_text())
    ok2, v2 = ve.check(
        "REDUCE", "inf:Y_LONG", "QUICK_CLOSE_STRONG_REDUCE_k1_TIGHT_REDUCE"
    )
    assert (ok2, v2) == (False, "VIOLATION_FAMILY_KILLED")
    assert "STRONG_REDUCE" in json.loads(k.read_text())


def test_vec_switch_exits_skip_kill_keep_window(tmp_path, monkeypatch):
    """BB_TAKE_4h under a promoting board is vector-modeled switch output, not a violation."""
    _iso(tmp_path, monkeypatch)
    assert ve.family_of("CLOSE BB_TAKE_4h_g0.02") == "BB_TAKE"
    ok, verdict = ve.check("CLOSE", "inf:X_LONG", "BB_TAKE_4h_g0.02")
    assert (ok, verdict) == (True, "VEC_SWITCH_EXIT")
    monkeypatch.setattr(ve, "in_bar_window", lambda now=None: False)
    ok2, v2 = ve.check("CLOSE", "inf:X_LONG", "BB_TAKE_4h_g0.02")
    assert (ok2, v2) == (False, "OFF_BAR_WINDOW")


def test_board_confirmed_and_stops_skip_window(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    monkeypatch.setattr(ve, "in_bar_window", lambda now=None: False)
    assert ve.check("CLOSE", "a", "VEC_DRIVEN_EXIT_RECONCILE_x")[0] is True
    assert ve.check("CLOSE", "a", "BOARD_CONVERGE_CLOSE vec=leg")[0] is True
    assert ve.check("CLOSE", "a", "FIXED_STOP_HIT_px")[0] is True
    ok, v = ve.check("CLOSE", "a", "WT_LOWER_CROSS_EXIT")
    assert (ok, v) == (False, "OFF_BAR_WINDOW")
