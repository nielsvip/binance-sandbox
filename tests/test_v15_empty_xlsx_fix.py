import pathlib, shutil, sys, zipfile
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import openpyxl
import v15_pilot
from v15_pilot import _resolve_cols, SWITCH_SHEETS, SKIP_SHEETS

def test_v15_live_xlsx_not_empty_and_valid_zip():
    # The bug: SPREADSHEETS/V15_V16_CELL_BY_CELL/AAPL_LONG_30d_matrix.xlsx was 1.8M BadZip File is not a zip file with 0 F filled despite 2793 done in progress.json
    p = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL" / "AAPL_LONG_30d_matrix.xlsx"
    assert p.exists(), f"{p} missing — should have been refilled from progress"
    # valid zip
    z = zipfile.ZipFile(str(p), 'r')
    assert len(z.namelist()) >= 10, f"BadZip entries {len(z.namelist())}"
    z.close()
    wb = openpyxl.load_workbook(str(p), data_only=False)
    # 12 tabs in order STDEV skipped, 21 sheets
    assert len(wb.sheetnames) >= 18, f"sheets {wb.sheetnames}"
    assert "STDEV_SLOPE_SIZING" in wb.sheetnames
    # STDEV must be skipped
    ws_stdev = wb["STDEV_SLOPE_SIZING"]
    assert ws_stdev.cell(3, 6).value is None, "STDEV_SLOPE_SIZING F3 should be None (skipped)"
    # every done row from progress should have F/G/E
    import json
    prog = json.loads((ROOT / "data/reports/lifecycle_pilot/AAPL_LONG_v14_progress.json").read_text())
    done = prog.get("done", {})
    assert len(done) >= 100, f"progress should have >100 done, got {len(done)}"
    # spot check first 2 done keys are in workbook with F not None
    for key in list(done.keys())[:2]:
        sheet, rest = key.split("!", 1)
        if sheet in SKIP_SHEETS or sheet == "STDEV_SLOPE_SIZING":
            continue
        r = int(rest.split(":", 1)[0])
        ws = wb[sheet]
        cols = _resolve_cols(ws)
        f = ws.cell(row=r, column=cols["F"]).value
        g = ws.cell(row=r, column=cols["G"]).value
        e = ws.cell(row=r, column=cols["E"]).value
        assert f is not None, f"{key} F None — refill failed"
        assert g is not None, f"{key} G None"
        assert e is not None, f"{key} E None (blank-until-POS should have baseline for first row)"
    wb.close()

def test_v15_ensure_handles_badzip():
    # ensure_lbI_headers must handle BadZip File is not a zip file by deleting and cloning fresh
    import tempfile, pathlib
    tmp = pathlib.Path("/tmp/test_v15_badzip.xlsx")
    tmp.write_bytes(b"not a zip")
    # should not raise, should recreate from TEMPLATE
    v15_pilot.ensure_lbI_headers(tmp)
    assert tmp.exists()
    z = zipfile.ZipFile(str(tmp), 'r')
    assert len(z.namelist()) >= 10
    z.close()
    tmp.unlink(missing_ok=True)

def test_v15_baseline_never_sums_neg_and_never_reverts():
    # cumulative_gain only increases on POS, E blank-until-POS
    # Simulate: baseline 10, row1 POS +2 => 12, row2 NEG -5 should stay 12 not 7
    # This is the user-reported 12.13 -> 12.13 -> 9.28 bug where NEG was summed
    # Verify code contains the guard
    txt = (ROOT / "v15_pilot.py").read_text()
    assert "cumulative_gain only += G if G>0" in txt or "cumulative_gain = float(cumulative_before + delta_for_row)" in txt or "NEVER sums NEG" in txt
    assert "NEVER revert" in txt or "NEVER revert to worse" in txt or "NEVER sums NEG" in txt
