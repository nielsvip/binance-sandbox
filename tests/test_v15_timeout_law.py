"""TIMEOUT LAW 2026-09-22: per-cell ≤10s red and move on, per-sym ≤60m never >20m never 0 trades."""

import pathlib

PILOT = pathlib.Path("v15_pilot_0914.py")
HERD = pathlib.Path("tools/v15_local_herd.py")

def test_pilot_per_cell_10s_red():
    src = PILOT.read_text()
    assert "per-cell" in src.lower() and "10s" in src, "per-cell 10s missing"
    assert "CELL-TIMEOUT" in src, "CELL-TIMEOUT marker missing"
    assert "COLOR RED AND MOVE ON" in src, "red and move on missing"
    assert "per_cell_deadline = 10.0" in src, "per_cell_deadline 10.0 missing"
    assert 'TimeoutError' in src, "TimeoutError missing"

def test_pilot_per_sym_60m():
    src = PILOT.read_text()
    assert "per-sym" in src.lower() and "60m" in src.lower(), "per-sym 60m missing"
    assert "3600" in src, "3600s alarm missing"
    assert "SIGALRM" in src, "SIGALRM missing"
    assert "never stall" in src.lower(), "never stall missing"

def test_pilot_never_0_trades_fallback():
    src = PILOT.read_text()
    assert "BASELINE FALLBACK" in src, "baseline fallback missing"
    assert "never 0 trades" in src.lower(), "never 0 trades missing"

def test_herd_60m():
    src = HERD.read_text()
    assert "60m" in src, "herd 60m missing"
    assert "SIGALRM" in src, "herd SIGALRM missing"

def test_pilot_compiles():
    import py_compile
    py_compile.compile(str(PILOT), doraise=True)
    py_compile.compile(str(HERD), doraise=True)
