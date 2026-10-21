"""Vec-decided supremacy (USER 2026-10-21): nothing not in the vectorized tests may refuse a trade.

- live_twins.vector_enforce.check: exits are NEVER refused; vec-decided entries skip the bar window.
- tools.forward_parity.live_guardian: NON_VEC/NATIVE_FILL detectors are exact-mode-only.
- ez_manage.execute_now: LOSS_PRONE never refuses; PARITY_DEDUP allows vec-decided (source-pinned).
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from live_twins import vector_enforce as ve


@pytest.fixture()
def isolated_kills(tmp_path, monkeypatch):
    monkeypatch.setattr(ve, "KILLS", tmp_path / "kills.json")
    monkeypatch.setattr(ve, "VIOLATIONS", tmp_path / "viol.jsonl")
    ve._KILLS["loaded"] = 0.0
    ve._KILLS["killed"] = {}
    yield tmp_path


def test_exit_never_refused(isolated_kills, monkeypatch):
    monkeypatch.setattr(ve, "in_bar_window", lambda now=None: False)
    ok, verdict = ve.check("CLOSE", "men:RVNUSDT_LONG", "VEL_EXIT_v4h-9.92_g0.06")
    assert ok is True
    ok, _ = ve.check("REDUCE", "men:LTCUSDC_LONG", "SOME_NATIVE_REASON")
    assert ok is True


def test_exit_does_not_kill_family(isolated_kills):
    ve.check("CLOSE", "men:RVNUSDT_LONG", "VEL_EXIT_v4h-9.92_g0.06")
    assert ve._load_kills() == {}


def test_vec_entry_skips_bar_window(isolated_kills, monkeypatch):
    monkeypatch.setattr(ve, "in_bar_window", lambda now=None: False)
    ok, verdict = ve.check("OPEN", "men:AXSUSDT_LONG", "VEC_DRIVEN_OPEN_PURE_BOARD_X")
    assert (ok, verdict) == (True, "VEC_DECIDED")
    ok, verdict = ve.check("OPEN", "men:AXSUSDT_LONG", "B12 |VEC_EXACT")
    assert (ok, verdict) == (True, "VEC_DECIDED")


def test_native_midbar_entry_still_refused(isolated_kills, monkeypatch):
    monkeypatch.setattr(ve, "in_bar_window", lambda now=None: False)
    ok, verdict = ve.check("OPEN", "men:BBUSDT_LONG", "QUICK_OPEN_STRONG_BUY_k1:80/d166")
    assert (ok, verdict) == (False, "OFF_BAR_WINDOW")


def test_native_inwindow_entry_allowed(isolated_kills, monkeypatch):
    monkeypatch.setattr(ve, "in_bar_window", lambda now=None: True)
    ok, _ = ve.check("OPEN", "men:BBUSDT_LONG", "QUICK_OPEN_STRONG_BUY_k1:80/d166")
    assert ok is True


def test_guardian_exact_mode_follows_config(monkeypatch):
    from tools.forward_parity import live_guardian as G

    import config as C

    G._EXACT_MODE.update(v=None, t=0.0)
    monkeypatch.setattr(C.Config, "PARITY_VEC_EXACT_MODE", False, raising=False)
    assert G.exact_mode_on() is False
    G._EXACT_MODE.update(v=None, t=0.0)
    monkeypatch.setattr(C.Config, "PARITY_VEC_EXACT_MODE", True, raising=False)
    assert G.exact_mode_on() is True
    G._EXACT_MODE.update(v=None, t=0.0)


def test_ez_manage_loss_prone_never_refuses():
    src = (ROOT / "ez_manage.py").read_text()
    assert 'return "BLOCKED_LOSS_PRONE_ENTRY_FILTER"' not in src
    assert "LOSS_PRONE_WOULD_REFUSE" in src


def test_ez_manage_dedup_vec_allow_pinned():
    src = (ROOT / "ez_manage.py").read_text()
    assert "_vec_decided" in src
    assert "VEC_DECIDED_DEDUP_ALLOW" in src
