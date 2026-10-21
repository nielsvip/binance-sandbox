"""GAP-C 2026-10-21 EXACT-PARITY: WT_2of3 reentries fired live with no enable
gate while the board switch defaults False with zero carriers. Pins the gate
order in evaluate_reentry. Source-level pin, no live stack import."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_wt2of3_default_off():
    import config_tradier as C

    assert C.TradierConfig.REENTRY_B01_WT_2of3_ENABLED is False


def test_wt2of3_gate_before_stoch():
    src = (ROOT / "tradier_manage.py").read_text()
    i = src.index("GAP-C 2026-10-21 EXACT-PARITY: WT_2of3_REENTRY fired live")
    window = src[i:i + 700]
    assert 'if _wt_fav >= 2 and bool(_cfg_auto("REENTRY_B01_WT_2of3_ENABLED", False)):' in window
