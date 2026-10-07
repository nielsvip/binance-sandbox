"""Regression for v15_pilot ensure_lbI_headers BadZipFile handling — zipfile import must exist."""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
PILOT = ROOT / "v15_pilot.py"

def test_zipfile_import_exists():
    text = PILOT.read_text()
    assert "import zipfile" in text, "zipfile import missing for ensure_lbI_headers BadZipFile handling"
    assert "zipfile.BadZipFile" in text, "ensure_lbI_headers should catch BadZipFile"

def test_ensure_lbi_headers_handles_badzip():
    import tempfile, openpyxl
    from pathlib import Path
    import v15_pilot
    # create a BadZip file (not a valid xlsx)
    with tempfile.TemporaryDirectory() as td:
        bad = Path(td) / "bad.xlsx"
        bad.write_bytes(b"not a zip")
        # should not raise NameError, should recreate from template or handle gracefully
        try:
            v15_pilot.ensure_lbI_headers(bad)
        except NameError as e:
            assert False, f"NameError zipfile not defined: {e}"
        except Exception:
            # other exceptions (FileNotFound, template missing) are acceptable, but not NameError
            pass
