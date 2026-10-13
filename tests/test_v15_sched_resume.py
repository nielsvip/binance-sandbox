"""2026-10-13 ASYNC JIT DISPATCH + RESUME-FROM-CELL (BACKTEST_BIBLE §93) — scheduler/dispatch/freeze unit tests."""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

import v15_fleet_scheduler as S  # noqa: E402
import v15_npz_freeze as F  # noqa: E402
import v15_unfinished_inventory as INV  # noqa: E402


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_dispatch_q_mark_prunes_old_and_marks_new():
    st = {"dispatch_q": {"OLD_LONG|30D": 0.0, "KEEP_LONG|30D": 9000.0}}
    S._dispatch_q_mark(st, ["NEW_SHORT|30D"], 10000.0, grace_s=2400)
    assert "OLD_LONG|30D" not in st["dispatch_q"]
    assert st["dispatch_q"]["KEEP_LONG|30D"] == 9000.0
    assert st["dispatch_q"]["NEW_SHORT|30D"] == 10000.0


def test_dispatching_syms_holds_slot_inside_grace_only():
    st = {"dispatch_q": {"AAA_LONG|30D": 1000.0, "BBB_SHORT|30D": -5000.0}, "dispatch_host": {"AAA": "s5", "BBB": "s6", "CCC": "s2"}}
    out = S._dispatching_syms(st, 1500.0, grace_s=2400)
    assert out == {"AAA": "s5"}
    assert "CCC" not in st["dispatch_host"]  # host map entry without a queue key is dropped


def test_prune_finished_dispatches_releases_exited_jobs():
    st = {"dispatch_q": {"AAA_LONG|30D": 0.0, "BBB_LONG|30D": 0.0, "CCC_LONG|30D": 990.0}, "dispatch_host": {"AAA": "s1", "BBB": "s2", "CCC": "s5"}}
    gone = S._prune_finished_dispatches(st, 1000.0, live={"BBB"}, min_age_s=180)
    assert gone == ["AAA_LONG|30D"]  # BBB job alive, CCC too young to judge
    assert set(st["dispatch_q"]) == {"BBB_LONG|30D", "CCC_LONG|30D"}
    assert "AAA" not in st["dispatch_host"]
    assert S._prune_finished_dispatches(st, 1000.0, live=None) == []  # ps failure never releases


def test_parity_gate_preflight_cached_blocking_and_fail_open(monkeypatch):
    calls = []

    def gate(cs, template_path=None):
        calls.append(cs)
        return {"ok": cs.startswith("STOCKS"), "hard": ["X: bold=1 global=2"]}

    cache = {}
    assert S._parity_gate_ok("crypto", "LONG", "T", cache, gate=gate) is False
    assert S._parity_gate_ok("crypto", "LONG", "T", cache, gate=gate) is False
    assert S._parity_gate_ok("stocks", "SHORT", "T", cache, gate=gate) is True
    assert calls == ["CRYPTO_LONG", "STOCKS_SHORT"]  # once per cat_side per tick

    def boom(cs, template_path=None):
        raise RuntimeError("x")

    assert S._parity_gate_ok("crypto", "SHORT", "T", {}, gate=boom) is True  # gate crash = allow (pilot semantics)
    monkeypatch.setenv("V15_SCHED_PARITY_PREFLIGHT", "0")
    assert S._parity_gate_ok("crypto", "LONG", "T", {}, gate=gate) is True


def test_launch_exhausted_pinned_uses_pin_cap():
    st = {"attempts": {"P_LONG|30D": 3}, "slices": {"P_LONG|30D": 10}}
    assert S._launch_exhausted("P_LONG|30D", "30D", "P_LONG", st, set(), set()) is True  # attempts cap 3 (fresh)
    assert S._launch_exhausted("P_LONG|30D", "30D", "P_LONG", st, set(), set(), pinned={"P_LONG"}) is False
    st["slices"]["P_LONG|30D"] = S._PIN_SLICE_CAP
    assert S._launch_exhausted("P_LONG|30D", "30D", "P_LONG", st, set(), set(), pinned={"P_LONG"}) is True


def test_slice_counter_resets_only_when_progress_advanced():
    st = {"slices": {"K|30D": 15}}
    S._slice_progress_reset(st, "K|30D", 100.0)  # first sighting: remember, no reset
    assert st["slices"]["K|30D"] == 15
    S._slice_progress_reset(st, "K|30D", 100.0)  # no progress since last launch (crash loop / refusal)
    assert st["slices"]["K|30D"] == 15
    S._slice_progress_reset(st, "K|30D", 250.0)  # board wrote rows -> budget restarts
    assert st["slices"]["K|30D"] == 0
    S._slice_progress_reset(st, "K|30D", None)
    assert st["slice_mt"]["K|30D"] == 250.0


def test_place_order_pinned_then_unfinished_then_fresh():
    seq = S._place_order([], [], ["FRESH", "CONT", "PIN", "REDO"], {}, redo={"REDO_LONG"}, attempts={}, launched_at={},
                         cont={"CONT_SHORT"}, pinned={"PIN_LONG"})
    order = [s for s, _ in seq]
    assert order[0] == "PIN"
    assert set(order[1:3]) == {"CONT", "REDO"}
    assert order[3] == "FRESH"


def test_drain_and_pin_files(tmp_path, monkeypatch):
    d = tmp_path / "drain.json"
    d.write_text(json.dumps({"hosts": ["s5"]}))
    monkeypatch.setenv("V15_SCHED_DRAIN_HOSTS", "s6, ")
    assert S._load_drain(d) == {"s5", "s6"}
    monkeypatch.delenv("V15_SCHED_DRAIN_HOSTS")
    assert S._load_drain(tmp_path / "missing.json") == set()
    p = tmp_path / "pin.json"
    p.write_text(json.dumps({"sides": ["AAA_LONG"], "hosts": []}))
    assert S._load_pinned(p) == ({"AAA_LONG"}, set())
    assert S._load_pinned(tmp_path / "none.json") == (set(), set())


def test_npz_freeze_registry_roundtrip(tmp_path, monkeypatch):
    fz = F.build(started={"AAA_LONG", "BBB_SHORT", "CCC_LONG"}, done={"BBB_SHORT"}, redo={"DDD_LONG"}, running={"EEE_SHORT"})
    assert set(fz) == {"AAA", "CCC", "DDD", "EEE"}
    p = tmp_path / "frz.json"
    F.write(fz, p, now=1.0)
    assert F.frozen_syms(p) == {"AAA", "CCC", "DDD", "EEE"}
    assert F.is_frozen("aaa", p) is True and F.is_frozen("BBB", p) is False
    monkeypatch.setenv("V15_NPZ_FREEZE", "0")
    assert F.frozen_syms(p) == set()
    monkeypatch.delenv("V15_NPZ_FREEZE")
    assert F.frozen_syms(tmp_path / "missing.json") == set()


def test_unfinished_classify_newest_board_wins():
    recs = [
        {"ss": "GALA_LONG", "mtime": 100, "rows": 3280, "final": True, "redo": False, "host": "s5", "dir": "a"},
        {"ss": "GALA_LONG", "mtime": 200, "rows": 289, "final": False, "redo": False, "host": "s1", "dir": "b"},
        {"ss": "DONE_SHORT", "mtime": 300, "rows": 3000, "final": True, "redo": False, "host": "s2", "dir": "b"},
        {"ss": "REDO_LONG", "mtime": 300, "rows": 3000, "final": True, "redo": True, "host": "s2", "dir": "b"},
        {"ss": "EMPTY_LONG", "mtime": 300, "rows": 0, "final": False, "redo": False, "host": "s2", "dir": "b"},
        {"ss": "OUT_LONG", "mtime": 300, "rows": 5, "final": False, "redo": False, "host": "s2", "dir": "b"},
    ]
    newest, unf = INV.classify(recs, allowed={"GALA_LONG", "DONE_SHORT", "REDO_LONG", "EMPTY_LONG"})
    assert set(unf) == {"GALA_LONG", "REDO_LONG"}
    assert unf["GALA_LONG"]["host"] == "s1"
    assert "OUT_LONG" not in newest


def test_dispatch_job_dedup_and_slots(tmp_path, monkeypatch):
    J = _load("v15_dispatch_job_t", "tools/v15_dispatch_job.py")
    g1 = J._dedup_lock("pytest:DEDUP:L30D")
    assert g1 is not None
    assert J._dedup_lock("pytest:DEDUP:L30D") is None  # second job for the same tag exits
    g1.close()
    s1 = J._acquire_slot(1, 0)
    assert s1 is not None
    assert J._acquire_slot(1, 0) is None  # all build slots busy -> wait times out
    s1.close()
