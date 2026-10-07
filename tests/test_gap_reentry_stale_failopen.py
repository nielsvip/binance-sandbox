"""Regression for 2026-09-14 no-reentry outage (zero orders, frozen indicators).

- SYMGATE stale fail-open: with no 5m/15m WT data at all, DeltaTracker speeds
  read exactly 0.0 and the old speed floor blocked EVERY reentry candidate.
  would_exit_trigger_now must NOT block on the speed floor in that case
  (score/DELTA/zone blocks stay intact).
- GAP pending persistence: _gap_moc_save_pending/_gap_moc_load_pending round-trip
  so Friday gap exits survive a restart into Monday's 09:30-11:00 rebuy window.
"""
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

import tradier_manage as tm
from wt_dc_delta import DeltaTracker


def _stub_manager():
    stub = SimpleNamespace()
    stub.delta_tracker = DeltaTracker()
    return stub


def test_symgate_stale_speed_floor_waived():
    # Empty indicators: no 5m/15m WT keys at all, live price present.
    ind = {"current_price": 100.0}
    blocked, reason = tm.TradierTradeManager.would_exit_trigger_now(
        _stub_manager(), "MU", ind, "LONG", current_price=100.0
    )
    assert not blocked, f"stale zero-data speed floor must not block, got: {reason}"


def test_gap_pending_persists_across_restart(tmp_path, monkeypatch):
    f = tmp_path / "gap_pending.json"
    monkeypatch.setattr(tm, "_GAP_MOC_PENDING_FILE", f)
    tm._GAP_MOC_PENDING_REENTRY.clear()
    tm._GAP_MOC_PENDING_REENTRY["trb:MU_LONG"] = {
        "amount": 2.0, "exit_price": 900.0, "exit_gain": -1.0,
        "ts": 1234.0, "avg_gap": -0.5, "which": "OPEN",
    }
    tm._gap_moc_save_pending()
    assert f.exists() and f.stat().st_size > 2
    # simulate restart wipe
    tm._GAP_MOC_PENDING_REENTRY.clear()
    tm._gap_moc_load_pending()
    assert tm._GAP_MOC_PENDING_REENTRY.get("trb:MU_LONG", {}).get("exit_price") == 900.0
    tm._GAP_MOC_PENDING_REENTRY.clear()
    if f.exists():
        f.unlink()
