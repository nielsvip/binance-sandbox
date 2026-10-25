"""Smallest test for tools/vec_close_backfill.py — guardian refusal w/o queue entry gets backfilled, covered ones skipped."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "vec_close_backfill.py"


def _run(qtext, ltext, extra=()):
    tmp = Path(tempfile.mkdtemp())
    q = tmp / "q.jsonl"
    q.write_text(qtext)
    lg = tmp / "live.log"
    lg.write_text(ltext)
    p = subprocess.run([sys.executable, str(TOOL), "--queue", str(q), "--log", str(lg), "--apply", *extra], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout), [json.loads(x) for x in q.read_text().splitlines() if x.strip()]


def test_backfills_missing_refusal():
    rep, rows = _run("", "[GUARDIAN] CRITICAL VEC_DECISION_REFUSED men:AXSUSDT_LONG :: CLOSE PEAK_GIVEBACK_GAIN_EROSION_STOP bar=2026-10-11T13:15:00Z refused by non-safety gate: BLOCKED_SKIPPED_STALE\n")
    assert rep["backfilled"] == 1
    assert rows[-1]["kind"] == "VEC_AUDIT_BACKFILL"
    assert rows[-1]["family"] == "PEAK_GIVEBACK"
    assert rows[-1]["position_key"] == "men:AXSUSDT_LONG"


def test_skips_covered_and_other_families():
    q = json.dumps({"ts": 1791724500, "kind": "VEC_DICTATE_ONLY_BLOCK", "position_key": "men:AXSUSDT_LONG", "action": "CLOSE", "reason": "PEAK_GIVEBACK_GAIN_EROSION_STOP"}) + "\n"
    log = ("[GUARDIAN] CRITICAL VEC_DECISION_REFUSED men:AXSUSDT_LONG :: CLOSE PEAK_GIVEBACK_GAIN_EROSION_STOP bar=2026-10-11T13:15:00Z refused by x: BLOCKED_SKIPPED_STALE\n"
           "[GUARDIAN] CRITICAL VEC_DECISION_REFUSED men:AXSUSDT_LONG :: CLOSE TRAIL_STOP bar=2026-10-11T13:20:00Z refused by x: BLOCKED_SKIPPED_STALE\n")
    rep, rows = _run(q, log)
    assert rep["backfilled"] == 0 and rep["covered"] == 1 and rep["ignored"] == 1
    assert len(rows) == 1


def test_off_bar_window_parsed():
    rep, rows = _run("", "INFO ⏳ [VECTOR_EXEC_REFUSED] men:BLURUSDT_SHORT action=CLOSE reason=FIXED_STOP_SHORT_px0.007 — outside 15m bar window\n")
    assert rep["backfilled"] == 1
    assert rows[-1]["family"] == "FIXED_STOP"
