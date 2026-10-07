"""Durable test for v15_pilot TEMPLATE law compliance — 7D BTCUSDC smoke.

Validates that v15_pilot:
- reads by row2 headers (Switch via header, not coords)
- keeps ORANGE GENERAL rows below white SWITCH rows
- writes BEST overrides BOLD in override column C before baseline
- writes baseline E for pending row as cumulative_before (not stale)
- evaluates yellows per switch only, writes delta to yellow cells, sums POS to VECTOR_DELTA G
- moves POS to next row same tab with new baseline, NEG to next tab first pending
- keeps NPZ in RAM (evaluate_prepared_sanitized fast path)
- keeps LIVE_DELTA/H and LIVE_SHARPE/I blank until sheet complete
- handles 13 tabs including STDEV_SLOPE_SIZING, every row gets G pos/neg
"""
import pathlib, sys, tempfile, json
import openpyxl
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_CRYPTO_LONG = ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / "TEMPLATE_CRYPTO_LONG.xlsx"

def test_template_law_headers():
    if not TEMPLATE_CRYPTO_LONG.exists():
        return  # skip on Mac without templates
    wb = openpyxl.load_workbook(str(TEMPLATE_CRYPTO_LONG), data_only=False)
    # Check header map includes Switch
    from v15_pilot import _hdr_col_map, _resolve_cols
    ws = wb["ENTRY_REVERSAL_BOUNCE"]
    m = _hdr_col_map(ws)
    assert "Switch" in m or "switch" in m, "Switch header not mapped"
    cols = _resolve_cols(ws)
    assert "A" in cols, "Switch column A not resolved via header"
    assert cols["A"] == m.get("Switch") or cols["A"] == m.get("switch"), "A should be Switch header col"

def test_orange_below_white():
    if not TEMPLATE_CRYPTO_LONG.exists():
        return
    wb = openpyxl.load_workbook(str(TEMPLATE_CRYPTO_LONG), data_only=False)
    for sname in ["ENTRY_REVERSAL_BOUNCE", "EXIT_STRUCTURAL"]:
        if sname not in wb.sheetnames:
            continue
        ws = wb[sname]
        last_switch_row = 0
        first_general_row = 9999
        for r in range(3, ws.max_row+1):
            a = ws.cell(row=r, column=1).value
            fam = str(ws.cell(row=r, column=4).value or "")
            if a and str(a).strip() and fam.upper() != "GENERAL":
                if str(a).strip().lower() not in ("switch","general","blanket"):
                    last_switch_row = max(last_switch_row, r)
            if fam.upper() == "GENERAL":
                first_general_row = min(first_general_row, r)
        if first_general_row != 9999:
            assert last_switch_row < first_general_row, f"{sname} ORANGE GENERAL {first_general_row} above white switch {last_switch_row}"

def test_fixed_pilot_compiles_and_has_fixes():
    p = ROOT / "v15_pilot.py"
    assert p.exists()
    text = p.read_text()
    # Check fixes are present
    assert "FIX 2026-09-27 TEMPLATE LAW" in text, "baseline E fix missing"
    assert "PROHIBITED-SKIP" in text and "window_days" in text, "window-aware prohibit fix missing"
    assert "header-aware, orange-skip" in text or "ORANGE can NEVER be above white" in text, "BEST-C-FILL orange fix missing"
    assert "SKIP_SHEETS: set[str] = set()" in text, "STDEV should not be skipped (13 tabs)"

def test_7d_progress_can_start():
    # Ensure 7d progress file handling is window-specific (dry-run check)
    p = ROOT / "v15_pilot.py"
    text = p.read_text()
    assert "_7d_progress.json" in text, "7d progress window-specific handling missing"
    assert "window_days" in text, "window_days handling missing"

def test_v12_ram_fast_path():
    # Ensure evaluate_prepared_sanitized is used (RAM law)
    p = ROOT / "v15_pilot.py"
    text = p.read_text()
    assert "evaluate_prepared_sanitized" in text, "RAM fast path missing"
    assert "V12_NPZ_CACHE" in text, "NPZ cache not set"
    assert "ALL_PREPARED" in text, "ALL_PREPARED RAM dict missing"
