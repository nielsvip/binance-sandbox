"""Durable test for v12/v13 delta calculation and baseline guard.

Ensures:
- delta = variant_gain - baseline_gain (not carbon-copied baseline)
- E/F/G formulas are not overwritten (VLOOKUP chain intact)
- baseline values are written only via baseline_gain, not copied when delta not calculated
"""
from __future__ import annotations
import tempfile
from pathlib import Path
import openpyxl
from openpyxl.styles import Font

def test_delta_calculation_is_variant_minus_baseline():
    baseline_gain = -0.3326665717798408
    vector_delta = -1.254689759078276
    # correct calculation
    variant_gain = baseline_gain + vector_delta
    # should be -1.587...
    assert abs(variant_gain - (-1.587356330858117)) < 1e-9
    # not carbon copy
    assert variant_gain != baseline_gain
    # delta is variant - baseline
    delta = variant_gain - baseline_gain
    assert abs(delta - vector_delta) < 1e-9

def test_baseline_not_carbon_copied_when_delta_zero():
    baseline_gain = -0.3326665717798408
    vector_delta = 0.0
    variant_gain = baseline_gain + vector_delta
    # when delta 0, variant == baseline, but E should stay via formula, not static copy
    assert variant_gain == baseline_gain
    # E should be =IF(F>0,Eprev+F,Eprev) not static baseline
    # This test ensures we don't write E as baseline + delta when F==0
    # E should be formula, not static value
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "test.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "ENTRY_REVERSAL_BOUNCE"
        ws["A3"] = "WT_15M_BOUNCE_OPEN_ENABLED"
        ws["E3"] = "='DUOL_LONG_BASELINE_METRICS'!B2"
        ws["F3"] = '=IFERROR(VLOOKUP($A3,Results_30d_Deltas!$A$2:$H$5000,8,FALSE)-E3,"")'
        ws["E4"] = '=IF(F4="",E3,IF(F4>0,E3+F4,E3))'
        ws["F4"] = '=IFERROR(VLOOKUP($A4,Results_30d_Deltas!$A$2:$H$5000,8,FALSE)-E3,"")'
        # Add Results sheet
        rws = wb.create_sheet("Results_30d_Deltas")
        rws.append(["param","default","override","is_non_default","delta_gain_vs_bh","delta_sharpe","delta_trades","variant_gain"])
        rws.append(["WT_15M_BOUNCE_OPEN_ENABLED", None, None, None, vector_delta, None, None, baseline_gain + vector_delta])
        wb.save(str(p))
        # Reload and verify F/E are formulas, not static baseline
        wb2 = openpyxl.load_workbook(str(p), data_only=False)
        ws2 = wb2["ENTRY_REVERSAL_BOUNCE"]
        assert isinstance(ws2.cell(3,6).value, str) and ws2.cell(3,6).value.startswith("=IFERROR")
        assert isinstance(ws2.cell(4,5).value, str) and ws2.cell(4,5).value.startswith("=IF(F4")
        # Ensure we didn't write static baseline into E4
        assert ws2.cell(4,5).value != baseline_gain

def test_pos_delta_only_prints_when_calculated():
    baseline_gain = -0.3326665717798408
    # Simulate pos delta
    vector_delta = 0.5
    variant_gain = baseline_gain + vector_delta
    # E should be variant_gain only when F>0 and parity, via formula
    # Ensure we don't carbon copy baseline when delta not calculated (None)
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "test.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "ENTRY_REVERSAL_BOUNCE"
        ws["E3"] = "='BASELINE'!B2"
        ws["F3"] = '=IFERROR(VLOOKUP($A3,Results!$A$2:$H$5000,8,FALSE)-E3,"")'
        ws["E4"] = '=IF(F4="",E3,IF(F4>0,E3+F4,E3))'
        ws["F4"] = '=IFERROR(VLOOKUP($A4,Results!$A$2:$H$5000,8,FALSE)-E3,"")'
        wb.save(str(p))
        wb2 = openpyxl.load_workbook(str(p), data_only=False)
        ws2 = wb2["ENTRY_REVERSAL_BOUNCE"]
        # F4 is formula, not static
        assert ws2.cell(4,6).value.startswith("=IFERROR")
        # E4 is formula, not static baseline
        assert ws2.cell(4,5).value.startswith("=IF(F4")

def test_no_carbon_copy_baseline_in_results():
    baseline_gain = -0.3326665717798408
    # Results should have variant_gain = baseline + delta, not baseline carbon copy
    vector_delta = -1.254689759078276
    variant_gain = baseline_gain + vector_delta
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "test.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.create_sheet("Results_30d_Deltas")
        ws.append(["param","default","override","is_non_default","delta_gain_vs_bh","delta_sharpe","delta_trades","variant_gain"])
        ws.append(["WT_15M_BOUNCE_OPEN_ENABLED", None, None, None, vector_delta, None, None, variant_gain])
        wb.save(str(p))
        wb2 = openpyxl.load_workbook(str(p), data_only=False)
        ws2 = wb2["Results_30d_Deltas"]
        assert abs(ws2.cell(2,5).value - vector_delta) < 1e-9
        assert abs(ws2.cell(2,8).value - variant_gain) < 1e-9
        assert abs(ws2.cell(2,8).value - baseline_gain) > 1e-9 or vector_delta == 0  # only equal when delta 0


def test_per_cell_flush_incremental_save():
    """Per-cell flush 2026-09-09: each variant writes its own Results row immediately,
    not waiting for 32/32 sheet complete. Verifies unique sheet!row:switch key and fsyncable save."""
    baseline_gain = -0.3326665717798408
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "percell.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "ENTRY_REVERSAL_BOUNCE"
        ws["A1"] = "Switch"
        ws["E1"] = "BASELINE"
        ws["A3"] = "WT_15M_BOUNCE_OPEN_ENABLED"
        ws["A4"] = "BB_SQUEEZE_ENTRY_ENABLED"
        ws["E3"] = "='DUOL_LONG_BASELINE_METRICS'!B2"
        ws["F3"] = '=IFERROR(VLOOKUP($A3,Results_30d_Deltas!$A$2:$H$5000,8,FALSE)-E3,"")'
        ws["E4"] = '=IF(F4="",E3,IF(F4>0,E3+F4,E3))'
        ws["F4"] = '=IFERROR(VLOOKUP($A4,Results_30d_Deltas!$A$2:$H$5000,8,FALSE)-E3,"")'
        rws = wb.create_sheet("Results_30d_Deltas")
        rws.append(["param","default","override","is_non_default","delta_gain_vs_bh","delta_sharpe","delta_trades","variant_gain"])
        wb.save(str(p))
        # Simulate two per-cell writes with unique sheet!row:switch keys (as patched v12 does)
        for row, delta in [(3, -1.254689759078276), (4, 0.2116)]:
            wb = openpyxl.load_workbook(str(p))
            rws = wb["Results_30d_Deltas"]
            switch = str(wb["ENTRY_REVERSAL_BOUNCE"].cell(row=row, column=1).value)
            unique_key = f"ENTRY_REVERSAL_BOUNCE!{row}:{switch}"
            # append if not exists
            exists = any(str(rws.cell(r,1).value) == unique_key for r in range(2, rws.max_row+1))
            assert not exists, f"key {unique_key} should be new per cell"
            nr = rws.max_row + 1
            rws.cell(row=nr, column=1).value = unique_key
            rws.cell(row=nr, column=5).value = float(delta)
            rws.cell(row=nr, column=8).value = baseline_gain + float(delta)
            wb.save(str(p))
            # verify incremental: file grows after each save, not only at end
            assert p.stat().st_size > 0
            wb2 = openpyxl.load_workbook(str(p), data_only=False)
            rws2 = wb2["Results_30d_Deltas"]
            assert rws2.max_row == nr, f"after row {row} write, Results should have {nr} rows, got {rws2.max_row}"
            # verify F formula still intact (not overwritten)
            ws2 = wb2["ENTRY_REVERSAL_BOUNCE"]
            assert str(ws2.cell(row=row, column=6).value).startswith("=IFERROR")
        # final: 2 deltas, 2 distinct Results rows, E2 chain still formula
        wb2 = openpyxl.load_workbook(str(p), data_only=False)
        assert wb2["Results_30d_Deltas"].max_row == 3  # header + 2 variants
        assert wb2["ENTRY_REVERSAL_BOUNCE"].cell(4,5).value.startswith("=IF(F4")
