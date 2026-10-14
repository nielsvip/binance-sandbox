"""USER 2026-10-14 HARD LAW: a published final below the best start is PHYSICALLY IMPOSSIBLE. KPI guard found 3 s2 boards
(ONEUSDT_SHORT 22.55->0.62 diag status_up on a pre-floor build; JASMYUSDT_LONG 3.98->3.74 + FILUSDC_SHORT 3.42->3.09: the
BEST-SEEN gate required best_seen.npz == current npz, but the START note is written before _run_npz_short exists -> npz '' ->
gate skipped). Pins: gate has no npz-string precondition and no chain-value precondition; START note keeps the start set;
hard [BEST-START-FLOOR] at the authoritative fresh final."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / "v15_pilot.py").read_text()


def test_gate_has_no_npz_or_chain_precondition():
    i = SRC.index("BEST-SEEN PUBLISH GATE")
    gate = SRC[i:i + 4000]
    assert '_bsx.get("npz") == _bs_npz_now' not in gate
    assert 'float(_bsx.get("gain") or -1e18) > float(cumulative_gain)' not in gate
    assert "if _cur_ov != _bs_ov:" in gate


def test_start_note_keeps_start_set():
    assert 'progress["best_seen"]["start_overrides"] = sanitize_overrides(dict(cumulative_overrides), defaults)[0]' in SRC


def test_hard_floor_at_fresh_final_after_fresh_eval_before_qualification():
    i_fresh = SRC.index('progress["final_gain_fresh_vec"] = float(_fresh_g)')
    i_floor = SRC.index("[BEST-START-FLOOR] {new_symside} REFUSED")
    i_qual = SRC.index("_qual_ok, _qual_reasons = _qualifies_30d(_fresh_final, bh)")
    assert i_fresh < i_floor < i_qual
    blk = SRC[i_floor - 2500:i_floor + 1500]
    assert '("best_start", _fl_bs.get("start_overrides"))' in blk and "_eps_fl(prepared" in blk
    assert 'progress["best_start_floor"]' in blk and 'os.environ.get("V15_BEST_START_FLOOR", "1")' in blk
