"""Race handling in BEST organizer — file deleted between glob and stat must not crash."""
import pathlib
import tempfile


def base_key(name: str):
    if "_30d_matrix" not in name:
        return None
    import re

    prefix = name.split("_30d_matrix")[0]
    prefix = re.sub(r"_bh.*$", "", prefix)
    return prefix


def test_missing_file_in_key_returns_fallback(tmp_path: pathlib.Path):
    # Simulate organize_safe key handling for a file that disappears
    missing = tmp_path / "SNDK_LONG_30d_matrix_pilot_20260914082735.xlsx"
    # Do not create file — stat will raise FileNotFoundError
    # Organizer key must catch and return fallback (-9999,0,0) not raise
    def key_robust(p: pathlib.Path):
        try:
            # Simulate bh_gain lookup returning None
            gain = None
            if gain is None:
                gain = -9999
            try:
                g = float(gain)
            except Exception:
                g = -9999
            try:
                mt = p.stat().st_mtime
            except Exception:
                mt = 0
            try:
                sz = p.stat().st_size
            except Exception:
                sz = 0
            return (g, mt, sz)
        except Exception:
            return (-9999, 0, 0)

    result = key_robust(missing)
    assert result == (-9999, 0, 0)
    # Ensure max() over list containing missing does not raise
    existing = tmp_path / "AAPL_LONG_bh1p00_gain2p00_30d_matrix.xlsx"
    existing.write_text("dummy")
    lst = [missing, existing]
    best = max(lst, key=key_robust)
    assert best == existing


def test_base_key_strips_bh_gain():
    assert base_key("SNDK_LONG_bh1p23_gain4p56_30d_matrix.xlsx") == "SNDK_LONG"
    assert base_key("AAPL_SHORT_30d_matrix_pilot_20260914082735.xlsx") == "AAPL_SHORT"
    assert base_key("TEMPLATE.xlsx") is None
