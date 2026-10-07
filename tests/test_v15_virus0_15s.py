"""VIRUS0 15s: HYPE/ALGO/BNB gain0 — FIX 2026-09-23 FULL SHEET LAW disables 15s abort, must continue to publish."""
import pathlib

PILOT = pathlib.Path("v15_pilot.py")

def test_virus0_15s_row_detector_disabled():
    src = PILOT.read_text()
    assert "VIRUS0-15s-ROW-DISABLED" in src, "per-row 15s detector must be DISABLED per FULL SHEET LAW"
    assert "continuing (abort disabled" in src, "row disabled message missing"
    assert "total_pos == 0" in src, "total_pos 0 check still logged"
    assert "_v15_start_time" in src, "start time missing"

def test_virus0_15s_sheet_detector_disabled():
    src = PILOT.read_text()
    assert "VIRUS0-15s-SHEET-DISABLED" in src, "per-sheet 15s detector must be DISABLED"
    assert "continuing (abort disabled" in src, "sheet disabled message missing"

def test_virus0_15s_global_disabled():
    src = PILOT.read_text()
    assert "VIRUS0-15s-DISABLED" in src, "global 15s must be DISABLED"
    assert "continuing to publish" in src, "global disabled must continue to publish"
    assert "if total_pos == 0:" in src, "global total_pos 0 check still present for logging"

def test_no_abort_unlink_on_0_pos():
    src = PILOT.read_text()
    lines = src.splitlines()
    disabled_blocks = [i for i, l in enumerate(lines) if "VIRUS0-15s" in l and "DISABLED" in l and "0 pos after" in l]
    assert len(disabled_blocks) == 3, f"expected 3 disabled detectors, got {len(disabled_blocks)}"
    for idx in disabled_blocks:
        snippet = "\n".join(lines[idx:idx+6])
        assert "wb_path.unlink" not in snippet, f"disabled block at {idx} must not unlink wb"
        # disabled block must not abort via 'return' on its own line (allow 'return' inside nested defs later)
        # check only immediate next non-empty lines after the print (up to 4 lines) for a bare abort return
        following = [l.strip() for l in lines[idx+1:idx+5] if l.strip() and not l.strip().startswith("#")]
        # if the next statement is a bare 'return' (abort), it would be exactly 'return' — we forbid that
        for fl in following:
            assert fl != "return", f"disabled block at {idx} must not have bare abort return"
            assert not fl.startswith("return  #") and fl != "return", "no abort return"

def test_pilot_compiles():
    import py_compile
    py_compile.compile(str(PILOT), doraise=True)

def test_numerical_output_still_written():
    src = PILOT.read_text()
    # must still write yellows and F/G even when delta -1, and publish path
    assert "pending_lbI" in src and "ws_h.cell" in src, "yellows must still be written"
    assert "final_path" in src and 'fmt(' in src, "final publish must remain"
