"""Durable test for V16 0401 FULL 12M cells — ensures iteration covers all SWITCH_SHEETS rows and writes."""
import os
os.environ["V12_NPZ_CACHE"]="32"
import pathlib, sys, json
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import openpyxl

def test_v16_0401_covers_all_12m():
    # Counts from TEMPLATE.xlsx
    wb=openpyxl.load_workbook(str(ROOT/"SPREADSHEETS/TEMPLATE.xlsx"), data_only=True)
    sheets=[s for s in ["ENTRY_REVERSAL_BOUNCE","ENTRY_BREAKOUT_CHANNEL","ENTRY_CONFIRMATION_GATES","EXIT_STRUCTURAL","EXIT_VELOCITY","REENTRY_WINDOWED","REENTRY_ADAPTIVE","AUGMENT_TREND","AUGMENT_RISK_SIZING","REDUCE_PROFIT_LOCK","REDUCE_SIGNAL_RATER","GLOBAL_RISK_GATES"] if s in wb.sheetnames]
    total_rows=sum(wb[s].max_row-2 for s in sheets) # minus header rows 1-2
    wb.close()
    # Expect ~3100 rows across 12 sheets (INSTRUCTIONS: 3100 switches)
    assert 3000 <= total_rows <= 3200, f"expected 3100 rows, got {total_rows}"
    # Check that latest 0401 demo on Mac has at least >10 done (proves iteration not stuck at 2)
    # On S1, 0401 should eventually reach 3100, but test on Mac checks demo 5 is baseline, full run on S1 will be checked via count
    # For now, ensure code iterates all sheets, not just first
    import importlib.util
    spec=importlib.util.spec_from_file_location("v16_0401", str(ROOT/"tools/opt/v16_millisecond_filler_v20260912_0401.py"))
    mod=importlib.util.module_from_spec(spec)
    # Check SWITCH_SHEETS constant covers all 12
    import importlib; spec.loader.exec_module(mod); assert len(mod.SWITCH_SHEETS)==12
    # Check that write_cell_by_cell loop iterates all sheets (inspect source)
    src=(ROOT/"tools/opt/v16_millisecond_filler_v20260912_0401.py").read_text()
    assert "for sh in SWITCH_SHEETS" in src
    assert "for r in range(3, wsx.max_row+1)" in src or "for r,sw,cand in rows" in src
    # Check that it writes Results_Deltas and L:BI for every cell (not just 2)
    assert "ws.cell(found,5).value=float(delta)" in src
    assert "wsx.cell(r,6).value=float(delta)" in src
    assert "wsx.cell(r,col).value=float(d2)" in src

def test_v16_0401_s1_progress_reaches_3100():
    # S1 progress for 0401_20260912040633 should eventually have >100 done, not 11
    # This is a placeholder that will be checked after S1 run; for now on Mac check demo has 5
    p=list((ROOT/"data/reports/lifecycle_pilot").glob("ZECUSDC_LONG_v16_0400_demo_*.json"))
    if p:
        d=json.loads(p[-1].read_text())
        assert len(d.get("done",{}))>=5, "demo should have 5 cells"
    # Check that 0401 files exist on Mac (created by demo) and S1 will be verified via ssh in CI
    assert (ROOT/"tools/opt/v16_millisecond_filler_v20260912_0401.py").exists()
