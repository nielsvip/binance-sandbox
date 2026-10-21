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
    monkeypatch.setattr(ve, "KILLS", k)
    monkeypatch.setattr(ve, "VIOLATIONS", v)
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
    mod.Config = type("Config", (), {"VECTOR_ENFORCE_ENABLED": True, "VECTOR_BAR_WINDOW_S": 180.0})
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
    ok, verdict = ve.check("REDUCE", "ang:X_LONG", "VEL_EXIT_v4h-10_g-0.05")
    assert ok is False and verdict == "VIOLATION_FAMILY_KILLED"
    assert json.loads(k.read_text())["VEL_EXIT"]["why"].startswith("non-cross exit")
    ok2, v2 = ve.check("CLOSE", "ang:Y_LONG", "VEL_EXIT_v4h-9_g0.1")
    assert ok2 is False and v2 == "KILLED_FAMILY_VEL_EXIT"
    ok3, _ = ve.check("OPEN", "ang:Z_LONG", "QUICK_OPEN_STRONG_BUY_k1")
    assert ok3 is True, "killing VEL_EXIT must not touch entries"


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
