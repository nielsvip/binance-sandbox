"""Scheduler done-detection: TOP-LEVEL final_gain only (nested endgame.final_gain must not mark done)."""

import ast
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))
import v15_fleet_scheduler as sched


def _marks():
    tree = ast.parse(sched.HOST_PY)
    fn = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "_progress_marks"
    )
    ns = {"json": json}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "<host_py>", "exec"), ns)
    return ns["_progress_marks"]


def _write(tmp_path, name, payload):
    p = tmp_path / name
    p.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return str(p)


def test_nested_endgame_gain_not_done(tmp_path):
    m = _marks()
    f = _write(
        tmp_path,
        "a.json",
        {
            "endgame": {"final_gain": 30.16},
            "needs_redo": {"depth": 1},
            "done": {"k": {"delta": 1.0}},
        },
    )
    assert m(f) == (False, False, None)


def test_top_final_gain_done(tmp_path):
    m = _marks()
    assert m(_write(tmp_path, "b.json", {"final_gain": 32.5})) == (True, False, 32.5)
    assert m(_write(tmp_path, "c.json", {"final_gain": -3.2})) == (True, False, -3.2)


def test_needs_redo_never_done(tmp_path):
    m = _marks()
    f = _write(tmp_path, "d.json", {"final_gain": 10.0, "needs_redo": {"depth": 1}})
    assert m(f) == (False, False, 10.0)


def test_oom_victim_spares_refills():
    r = sched._rank_oom_victim(
        [
            ("REFILL_SHORT", 186, 900, True),
            ("FRESH_LONG", 1500, 1800, False),
            ("NEWER_LONG", 40, 300, False),
        ]
    )
    assert r == "NEWER_LONG"
    r2 = sched._rank_oom_victim(
        [("REFILL_A", 10, 100, True), ("REFILL_B", 5000, 3000, True)]
    )
    assert r2 == "REFILL_A"
    r3 = sched._rank_oom_victim([("A_LONG", 100, 500), ("B_LONG", 50, 600)])
    assert r3 == "B_LONG"


def test_progress_is_refill(tmp_path):
    f1 = tmp_path / "r.json"
    f1.write_text(json.dumps({"redo_depth": 1, "done": {}}))
    assert sched._progress_is_refill(str(f1)) is True
    f2 = tmp_path / "f.json"
    f2.write_text(json.dumps({"done": {"k": {}}}))
    assert sched._progress_is_refill(str(f2)) is False
    assert sched._progress_is_refill(str(tmp_path / "nope.json")) is False


def test_verdicts_and_garbage(tmp_path):
    m = _marks()
    assert m(_write(tmp_path, "e.json", {"verdict": "IMPOSSIBLE"}))[1] is True
    assert m(_write(tmp_path, "f.json", {"verdict": "BEST_EFFORT"}))[1] is True
    assert m(
        _write(tmp_path, "g.json", {"done": {"k": {"verdict": "IMPOSSIBLE"}}})
    ) == (False, False, None)
    assert m(_write(tmp_path, "h.json", {"final_gain": True})) == (False, False, None)
    assert m(_write(tmp_path, "i.json", "{not json")) == (False, False, None)
    assert m(_write(tmp_path, "j.json", [1, 2])) == (False, False, None)
