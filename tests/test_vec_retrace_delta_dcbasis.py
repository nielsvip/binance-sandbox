"""USER 2026-10-24 retrace DELTA/DC_BASIS TODOs: narrow DC_BASIS family restored
(modeled 3m leg, per-sym gated, shared twin); untwinned legs stay dead
(DELTA floor leg per-sym gated but family refused; PROFIT leg default-OFF).
Source-level pins, no live stack import.
"""
from __future__ import annotations

import sys
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from live_twins.vector_enforce import VEC_SWITCH_EXIT_FAMILIES, family_of


def test_narrow_family_mapping():
    assert family_of("DELTA_EXIT_DC15M_FLOOR_BREAK") == "DELTA"
    assert family_of("DC_BASIS_PROFIT_EXIT") == "DC_BASIS"
    assert family_of("DC_BASIS_3M_REDUCE: price_below_dc_basis_3m") == "DC_BASIS"
    assert family_of("DC_BREACH_X") == "DC_BREACH"
    assert family_of("DC_15M_HOLD_OVERRIDE") == "DC"
    assert family_of("DC_BB_D_BREAK_X") == "DC"
    assert family_of("E_1_WT_DELTA_EXIT_delta=-19") == "E_1"


def test_allowlist_dc_basis_in_delta_out():
    assert "DC_BASIS" in VEC_SWITCH_EXIT_FAMILIES
    assert "DELTA" not in VEC_SWITCH_EXIT_FAMILIES


def test_profit_switch_default_off_both_configs():
    import config as C
    from v12_quick_engine import QuickConfig

    assert C.Config.DC_BASIS_PROFIT_EXIT_ENABLED is False
    assert QuickConfig.DC_BASIS_PROFIT_EXIT_ENABLED is False
    assert C.Config.DELTA_EXIT_ENABLED is True


def test_live_legs_gated():
    src = (ROOT / "ez_manage.py").read_text()
    assert '_psym_get(symbol, position_side, "DELTA_EXIT_ENABLED", True)' in src
    assert (
        '_psym_get(symbol, position_side, "DC_BASIS_PROFIT_EXIT_ENABLED", False)'
        in src
    )


def test_profit_template_rows_crypto():
    for fn in ("TEMPLATE_CRYPTO_LONG.xlsx", "TEMPLATE_CRYPTO_SHORT.xlsx"):
        wb = openpyxl.load_workbook(
            ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / fn, read_only=True
        )
        try:
            ws = wb["REDUCE_PROFIT_LOCK"]
            hits = [
                r[0].value
                for r in ws.iter_rows(min_row=3, max_col=1)
                if r[0].value == "DC_BASIS_PROFIT_EXIT_ENABLED"
            ]
            assert len(hits) >= 2, fn
        finally:
            wb.close()
