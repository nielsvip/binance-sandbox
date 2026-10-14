"""2026-10-14: every tier refreshes once older than its window (USER "run all sym_sides every 24 hours"); terminal
soft-skip verdicts with a final gain count as done (the SOFT-SKIP relaunch loop was 42% of 24h launches)."""

import ast
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from tools import v15_fleet_scheduler as S

NOW = 10 * 86400.0
WIN = {"M": 1.0, "L": 2.0}


def _t(tier, age_d):
    return {"X_LONG": {"tier": tier, "mtime": NOW - age_d * 86400.0}}


def test_w_unchanged():
    assert S._refresh_due("X_LONG", _t("W", 1.1), NOW, WIN)
    assert not S._refresh_due("X_LONG", _t("W", 0.9), NOW, WIN)


def test_m_l_refresh_after_window(monkeypatch):
    monkeypatch.delenv("V15_SCHED_REFRESH_ALL", raising=False)
    assert S._refresh_due("X_LONG", _t("M", 1.2), NOW, WIN)
    assert not S._refresh_due("X_LONG", _t("M", 0.8), NOW, WIN)
    assert S._refresh_due("X_LONG", _t("L", 2.1), NOW, WIN)
    assert not S._refresh_due("X_LONG", _t("L", 1.5), NOW, WIN)
    assert not S._refresh_due("Y_LONG", _t("M", 5), NOW, WIN)


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("V15_SCHED_REFRESH_ALL", "0")
    assert not S._refresh_due("X_LONG", _t("M", 5), NOW, WIN)
    assert S._refresh_due("X_LONG", _t("W", 5), NOW, WIN)


def _marks():
    tree = ast.parse(S.HOST_PY)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_progress_marks")
    ns = {"json": json}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "<host_py>", "exec"), ns)
    return ns["_progress_marks"]


def test_softskip_verdict_done(tmp_path, monkeypatch):
    monkeypatch.delenv("V15_SCHED_SOFTSKIP_DONE", raising=False)
    p = tmp_path / "a.json"
    p.write_text(json.dumps({"final_gain": 3.2, "verdict": "BEST_EFFORT", "needs_redo": {"x": 1}}))
    assert _marks()(str(p))[0] is True
    p.write_text(json.dumps({"final_gain": 3.2, "verdict": "QUALIFIED", "needs_redo": {"x": 1}}))
    assert _marks()(str(p))[0] is False
    monkeypatch.setenv("V15_SCHED_SOFTSKIP_DONE", "0")
    p.write_text(json.dumps({"final_gain": 3.2, "verdict": "BEST_EFFORT", "needs_redo": {"x": 1}}))
    assert _marks()(str(p))[0] is False
