"""durable: SNDK_LONG must fill only yellow cells, not RANDOM filter cells"""
import pathlib
def test_yellow_only():
    src = pathlib.Path("v15_pilot.py").read_text()
    # heavy must not be random top2
    assert "top2 heavy 40min" not in src, "must not have random top2"
    # must have yellows only limit
    assert "yellows only" in src, "must flag yellows only"
    # must still have header_to_col filtering
    assert "header_to_col" in src
    # must not have invented multi-filter pairs (all_combos disabled)
    assert "all_combos = []" in src
    # must have combined pos recalc (real)
    assert "COMBINED POS" in src
    # must use sequential heavy with MAX TIMEPER CELL
    assert "sequential heavy (no timeout, MAX TIMEPER CELL" in src
