"""USER 2026-10-24: ratio_rebalance_loop fired REDUCE/CLOSE with no master switch
(loop started unconditionally; RATIO_REBALANCE_ENABLED existed nowhere except a
no-op bible-registration shim). Pins the kill-switch wiring. Source-level pins,
no live stack import (mirrors test_converge_switch_wired.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_ratio_master_default_true_both_configs():
    import config as C
    from v12_quick_engine import QuickConfig

    assert C.Config.RATIO_REBALANCE_ENABLED is True
    assert QuickConfig.RATIO_REBALANCE_ENABLED is True


def test_ratio_loop_gated_live():
    src = (ROOT / "ez_manage.py").read_text()
    i_fn = src.index("async def ratio_rebalance_loop")
    region = src[i_fn:i_fn + 3000]
    assert 'getattr(config, "RATIO_REBALANCE_ENABLED", True)' in region
    assert "loop exits" in region
    assert "queue_trade_action" in src[i_fn:i_fn + 20000]


def test_ratio_template_rows_crypto():
    for fn in ("TEMPLATE_CRYPTO_LONG.xlsx", "TEMPLATE_CRYPTO_SHORT.xlsx"):
        wb = openpyxl.load_workbook(
            ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / fn, read_only=True
        )
        hits = set()
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                if not row:
                    continue
                vals = [str(c) for c in row if c is not None]
                if vals and vals[0] == "RATIO_REBALANCE_ENABLED":
                    hits.add(vals[1] if len(vals) > 1 else None)
        assert {"True", "False"} <= {str(h) for h in hits}, (fn, hits)
