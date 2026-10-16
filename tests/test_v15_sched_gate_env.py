import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


S = _load("v15_fleet_scheduler")
D = _load("v15_dispatch_job")


def test_gate_env_inline_appends_benched_values(tmp_path, monkeypatch):
    monkeypatch.delenv("V15_SCHED_GATE_ENV", raising=False)
    p = tmp_path / "g.json"
    p.write_text(json.dumps({"env": {"V15_INERT_SAMPLE_EVERY": "25", "V15_V17_SHARE_RETEST": "0.84", "PATH": "/x", "V15_X": "a b"}}))
    s = S.gate_env_inline(p)
    assert s == " V15_INERT_SAMPLE_EVERY=25 V15_V17_SHARE_RETEST=0.84 V15_X='a b'"


def test_gate_env_inline_off_or_missing(tmp_path, monkeypatch):
    assert S.gate_env_inline(tmp_path / "missing.json") == ""
    p = tmp_path / "g.json"
    p.write_text(json.dumps({"env": {"V15_A": "1"}}))
    monkeypatch.setenv("V15_SCHED_GATE_ENV", "0")
    assert S.gate_env_inline(p) == ""


def test_last_inline_assignment_wins_in_bash(tmp_path):
    import subprocess
    out = subprocess.run(["bash", "-c", "export V15_A=autotune; V15_A=hard V15_A=gate bash -c 'echo $V15_A'"], capture_output=True, text=True).stdout.strip()
    assert out == "gate"


def test_autotune_skipped_when_gate_env_exists(tmp_path, monkeypatch):
    monkeypatch.delenv("V15_SCHED_GATE_ENV", raising=False)
    monkeypatch.delenv("V17_AUTOTUNE_APPLY", raising=False)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "v15_gate_env.json").write_text("{}")
    at = tmp_path / "at.json"
    at.write_text(json.dumps({"apply": True, "env": {"V15_V17_SHARE_RETEST": 0.6}}))
    monkeypatch.setattr(D, "ROOT", tmp_path)
    pre, info = D._autotune_exports("X:30D", at)
    assert pre == "" and "skipped" in info
    (tmp_path / "data" / "v15_gate_env.json").unlink()
    pre, info = D._autotune_exports("X:30D", at)
    assert "V15_V17_SHARE_RETEST=0.6" in pre


def _marks():
    import ast
    tree = ast.parse(S.HOST_PY)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_progress_marks")
    ns = {"json": json}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "<host_py>", "exec"), ns)
    return ns["_progress_marks"]


def test_terminal_softskip_board_is_done_and_not_redo(tmp_path, monkeypatch):
    monkeypatch.delenv("V15_SCHED_SOFTSKIP_DONE", raising=False)
    S._progress_marks = _marks()
    p = tmp_path / "X_LONG_v14_progress.json"
    p.write_text(json.dumps({"final_gain": 7.14, "verdict": "BEST_EFFORT", "needs_redo": {"overrides": {}, "depth": 1}}))
    done, q, g, redo = S._progress_marks(str(p))
    assert done and q and g == 7.14 and redo is False
    p.write_text(json.dumps({"final_gain": 7.14, "verdict": "QUALIFIED", "needs_redo": {"overrides": {}}}))
    done, q, g, redo = S._progress_marks(str(p))
    assert not done and redo is True
    monkeypatch.setenv("V15_SCHED_SOFTSKIP_DONE", "0")
    p.write_text(json.dumps({"final_gain": 7.14, "verdict": "BEST_EFFORT", "needs_redo": {"overrides": {}}}))
    assert S._progress_marks(str(p))[3] is True
