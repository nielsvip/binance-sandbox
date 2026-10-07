"""Untested-symbol flat-SPS guard: real unit tests (tmp matrices, no repo imports)."""
import untested_flat_sps as u


def test_parse_plain_matrix_name():
    assert u.parse_matrix_sym_side("EDUUSDT_LONG_30d_matrix.xlsx") == ("EDUUSDT", "LONG")
    assert u.parse_matrix_sym_side("NMRUSDT_SHORT_30d_matrix.xlsx") == ("NMRUSDT", "SHORT")


def test_parse_handles_extra_segments():
    assert u.parse_matrix_sym_side("UNIUSDC_LONG_bh57p81_gain32p89_30d_matrix.xlsx") == ("UNIUSDC", "LONG")
    assert u.parse_matrix_sym_side("1000000MOGUSDT_SHORT_bhm9p75_gain6p56_30d_matrix.xlsx") == ("1000000MOGUSDT", "SHORT")


def test_parse_rejects_non_matrix():
    assert u.parse_matrix_sym_side("TEMPLATE_CRYPTO_LONG.xlsx") is None
    assert u.parse_matrix_sym_side("NMRUSDT_LONG_30d_matrix.xlsx.bak") is None
    assert u.parse_matrix_sym_side("notes.txt") is None


def test_is_backtested_tmp_dir(tmp_path):
    d = tmp_path / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
    d.mkdir(parents=True)
    (d / "EDUUSDT_LONG_30d_matrix.xlsx").write_text("x")
    assert u.is_backtested("EDUUSDT", "LONG", root=str(tmp_path)) is True
    assert u.is_backtested("EDUUSDT", "SHORT", root=str(tmp_path)) is False
    assert u.is_backtested("NMRUSDT", "LONG", root=str(tmp_path)) is False


def test_is_backtested_missing_dir():
    assert u.is_backtested("EDUUSDT", "LONG", root="/nonexistent-root-xyz") is False


def test_flat_sps_quantity():
    assert u.flat_sps_quantity(16.0, 0.068) == 16.0 / 0.068
    assert u.flat_sps_quantity(0, 1.0) == 0.0
    assert u.flat_sps_quantity(16.0, 0.0) == 0.0
    assert u.flat_sps_quantity(-5, 1.0) == 0.0


def test_config_default_on():
    import re
    src = open("config.py").read()
    m = re.search(r"UNTESTED_FLAT_SPS_ENABLED:\s*bool\s*=\s*(True|False)", src)
    assert m and m.group(1) == "True"


def test_hook_present_in_ceiling_block():
    src = open("ez_manage.py").read()
    assert "import untested_flat_sps as _ufs" in src
    assert "if not _ufs.is_backtested(symbol, _oc_side):" in src
    assert "[FLAT_SPS_UNTESTED]" in src
