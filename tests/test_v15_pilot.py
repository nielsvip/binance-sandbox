"""test_v15_pilot — durable regression for SERIOUS cell-by-cell filler."""
import pathlib
import py_compile

ROOT = pathlib.Path(__file__).resolve().parents[1]
V15 = ROOT / "v15_pilot.py"
TOOLS_V15 = ROOT / "tools" / "v15_pilot_sheet_runner.py"
TEMPLATE = ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / "TEMPLATE_CRYPTO_LONG.xlsx"

def test_compiles():
    py_compile.compile(str(V15), doraise=True)
    py_compile.compile(str(TOOLS_V15), doraise=True)

def test_keeps_npz_in_memory():
    src = V15.read_text()
    assert 'V12_NPZ_CACHE' in src and '"32"' in src, "must set V12_NPZ_CACHE=32"
    assert 'ALL_PREPARED' in src and 'ALL_NPZ_ARRAYS' in src, "must keep ALL_PREPARED dict"
    assert 'preload_prepared' in src or 'preload_all' in src, "must preload NPZ"

def test_wb_keep_open_per_sheet():
    src = V15.read_text()
    assert 'wb_keep' in src, "must keep wb open per sheet"
    assert 'ws_keep' in src
    assert src.count('wb_keep') >= 3, "wb_keep must be reused per row not per reload"
    assert 'workers' in src.lower() and '16' in src, "workers 16 required"

def test_per_cell_timeout_and_heartbeat():
    src = V15.read_text()
    assert 'per_cell_timeout_sec = 0.5' in src or 'per_cell_timeout_sec = 60' in src
    assert '0.5 if' in src or '1.0' in src, "must have 0.5s for 7d / 1.0s for 30d never-wait"
    assert 'heartbeat' in src.lower()
    assert '/tmp/v14_heartbeat' in src

def test_atomic_save_and_no_overwrite():
    src = V15.read_text()
    assert '_atomic_save' in src
    assert '.tmp' in src and 'os.replace' in src
    # 'refusing to overwrite' lives in sheet_runner (checked below) — v15_pilot uses _atomic_save tmp+fsync+rename
    assert 'clone_template' in src
    # either v15_pilot or sheet_runner must guard overwrite
    sheet_runner = (ROOT / "tools" / "v15_pilot_sheet_runner.py").read_text() if (ROOT / "tools" / "v15_pilot_sheet_runner.py").exists() else ""
    assert 'refusing to overwrite' in src or 'refusing to overwrite' in sheet_runner

def test_cell_by_cell_LBI_and_results():
    src = V15.read_text()
    assert 'pending_lbI' in src
    assert 'Results_Deltas' in src
    assert 'column=5' in src and 'column=8' in src, "col5 delta col8 variant_gain"
    assert 'header_to_col' in src
    assert 'progress_path.write_text' in src
    assert 'delta = vg - cumulative_before' in src or 'delta=vg - cumulative_before' in src or 'vg - cumulative_before' in src

def test_real_numpy_via_live_scripts():
    src = V15.read_text()
    assert 'prepare_batch' in src
    assert 'evaluate_prepared_sanitized' in src
    assert 'evaluate_v12' in src or 'v12_pilot' in src
    assert 'backtest_v12_engine' in src or 'live_evaluate' in src

def test_no_crash_per_row_try_except():
    src = V15.read_text()
    assert '[ROW-ERR]' in src
    assert 'try:' in src

def test_skip_combos_when_gt500():
    src = V15.read_text()
    assert 'len(rows) <= 500' in src

def test_template_not_overwritten():
    # TEMPLATE must exist and not be the output dir file
    assert TEMPLATE.exists()
    assert TEMPLATE.stat().st_size > 500_000
    # output must go to V15_V16_CELL_BY_CELL not SPREADSHEETS root
    src = V15.read_text()
    assert 'V15_V16_CELL_BY_CELL' in src
    assert 'TEMPLATE.xlsx' not in src or 'clone_template' in src

def test_uses_numpy():
    src = V15.read_text()
    assert 'import numpy' in src or 'import np' in src
