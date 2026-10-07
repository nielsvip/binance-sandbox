import pathlib, shutil, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import openpyxl
from tools.opt.v14_sequential_filler import ensure_lbI_headers, clone_template
from tools.opt.v14_sequential_filler_v15 import ensure_lbI_headers as ensure_v15
from tools.opt.v14_sequential_filler_v15_parallel import ensure_lbI_headers as ensure_v15p

def test_lbI_wt_at_12():
    src = ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / "TEMPLATE_CRYPTO_LONG.xlsx"
    for idx, fn in enumerate((ensure_lbI_headers, ensure_v15, ensure_v15p)):
        tmp = pathlib.Path(f"/tmp/test_empty_matrix_lbI_{idx}.xlsx")
        shutil.copy(src, tmp)
        fn(tmp)
        wb = openpyxl.load_workbook(str(tmp), data_only=False)
        ws = wb["ENTRY_REVERSAL_BOUNCE"]
        headers = [ws.cell(2, c).value for c in range(12, 22)]
        assert any("WT_15M" in str(h) for h in headers), f"WT_15M not in L:BI 12-22: {headers}"
        assert headers[0].startswith("WT_15M"), f"WT should be first at 12, got {headers[0]}"
        wb.close()
        tmp.unlink(missing_ok=True)

def test_clone_cleans_c_and_results():
    src = ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / "TEMPLATE_CRYPTO_LONG.xlsx"
    # TEMPLATE has C3 with " + " corruption in SNDK case, ensure clone clears
    tmp = clone_template(src, "TEST_EMPTY_CLEAN")
    # clone alone keeps stale ATR at L12; ensure_lbI_headers rebuilds to WT
    ensure_lbI_headers(tmp)
    try:
        wb = openpyxl.load_workbook(str(tmp), data_only=False)
        ws = wb["ENTRY_REVERSAL_BOUNCE"]
        # C3 should be cleared if it contained " + "
        for r in range(3, 6):
            c = ws.cell(r, 3).value
            assert not (isinstance(c, str) and " + " in c), f"C{r} not cleared: {c}"
        # headers should be WT first after ensure
        assert "WT_15M" in str(ws.cell(2, 12).value), f"L12 not WT: {ws.cell(2,12).value}"
        # Results sheet should exist and be empty (max_row 1)
        assert "Results_Deltas" in wb.sheetnames
        ws2 = wb["Results_Deltas"]
        assert ws2.max_row == 1, f"Results not cleared max_row {ws2.max_row}"
        wb.close()
    finally:
        tmp.unlink(missing_ok=True)

def test_skip_empty_baseline_avoids_waste():
    # Early gate: baseline invalid/0 trades should skip clone entirely (no empty pilot vomit)
    # Verify the filler code contains the early gate string
    import pathlib as _p
    for filler in ["v14_sequential_filler.py", "v14_sequential_filler_v15.py", "v14_sequential_filler_v15_parallel.py"]:
        p = ROOT / "tools" / "opt" / filler
        txt = _p.Path(p).read_text()
        assert "skip-empty-baseline" in txt, f"{filler} missing early skip gate"
        assert "baseline — early gate BEFORE clone" in txt, f"{filler} should gate before clone to avoid waste"
        # should not create empty pilot: check that clone is after baseline check
        clone_idx = txt.index("clone — only if baseline valid")
        baseline_idx = txt.index("baseline — early gate BEFORE clone")
        assert baseline_idx < clone_idx, f"{filler} baseline must be before clone"
