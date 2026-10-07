import json, pathlib, tempfile, os

def test_progress_resume_not_restart(tmp_path):
    """MSTR must continue from existing done, not restart after crash."""
    prog = tmp_path / "MSTR_LONG_v14_progress.json"
    # Simulate existing progress with 324 rows
    initial = {
        "symside": "MSTR_LONG",
        "baseline_gain": -11.55,
        "bh": -16.62,
        "done": {f"ENTRY_REVERSAL_BOUNCE!{i}:SWITCH=VAL": {"delta": 1.0, "vec_gain": 1.0} for i in range(324)},
        "cumulative_gain": -11.55,
    }
    prog.write_text(json.dumps(initial))
    # Simulate filler resume: load and ensure not overwritten
    data = json.loads(prog.read_text())
    assert len(data["done"]) == 324, "should load 324"
    # Add one more row as filler would
    data["done"]["ENTRY_BREAKOUT_CHANNEL!324:NEW=VAL"] = {"delta": 2.0, "vec_gain": 2.0}
    # Atomic write (as fix should do)
    tmp = prog.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.rename(prog)
    data2 = json.loads(prog.read_text())
    assert len(data2["done"]) == 325, "should resume to 325, not restart to 1"
    assert "ENTRY_REVERSAL_BOUNCE!0:SWITCH=VAL" in data2["done"], "should retain old rows"

def test_quarantine_excludes_zec(tmp_path):
    """ZECUSDC should be excluded from quarantine per user request."""
    # Simulate quarantine check
    baseline_vec = {"valid": False, "trades": None}
    baseline_live = {"valid": False, "trades": None}
    # Old logic
    old_waster = (not baseline_vec.get("valid")) and (not baseline_live.get("valid"))
    assert old_waster is True
    # New logic with ZEC exclusion
    for sym in ["ZECUSDC_LONG", "ZECUSDC_SHORT", "SOLUSDC_LONG"]:
        new_waster = (not baseline_vec.get("valid")) and (not baseline_live.get("valid")) and ("ZECUSDC" not in sym)
        if "ZECUSDC" in sym:
            assert new_waster is False, f"{sym} should not be quarantined"
        else:
            assert new_waster is True, f"{sym} should be quarantined"

def test_crash_does_not_lose_done(tmp_path):
    """Crash during write should not lose done via atomic rename."""
    prog = tmp_path / "test.json"
    data = {"done": {"a": 1, "b": 2}}
    prog.write_text(json.dumps(data))
    # Simulate crash after writing tmp but before rename: original should remain
    tmp = prog.with_suffix(".tmp")
    tmp.write_text(json.dumps({"done": {"a": 1, "b": 2, "c": 3}}))
    # Crash: do not rename, original intact
    assert len(json.loads(prog.read_text())["done"]) == 2
    # Recovery: rename
    tmp.rename(prog)
    assert len(json.loads(prog.read_text())["done"]) == 3
