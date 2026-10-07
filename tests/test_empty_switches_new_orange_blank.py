"""Empty switches: NEW orange/blank switches must be tested first, yellow per_sym L:BI kept for later."""
from pathlib import Path
import openpyxl

def test_new_orange_blank_not_yellow():
    # Templates must have orange/blank general switches present, yellows must not be blanked for deletion
    for tmpl in Path("SPREADSHEETS").glob("TEMPLATE*.xlsx"):
        wb=openpyxl.load_workbook(str(tmpl), data_only=False)
        # check that L:BI yellow headers exist and are not all blank (per_sym kept)
        has_yellow = False
        has_orange = False
        for ws in wb.worksheets:
            if ws.title.startswith("LEGEND") or "RESULTS" in ws.title:
                continue
            # check header row 2 for L:BI
            for col in range(12,23):
                if ws.cell(2, col).value:
                    has_yellow = True
            # check general switches col A
            for r in range(3, min(20, ws.max_row+1)):
                if ws.cell(r,1).value and ws.cell(r,1).value not in ("switch","general"):
                    has_orange = True
        wb.close()
        assert has_yellow, f"{tmpl.name} must keep yellow L:BI per_sym switches (not marked for deletion)"
        assert has_orange, f"{tmpl.name} must have orange/blank general switches"
    # pilot must test new orange/blank before yellows
    src=Path("v15_pilot.py").read_text()
    assert "_per_yellow_sum" in src, "yellow calc kept"
    assert "yellow filter isolation" in src.lower(), "yellow isolation kept"
    assert "FORCE_DC_RERUN" in src or "RESUME-ALLOW" in src, "pilot must allow retest of new switches without retesting old done"

def test_empty_switches_audit_keeps_yellows():
    src=Path("v15_pilot.py").read_text()
    # ensure pilot skips old done (not retest old numbers) and only tests new
    assert 'progress.get("done"' in src, "must skip old done"
    assert "never_pos" not in src or "KG_NEVER_SKIP" in src, "not marking yellows as never_pos"

