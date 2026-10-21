"""USER 2026-10-21 fresh-fill grace: converge-to-flat never flattens a fill the twin never saw.

Measured: flz:ZECUSDC_LONG native AUG filled 22:52:04, flattened as paper-flat-live-holds 44 s later.
Source-level pins, no live stack import.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_grace_default_900():
    import config as C

    assert float(C.Config.VEC_CONVERGE_FRESH_FILL_GRACE_S) == 900.0


def test_grace_wired_in_converge_block():
    src = (ROOT / "ez_manage.py").read_text()
    i = src.index("VEC_CONVERGE_FRESH_FILL_GRACE_S")
    region = src[i:i + 2000]
    assert "_cvg_on = False" in region  # fresh fills skip the flatten
    assert "updateTime" in region  # augment-fresh counts, not just opened_at
    assert "opened_at" in region
    assert "_cvg_grace > 0" in region  # 0 disables the grace (ROLLBACK)


def test_grace_does_not_touch_stale_flatten_or_owed():
    src = (ROOT / "ez_manage.py").read_text()
    i = src.index("VEC_STALE_FLATTEN_BARS")
    assert "FRESH_FILL_GRACE" not in src[i:i + 3000]
    assert "VEC_EXIT_OWED" in src  # exit-owed path intact
