"""Durable regression for 2026-09-11 ENTRY_VET fix:

- ADX_RANGING_THRESHOLD_THR was checking `close` (always 0→always blocked).
  Now checks `adx_1h` vs threshold 10 (user default).
- ENTRY_VET NO_TRIGGER was blocking ZEC LONG when wt cross / DC breakout /
  wt+k/d trigger absent. ENTRY_VET_RELAX_MODE=3 now auto-passes.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import ez_manage as ez_manage_mod
from ez_manage import _batch1_template_live_gate, check_entry_vetting

# ez_manage uses its own singleton `config = Config()` (ez_manage.py:6882),
# so module-level `config` and `ez_manage.config` are distinct. Patch both.
def _set_both(attr, value):
    setattr(config, attr, value)
    setattr(ez_manage_mod.config, attr, value)


def _indicators(**kwargs):
    base = {
        "adx_1h": 47.0,
        "close": 1080.0,
        "current_price": 1080.0,
        "dc_high_3m": 1080.0,
        "dc_high_3m_ant": 1075.0,
        "low_15m": 1070.0,
        "low_15m_prev": 1060.0,
        "wt_cross_1m": "BULL",
        "wt_cross_3m": "BULL",
        "wt1_3m": 10.0,
        "wt2_3m": 5.0,
        "k_3m": 60.0,
        "d_3m": 50.0,
        "k_1m": 60.0,
        "k_1m_prev": 50.0,
        "d_1m": 50.0,
        "high_15m": 1080.0,
        "high_15m_prev": 1075.0,
    }
    base.update(kwargs)
    return base


def test_adx_threshold_uses_adx_not_close():
    """Batch1 ADX gate now checks adx_1h=47 (>10) not close=0."""
    # Before fix: close missing →0 <=10 → ADX_RANGING_THRESHOLD_THR (always blocked).
    # After fix: adx_1h checked, so high ADX must NOT be the blocker.
    _set_both("ADX_RANGING_THRESHOLD", 10.0)
    # High ADX: should NOT be blocked on ADX (may block later on other gate, but not ADX)
    _, reason_high = _batch1_template_live_gate(_indicators(adx_1h=47.0, k_3m=30.0), is_long=True)
    assert reason_high != "ADX_RANGING_THRESHOLD_THR", f"high ADX incorrectly blocked on ADX: {reason_high}"
    # Also when close missing, high ADX still not blocked on ADX
    ind_no_close = _indicators(adx_1h=47.0, k_3m=30.0)
    ind_no_close.pop("close", None)
    _, reason_nc = _batch1_template_live_gate(ind_no_close, is_long=True)
    assert reason_nc != "ADX_RANGING_THRESHOLD_THR"
    # Low ADX below threshold SHOULD still block on ADX (5 <=10)
    allowed_low, reason_low = _batch1_template_live_gate(_indicators(adx_1h=5.0, k_3m=30.0), is_long=True)
    assert allowed_low is False
    assert reason_low == "ADX_RANGING_THRESHOLD_THR"


def test_adx_threshold_disabled_when_zero():
    _set_both("ADX_RANGING_THRESHOLD", 0.0)
    _, reason = _batch1_template_live_gate(_indicators(adx_1h=5.0, k_3m=30.0), is_long=True)
    # When threshold is 0, ADX gate is disabled → should not be ADX blocker
    assert reason != "ADX_RANGING_THRESHOLD_THR"
    _set_both("ADX_RANGING_THRESHOLD", 10.0)  # restore


def test_entry_vet_relax_mode_3_bypasses_no_trigger():
    """With RELAX_MODE=3, NO_TRIGGER must not fire even when trigger absent."""
    # Force trigger-false: no wt cross, price not breaking DC, wt1<wt2 and k<d
    ind = _indicators(
        wt_cross_1m=None,
        wt_cross_3m=None,
        wt1_3m=-10.0,
        wt2_3m=5.0,
        k_3m=30.0,
        d_3m=50.0,
        dc_high_3m=2000.0,  # price below DC so no breakout
    )
    _set_both("ENTRY_VET_RELAX_MODE", 3)
    _, reason = check_entry_vetting(ind, current_price=1080.0, is_long=True)
    assert "NO_TRIGGER" not in reason and "NO_STRUCT" not in reason, f"mode3 should bypass, got {reason}"
    # Mode 1 must block with NO_TRIGGER / NO_STRUCT (prove old failure)
    _set_both("ENTRY_VET_RELAX_MODE", 1)
    allowed2, reason2 = check_entry_vetting(ind, current_price=1080.0, is_long=True)
    assert allowed2 is False
    assert "NO_TRIGGER" in reason2 or "NO_STRUCT" in reason2
    _set_both("ENTRY_VET_RELAX_MODE", 3)  # restore

def test_entry_vet_symmetry_long_short():
    """ENTRY_VET trigger is opposite for LONG vs SHORT (BULL vs BEAR)."""
    _set_both("ENTRY_VET_RELAX_MODE", 1)
    # LONG trigger via BULL cross + wt1>wt2/k>d + rising structure should NOT be NO_TRIGGER for LONG
    ind_bull = _indicators(wt_cross_1m="BULL", wt1_3m=10, wt2_3m=5, k_3m=70, d_3m=40, dc_high_3m=900, dc_high_3m_ant=890, low_15m=1070, low_15m_prev=1060)
    _, reason_long = check_entry_vetting(ind_bull, current_price=1080, is_long=True)
    assert "NO_TRIGGER" not in reason_long, f"LONG BULL should not be NO_TRIGGER, got {reason_long}"
    # Same BULL indicators for SHORT must be opposite → should be NO_TRIGGER/NO_STRUCT
    _, reason_short = check_entry_vetting(ind_bull, current_price=1080, is_long=False)
    assert "NO_TRIGGER" in reason_short or "NO_STRUCT" in reason_short
    _set_both("ENTRY_VET_RELAX_MODE", 3)
