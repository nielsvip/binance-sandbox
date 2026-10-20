"""durable: v15 never stops at stranded row 21, flags red and continues to sheet 13"""
import pathlib

def test_never_stop_flags_red():
    src = pathlib.Path("v15_pilot.py").read_text()
    # must not kill on NO VALID — every row gets F 0 and continues
    # the single os._exit(2) is the bounded 1h stuck-first-cell abort (r3 F/G blank AND < 5 rows done), not a kill on a NO VALID strand
    assert src.count("os._exit(2)") == 1, "must not kill on NO VALID strand"
    i = src.index("os._exit(2)")
    assert "[STUCK-CELL-ABORT]" in src[i - 300 : i] and "_elapsed > 3600" in src[i - 400 : i]
    assert "_stuck = (_f3 is None and _g3 is None) and _done < 5" in src[:i]
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
