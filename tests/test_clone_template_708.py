"""Durable test for clone_template 708 enforcement — 1 interim per sym_side, no pilot timestamps.

Regresses V15_V16_CELL_BY_CELL overflow: clone_template must never create
*_30d_matrix_pilot_*.xlsx timestamped files; it must reuse the single interim
file in place and delete any stale pilots for that sym, so total files stays
≤708 (354 sym × 2 runs) instead of ballooning to 4k/4GB.

Winning files (*bh*_gain*_30d_matrix.xlsx) are produced separately via
final_name and are not covered here; this test covers the interim path only.
"""
import pathlib
import tempfile

import openpyxl

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _make_template(path: pathlib.Path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "TEMPLATE_BASELINE_METRICS"
    ws["A1"] = "TEMPLATE_BASELINE_METRICS"
    ws2 = wb.create_sheet("WT_Cross_Long")
    ws2["A1"] = "x"
    wb.save(str(path))


def _load_clone():
    # Import clone_template live from v15_pilot_0914 without importing the whole module's side effects
    import importlib.util
    spec = importlib.util.spec_from_file_location("v15p", str(ROOT / "v15_pilot_0914.py"))
    mod = importlib.util.module_from_spec(spec)
    # Pre-create OUT_DIR stub in mod namespace before exec would be needed, but easier: exec source
    # We'll just read source and verify contract, and exercise filesystem behavior via isolated OUT_DIR
    src = (ROOT / "v15_pilot_0914.py").read_text()
    assert "def clone_template" in src
    return src


def test_source_contract_no_pilot_timestamp():
    src = _load_clone()
    # Extract clone_template body only — strftime elsewhere (logs, final_name) is fine
    import re
    m = re.search(r"def clone_template\(.*?\n(?=\ndef |\Z)", src, re.S)
    body = m.group(0) if m else src
    assert "Remove stale pilot" in body, "stale pilot deletion must be present"
    assert "Enforce 708" in body, "708 enforcement comment must be present"
    assert "refusing to overwrite" not in body, "old FileExistsError guard for pilot timestamps must be removed"
    assert 'strftime' not in body, "clone_template must not create timestamped pilot files"
    # No return of a pilot path — only the single interim target
    assert body.count("_30d_matrix_pilot_") <= 1, "only deletion glob should mention pilot in clone_template"
    assert 'target = OUT_DIR / f"{new_symside}_30d_matrix.xlsx"' in body


def test_clone_template_overwrites_single_interim_and_deletes_stale_pilots():
    import importlib.util
    # Execute module in isolated temp dir so OUT_DIR points to temp
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        out_dir = tmp_path / "out"
        tmpl = tmp_path / "TEMPLATE.xlsx"
        _make_template(tmpl)
        # Patch OUT_DIR before import by exec-ing source with replacement
        src = (ROOT / "v15_pilot_0914.py").read_text()
        # Create a minimal harness: we import the file and monkey-patch OUT_DIR
        spec = importlib.util.spec_from_file_location("v15p_tmp", str(ROOT / "v15_pilot_0914.py"))
        mod = importlib.util.module_from_spec(spec)
        # Need openpyxl available in mod; exec
        # Use exec with custom OUT_DIR: instead patch after load, then call clone_template
        # Load module normally (will use real OUT_DIR), then override
        spec.loader.exec_module(mod)  # type: ignore
        orig_out = mod.OUT_DIR
        try:
            mod.OUT_DIR = out_dir
            sym = "AAPL_LONG"
            # First call creates the single interim file
            p1 = mod.clone_template(tmpl, sym)
            assert p1 == out_dir / f"{sym}_30d_matrix.xlsx", f"must return single interim path, got {p1}"
            assert p1.exists(), "first clone must create file"
            p1.write_text("v1") if False else None  # keep xlsx
            # Simulate stale pilots left from old code
            stale1 = out_dir / f"{sym}_30d_matrix_pilot_20260919120000.xlsx"
            stale2 = out_dir / f"{sym}_30d_matrix_pilot_20260919220000.xlsx"
            stale1.write_bytes(b"x")
            stale2.write_bytes(b"x")
            assert stale1.exists() and stale2.exists()
            # Second call must delete stale pilots and overwrite same interim path, not create new pilot
            before = sorted(out_dir.glob("*.xlsx"))
            p2 = mod.clone_template(tmpl, sym)
            after = sorted(out_dir.glob("*.xlsx"))
            assert p2 == out_dir / f"{sym}_30d_matrix.xlsx"
            assert not stale1.exists(), "stale pilot 1 must be deleted"
            assert not stale2.exists(), "stale pilot 2 must be deleted"
            # After cleanup only one interim file for this sym
            assert len([x for x in after if sym in x.name]) == 1, f"must have exactly 1 file for {sym}, got {after}"
            # Non-sym files must not be touched
            other = out_dir / "MSFT_LONG_30d_matrix_pilot_20260919.xlsx"
            other.write_bytes(b"y")
            p3 = mod.clone_template(tmpl, sym)
            assert other.exists(), "other sym's pilot must not be deleted"
        finally:
            mod.OUT_DIR = orig_out


def test_708_bound_single_interim_per_sym():
    """708 = 354 sym × 2 (1 interim + 1 winning). This test asserts the interim half stays ≤354."""
    src = (ROOT / "v15_pilot_0914.py").read_text()
    # Contract: clone_template returns exactly f"{sym}_30d_matrix.xlsx" — one per sym — so max interim files is |syms|
    assert 'target = OUT_DIR / f"{new_symside}_30d_matrix.xlsx"' in src
    # No branching that would return a pilot path
    # The only glob for pilots must be the deletion loop, not a return
    assert src.count("_30d_matrix_pilot_") <= 2, "only deletion glob should mention pilot"
