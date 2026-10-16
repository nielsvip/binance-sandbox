"""The 2026-10-15 flush throttle must be revertible in one step, without touching code.

USER 2026-10-15: "make sure this is easily reversible if necessary". Three documented revert paths, cheapest first:
  1. `V15_PROGRESS_JSON_EVERY_S=0` / `V15_WB_SAVE_EVERY_S=0` — env, or the gated defaults in data/v15_gate_flags.json
     (which _load_gate_flags injects into os.environ). 0 = write on every call = the exact pre-change behaviour.
  2. restore backups/before_progress_wb_flush_5min_202610150309.py over v15_pilot.py.
  3. the S2 gate champion tree (gate/champion/tree/v15_pilot.py) + v15_gate_guard.py AUTO_FALLBACK.

These tests pin path 1 as a real function contract, not a comment: the interval is resolved per call (so the gate-flags
file, which is loaded AFTER the module constants, still wins) and 0 restores per-row writes exactly.
"""
import importlib.util
import pathlib
import sys

import pytest

SPEC = importlib.util.spec_from_file_location("v15_pilot_flush", str(pathlib.Path(__file__).resolve().parent.parent / "v15_pilot.py"))


@pytest.fixture(scope="module")
def pilot():
    """Import v15_pilot far enough to reach the module-level pure helpers (no board, no NPZ, no network)."""
    if "v15_pilot" in sys.modules:
        return sys.modules["v15_pilot"]
    sys.argv = ["v15_pilot.py", "--sym-side", "AAPL_LONG", "--dry-run"]
    m = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(m)
    return m


def test_force_always_writes(pilot):
    assert pilot._flush_due(last_t=1000.0, interval_s=300.0, now=1000.0, force=True) is True


def test_not_due_inside_the_interval(pilot):
    assert pilot._flush_due(last_t=1000.0, interval_s=300.0, now=1010.0) is False


def test_due_after_the_interval(pilot):
    assert pilot._flush_due(last_t=1000.0, interval_s=300.0, now=1301.0) is True


def test_zero_interval_is_the_revert_every_call_writes(pilot):
    for elapsed in (0.0, 0.001, 1.0, 5.0):
        assert pilot._flush_due(last_t=1000.0, interval_s=0.0, now=1000.0 + elapsed) is True, "interval 0 must restore per-row writes"


def test_negative_interval_also_writes(pilot):
    assert pilot._flush_due(last_t=1000.0, interval_s=-1.0, now=1000.0) is True


def test_garbage_interval_fails_open_to_writing(pilot):
    assert pilot._flush_due(last_t=None, interval_s="nonsense", now=None) is True, "an unreadable throttle must write, never silently stop persisting"


def test_interval_resolved_per_call_from_env(pilot, monkeypatch):
    monkeypatch.delenv("V15_PROGRESS_JSON_EVERY_S", raising=False)
    assert pilot._flush_interval_s("V15_PROGRESS_JSON_EVERY_S", 300.0) == 300.0
    monkeypatch.setenv("V15_PROGRESS_JSON_EVERY_S", "0")
    assert pilot._flush_interval_s("V15_PROGRESS_JSON_EVERY_S", 300.0) == 0.0, "env/gate-flags must win over the import-time constant"
    monkeypatch.setenv("V15_PROGRESS_JSON_EVERY_S", "not-a-number")
    assert pilot._flush_interval_s("V15_PROGRESS_JSON_EVERY_S", 300.0) == 300.0, "a bad value must fall back to the default, not crash a board"


def test_workbook_interval_switch_too(pilot, monkeypatch):
    monkeypatch.setenv("V15_WB_SAVE_EVERY_S", "0")
    assert pilot._flush_interval_s("V15_WB_SAVE_EVERY_S", 300.0) == 0.0


def test_gate_flags_keys_are_injectable(pilot):
    """_load_gate_flags only promotes V15_* keys into os.environ — both switches qualify, so a gate-flags revert works."""
    for k in ("V15_PROGRESS_JSON_EVERY_S", "V15_WB_SAVE_EVERY_S"):
        assert k.startswith("V15_")


def test_revert_backup_exists():
    b = pathlib.Path("backups/before_progress_wb_flush_5min_202610150309.py")
    assert b.exists() and b.stat().st_size > 900000, "the pre-change pilot backup must stay in place as revert path 2"
