"""Flush contract for the fill loop.

USER 2026-10-15 ("it is the 11.6 MB progress JSON being rewritten once per row <- change this as we are not crashing
every minute anymore so keep more in memory same for xlsx writes - once every 5min or so max") REPLACES the former
per-row-save rule asserted here. Measured before the change: 11.6 MB rewritten per row = 2.9 MB/s, ~4.6 GB per board,
and ~93 % of a board's CPU was not eval compute.

The new contract, which these tests pin:
  * the fill loop persists through _flush_progress / _flush_workbook, never with a direct per-row write;
  * both are time-throttled (PROGRESS_JSON_EVERY_S / WB_SAVE_EVERY_S, default 300 s);
  * a PROMOTION, a cycle end and a sheet end still force the write, so a sheet never lags a promoted set and a resume
    never loses a promoted override;
  * 1d/7d are script-testing windows only — no branch may send the 30d production workload down a per-row path.
"""
import pathlib
import re

PILOT = pathlib.Path("v15_pilot.py")
# the sequential + cycle fill loop of _main_single, where a per-row write costs the whole board
# 2026-10-20 V18: anchored on the loop's own marker (was the fixed line window 13000-14300, which every insertion above shifts)
FILL_MARKER = "    # 0914 branching: if cycle mode use dedicated round-robin handler else legacy sequential"
FILL_SPAN = 1300


def _text():
    return PILOT.read_text()


def _fill_lines():
    lines = _text().splitlines()
    lo = lines.index(FILL_MARKER)
    return [(i + 1, l) for i, l in enumerate(lines) if lo <= i < lo + FILL_SPAN]


def test_flush_helpers_exist_and_are_throttled():
    t = _text()
    # 2026-10-15 05:1xZ: default held at 60 s until the champion-vs-champion determinism control clears the 300 s
    # arm (clean A/B: 3 fewer promotions, final_gain -0.53 pp). Opt in with V15_PROGRESS_JSON_EVERY_S=300.
    assert 'PROGRESS_JSON_EVERY_S = float(os.environ.get("V15_PROGRESS_JSON_EVERY_S", "60"))' in t, "progress flush interval must stay at the proven 60 s default"
    assert 'WB_SAVE_EVERY_S = float(os.environ.get("V15_WB_SAVE_EVERY_S", "300"))' in t, "workbook flush interval must default to 300 s"
    assert "def _flush_progress(force: bool = False):" in t, "_flush_progress helper missing"
    assert "def _flush_workbook(wb_obj, force: bool = False):" in t, "_flush_workbook helper missing"
    assert '_flush_due(_flush_state["json"], _flush_interval_s("V15_PROGRESS_JSON_EVERY_S", PROGRESS_JSON_EVERY_S), time.time(), force)' in t, "progress flush not routed through the revertible throttle"
    assert '_flush_due(_flush_state["wb"], _flush_interval_s("V15_WB_SAVE_EVERY_S", WB_SAVE_EVERY_S), time.time(), force)' in t, "workbook flush not routed through the revertible throttle"


def test_fill_loop_has_no_per_row_direct_write():
    offenders = [(n, l.strip()) for n, l in _fill_lines() if "_atomic_write_json(progress_path, progress)" in l]
    assert not offenders, f"fill loop must persist via _flush_progress, found direct writes: {offenders}"


def test_promotion_and_cycle_end_still_force():
    t = _text()
    assert "_flush_progress(force=True)  # promotion = milestone, never deferred" in t, "promotion must force the progress write"
    assert "_flush_workbook(wb_keep, force=True)  # promotion = milestone" in t, "promotion must force the workbook save"
    assert re.search(r"_flush_progress\(force=True\)\n\s+except: pass\n\s+print\(f\"\[0914-cycle\] dynamic cycle complete", t), "cycle end must force the progress write"
    assert '_flush_progress(force=bool(delta_best is not None and delta_best > 1e-9))' in t, "a positive delta row must force the write"


def test_no_1d_7d_escape_sends_production_down_a_per_row_path():
    t = _text()
    assert "args.window_days not in (1,7)" not in t, "1d/7d are script-testing windows only: they must not gate the 30d write path"


def test_sheet_final_save_remains_unthrottled():
    t = _text()
    assert re.search(r'print\(f"\[SHEET FLUSH\] \{sheet\} final save", flush=True\)\n\s+_atomic_save\(wb_keep, wb_path\)', t), "sheet end must still save unconditionally"


def test_every_row_yellow():
    t = _text()
    assert "EVERY ROW+EVERY YELLOW" in t, "every row/yellow missing"
    assert "NO-SKIP" in t, "no-skip missing"


def test_zero_stop():
    assert "ZERO-STOP" in _text(), "zero-stop missing"


def test_e2_numeric_write_still_asserted():
    t = _text()
    assert "E2 numeric written" in t, "E2 numeric not found"
    assert "STDEV_SLOPE_SIZING" in t, "E2 sheet not found"
