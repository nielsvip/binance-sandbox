"""USER 2026-10-23 hawk PROFIT_TARGET (trc:CDE_SHORT kill): the switch-gated
pct take-profit (v12 leg + both live twins, default OFF, 25 per-sym backtest
holds) is its own narrow family. Proves the narrow mapping, the broad PROFIT
kill staying armed, and the v12 walk booking closes only when ON."""

import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from live_twins import vector_enforce as ve

CDE = ROOT / "backtest_v8" / "indicators" / "CDE.npz"


def _iso(tmp_path, monkeypatch):
    k = tmp_path / "kills.json"
    v = tmp_path / "viol.jsonl"
    q = tmp_path / "retrace.jsonl"
    m = tmp_path / "monitor.jsonl"
    monkeypatch.setattr(ve, "KILLS", k)
    monkeypatch.setattr(ve, "VIOLATIONS", v)
    monkeypatch.setattr(ve, "RETRACE_QUEUE", q)
    monkeypatch.setattr(ve, "MONITOR_LOG", m)
    ve._KILLS.update(loaded=0.0, killed={})
    mod = types.ModuleType("config")
    mod.Config = type(
        "Config", (), {"VECTOR_ENFORCE_ENABLED": True, "VECTOR_BAR_WINDOW_S": 180.0}
    )
    monkeypatch.setitem(sys.modules, "config", mod)
    monkeypatch.setattr(ve, "in_bar_window", lambda now=None: True)
    return k


def test_profit_target_narrow_while_broad_profit_stays_killed(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    assert ve.family_of("PROFIT_TARGET_g4.30_g4.30") == "PROFIT_TARGET"
    assert ve.family_of("PROFIT_TP_EXHAUSTION") == "PROFIT"
    assert ve.family_of("PROFIT_LOCK") == "PROFIT"
    ok, verdict = ve.check("CLOSE", "trc:CDE_SHORT", "PROFIT_TARGET_g4.30_g4.30")
    assert ok is True and verdict == "VEC_SWITCH_EXIT", verdict
    bad, v2 = ve.check("CLOSE", "trc:X_SHORT", "PROFIT_LOCK_g1.00")
    assert bad is False and v2 == "VIOLATION_FAMILY_KILLED", v2
    bad2, v3 = ve.check("CLOSE", "trc:Y_SHORT", "PROFIT_TP_EXHAUSTION")
    assert bad2 is False and v3 == "KILLED_FAMILY_PROFIT", v3


@pytest.mark.skipif(not CDE.exists(), reason="CDE.npz not synced on this host")
def test_walk_books_profit_target_only_when_on():
    import numpy as np

    import v12_quick_engine as V

    npz = np.load(str(CDE), allow_pickle=True)

    def pt_rows(on):
        cfg = V.QuickConfig()
        cfg.PROFIT_TARGET_ENABLED = on
        cfg.PROFIT_TARGET_PCT = 1.6
        res = V.simulate_one(npz, "CDE", False, cfg, force_initial_seed=True)
        return [
            e for e in res.get("ledger", []) if str(e.get("reason")) == "PROFIT_TARGET"
        ]

    assert pt_rows(False) == []
    on = pt_rows(True)
    assert len(on) > 0, "switch ON must book PROFIT_TARGET closes on CDE SHORT"
    assert all(e.get("type") == "CLOSE" for e in on)
