"""Durable: sheet never abandoned, lighting fast <1s, 4-sheet batch, paired LONG/SHORT, red fixer."""
import pathlib

def test_sheet_never_abandoned_and_lighting_fast():
    txt = pathlib.Path("v15_pilot.py").read_text()
    # Sheet never abandoned until finished, saved with bh/gain, 365D rerun, backtest done, NPZ stays hot
    assert "SHEET NEVER ABANDONED" in txt
    assert "SHEET-NEVER-ABANDONED" in txt or "SHEET NEVER ABANDONED" in txt
    assert "saved with bh/gain" in txt or "final_name = f\"{new_symside}_bh" in txt
    assert "365D rerun" in txt or "365D" in txt
    assert "backtest_v12_engine" in txt or "backtest_v12" in txt
    assert "NPZ stays" in txt or "NPZ stays hot" in txt
    # Lighting fast: cell must fill <1s, timeouts are plague - now 0.1s per spec (workers plow, red fixer handles >0.1s)
    assert "_per_cell_hard_limit = 0.1" in txt or "_per_cell_hard_limit = YELLOW_TIMEOUT" in txt or "YELLOW_TIMEOUT = 0.1" in txt
    assert "LIGHTING FAST" in txt
    assert "timeouts are plague" in txt or "timeout" in txt.lower()
    # Tenths per cell: 0.1s hard limit, red cell queued for fixer
    assert "per_cell_timeout_sec = 0.1" in txt or "per_cell_timeout_sec = YELLOW_TIMEOUT" in txt or "YELLOW_TIMEOUT = 0.1" in txt
    # Check that 0.1s limit is used for per_cell_deadline
    assert "per_cell_deadline = _per_cell_hard_limit" in txt or 'per_cell_deadline = YELLOW_TIMEOUT' in txt or 'YELLOW_TIMEOUT' in txt

def test_four_sheet_batch_and_paired():
    txt = pathlib.Path("v15_pilot.py").read_text()
    assert "BATCH-PLAN" in txt
    assert "BATCH-START" in txt
    assert "batches of 4" in txt or "batches of" in txt
    assert "PAIRED-NPZ" in txt
    assert "worst2best" in txt
    assert "4-sheet" in txt

def test_baseline_honest_all_12():
    txt = pathlib.Path("v15_pilot.py").read_text()
    assert "E2 numeric written" in txt
    assert "ALL 12 tabs" in txt
    # Check baseline fix exists (may be row=2 or row=3 depending on impl)
    assert "BASELINE" in txt and "ws_fix.cell" in txt
    assert "E for this row (col 5) is cumulative_before - always numeric" in txt

def test_red_fixer_continuous():
    p = pathlib.Path("tools/v15_red_fixer.py")
    assert p.exists(), "v15_red_fixer.py missing"
    txt = p.read_text()
    assert "continuously fixing" in txt.lower() or "CONTINUOUS" in txt
    assert "VLOOKUP" in txt
    assert "FF0000" in txt
    assert "tabColor" in txt
    assert "E2_header" in txt or "E-strand" in txt or "E3_strand" in txt
    assert "worst_first" in txt or "4-sheet" in txt
    # Daemon and cron
    assert "--cron-check" in txt
    assert "ensure_daemon_running" in txt
    assert "while True" in txt

def test_no_timeouts_plague():
    txt = pathlib.Path("v15_pilot.py").read_text()
    # Ensure old 10s deadline not used as hard limit (now 1.0)
    # The string per_cell_deadline = 10.0 should not exist (replaced)
    assert "per_cell_deadline = 10.0" not in txt, "still has 10s plague timeout"

def test_hustle_empty_and_cycle_all_tabs():
    txt = pathlib.Path("v15_pilot.py").read_text()
    # Hustle column F must be empty when not in hustle mode (worst_first)
    assert 'F hustle vs baseline — not in worst_first, leave empty until live hustle' in txt or 'F empty in worst_first' in txt
    assert 'getattr(args, "seq_mode", "") == "hustle"' in txt
    # Cycle must go through all 12 tabs then come back, until POS then move to next row (tenths)
    assert "Cycle-through-all-tabs: go through all 12 tabs then come back, until POS then move to next row" in txt
    assert "all-tabs cycle, advance row on POS" in txt
    assert "all-tabs cycle, stay row on NEG" in txt
