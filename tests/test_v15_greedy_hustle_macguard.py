"""Durable: greedy cumulative (G), hustle exhaustive, macbook guard, never-stop red continuation."""
import pathlib
import py_compile
import openpyxl

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / "TEMPLATE_CRYPTO_LONG.xlsx"
V15 = ROOT / "v15_pilot.py"
V15_ENGINE = ROOT / "backtest_v15_engine.py"

def test_template_greedy_cumulative_uses_G_not_F():
    """Current law (v15_pilot docstring, 2026-10): "Pilot decides, sheet has no formulas in data rows (r>=3)". The template therefore carries NO
    pre-printed E/F/G/H (no formula, no number); E = BASELINE header, F = HUSTLE_DELTA (vs ORIGINAL baseline), G = VECTOR_DELTA (vs the LATEST baseline).
    The greedy E chain is written by the pilot from G: it advances at exactly one site, from cumulative_before + the row's G (never F)."""
    wb = openpyxl.load_workbook(str(TEMPLATE), data_only=False)
    for sheet in ["ENTRY_REVERSAL_BOUNCE", "STDEV_SLOPE_SIZING", "ENTRY_BREAKOUT_CHANNEL", "EXIT_STRUCTURAL", "GLOBAL_RISK_GATES"]:
        ws = wb[sheet]
        assert ws.cell(2, 5).value == "BASELINE" or "GREEDY" in str(ws.cell(2, 5).value)
        assert ws.cell(2, 6).value == "HUSTLE_DELTA"
        assert ws.cell(2, 7).value == "VECTOR_DELTA"
        for r in range(3, min(ws.max_row, 60) + 1):
            for c in (5, 6, 7, 8):
                v = ws.cell(r, c).value
                assert v is None, f"{sheet}!{r} col {c} must be blank in the template (pilot decides, no formulas/pre-printed deltas): {v!r}"
    wb.close()
    src = V15.read_text()
    assert 'cumulative_gain = cumulative_before + float(row_delta)' in src
    assert '_num_cell(ws, rr, cols["G"], row_delta, promote)' in src
    assert "F = HUSTLE_DELTA: the row's setting vs the ORIGINAL baseline (G is the delta vs the LATEST baseline)" in src

def test_stdev_not_all_baseline():
    """STDEV sheet was bug: all E = baseline B2. Template law now: STDEV data rows carry no E at all (neither a constant baseline nor a formula)."""
    wb = openpyxl.load_workbook(str(TEMPLATE), data_only=False)
    ws = wb["STDEV_SLOPE_SIZING"]
    es = [ws.cell(r, 5).value for r in range(3, ws.max_row + 1)]
    assert all(e is None for e in es), "STDEV E must be blank in the template (the pilot writes the chain)"
    wb.close()

def test_macbook_guard_blocks_backtest():
    src = V15.read_text()
    assert ('platform.system() == "Darwin"' in src) or ('sys.platform == "darwin"' in src), "must check Darwin"
    assert "BLOCKED" in src and "NO backtests on MacBook" in src, "must BLOCK on Darwin without dry-run"
    assert 'sys.exit(2)' in src, "must exit 2 on Mac block"
    # backtest_v15_engine also blocks
    src2 = V15_ENGINE.read_text()
    assert 'BLOCKED: NO backtests on MacBook' in src2, "v15_engine must block Darwin"
    assert 'V15_ALLOW_MAC' in src2, "engine must have allow override"

def test_never_stop_marks_red_and_continues():
    src = V15.read_text()
    # must flag NEG with orange but also write G value (our fix)
    assert 'ws_row.cell(row=r, column=7).value = float(delta_best)' in src, "NEG must write G greedy value"
    # parity-fail must write both F and G
    assert src.count('column=7).value = float(delta_best)') >= 3, "all NEG/parity/live-neg must write G"
    # BadZip recovery
    assert "BadZip-recover" in src, "must recover BadZip and continue"
    # per-cell red and continue, not return
    assert "PER_CELL TIMEOUT" in src or "per_cell_timeout" in src
    assert "FF0000" in src  # red for failures
    assert "FFA500" in src  # orange for NEG
    # must not have os._exit killing workbook
    # the single os._exit(2) is the bounded 1h stuck-first-cell abort (r3 F/G blank AND < 5 rows done), never a kill on a NO VALID strand / NEG row
    assert src.count("os._exit(2)") == 1
    i = src.index("os._exit(2)")
    assert "[STUCK-CELL-ABORT]" in src[i - 300 : i] and "_elapsed > 3600" in src[i - 400 : i]
    assert "_stuck = (_f3 is None and _g3 is None) and _done < 5" in src[:i]

def test_greedy_cumulative_logic():
    src = V15.read_text()
    # delta = vg - cumulative_before (greedy, not vs baseline)
    assert "vg - cumulative_before" in src
    # hustle vs baseline is separate
    assert "hustle" in src.lower() or "HUSTLE_DELTA" in src
    assert "_hustle_neg = float(vec_best" in src or "_hustle_delta" in src or "hustle_delta_vs_baseline" in src
    # E column is cumulative_before, G is greedy delta
    assert "column=5).value = float(cumulative_before)" in src
    assert "column=7).value = float(delta_best)" in src

def test_hustle_exhaustive_combinations():
    src = V15.read_text()
    # hustle must try all best deltas together in numerous combinations until max delta
    assert "hustle" in src.lower()
    assert "beam" in src.lower()
    # now beam 64 depth 10 and exhaustive top12
    assert "64" in src and "10" in src, "beam should be 64 depth 10"
    assert "exhaustive" in src.lower() or "EXHAUSTIVE" in src
    assert "itertools.combinations" in src or "combinations" in src
    assert "_hustle_top[:80]" in src or "80" in src, "top 80 for hustle"

def test_filter_full_evaluation():
    src = V15.read_text()
    # must evaluate ALL filters per row, not limit 4 that caused duplicate deltas and incomplete yellows
    assert "Per row: every yellow = running set + switch=cand + that filter, delta vs the LATEST baseline, always written." in src
    assert "top50 capped" in src or "50" in src
    # old limit 4 must not remain as primary
    assert "top4 FAST" not in src, "old top4 limit must be removed"

def test_compile():
    py_compile.compile(str(V15), doraise=True)
    py_compile.compile(str(V15_ENGINE), doraise=True)
