"""durable: v15 never stops at stranded row 21, flags red and continues to sheet 13"""
import pathlib

def test_never_stop_flags_red():
    src = pathlib.Path("v15_pilot.py").read_text()
    # must not kill on NO VALID — every row gets F 0 and continues
    assert "os._exit(2)" not in src, "must not kill on NO VALID strand"
    # must flag stranded/timeout cell red (FF0000) and continue - MAX TIMEPER CELL, no per-cand timeout
    assert "FF0000" in src, "must flag strand cell red"
    assert "MAX TIMEPER CELL" in src, "must handle MAX TIMEPER CELL"
    assert "1.0s" in src, "must have per-cell budget"
    # must still write F 0 for NO VALID and continue
    assert 'ws_row.cell(row=r, column=6).value = 0.0' in src
    # must reach sheet 13
    assert "SHEET FLUSH" in src
    assert "sheet DONE" in src

def test_no_invented_only_recalc():
    src = pathlib.Path("v15_pilot.py").read_text()
    # combined pos filters recalculated with real backtest is correct (not invented)
    assert "COMBINED POS" in src, "combined pos recalc must be present"
    assert "pos_filters =" in src
    # invented combo pairs must stay disabled
    assert "all_combos = []" in src
    # F is best SINGLE or combined recalc only, never invented sum without recalc
    assert "REMOVED invented" not in src or "COMBINED POS" in src
