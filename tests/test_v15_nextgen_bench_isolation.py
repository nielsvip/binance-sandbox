import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.v15_nextgen_bench_isolation import check_config, command, prepare


def fixture(tmp_path, pilot="pass\n", bad_config=False):
    root, side, assets = (tmp_path / name for name in ("root", "SIDE_LONG", "assets"))
    root.mkdir()
    side.mkdir()
    assets.mkdir()
    env_dir = tmp_path / "interpreter"
    (env_dir / "bin").mkdir(parents=True)
    (env_dir / "bin/python").symlink_to(sys.executable)
    (root / ".venv").symlink_to(env_dir, target_is_directory=True)
    (root / "backtest_v8/indicators").mkdir(parents=True)
    (root / "backtest_v8/indicators/SIDE.npz").write_bytes(b"fixture")
    repo = Path(__file__).resolve().parents[1]
    parsed = ast.parse((repo / "config.py").read_text())
    actual = next(node for node in ast.walk(parsed) if isinstance(node, ast.FunctionDef) and node.name == "_resolve_base_path")
    config = "import os, platform\nfrom pathlib import Path\n" + ast.unparse(actual) + "\nclass Config:\n    BASE_PATH = _resolve_base_path()\n    DATA_DIR = BASE_PATH / 'data'\n"
    if bad_config:
        config += f"Config.BASE_PATH = Path({str(tmp_path / 'live')!r})\n"
    (root / "config.py").write_text(config)
    (root / "config_tradier.py").write_text("from config import Config\nclass TradierConfig:\n    BASE_PATH = Config.BASE_PATH\n")
    (root / "per_sym_store.py").write_text("import os\nfrom pathlib import Path\nDB_PATH = Path(os.environ['PER_SYM_STORE_DB'])\n")
    meta = {"source_sha256": {}, "candidate_sha256": {}, "scope": "test routing"}
    for name, payload in (("v15_pilot.py", pilot), ("switch_parity.py", "pass\n")):
        (root / name).write_text(payload)
        (assets / name).write_text(payload)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        meta["source_sha256"][name] = digest
        meta["candidate_sha256"][name] = digest
    (assets / "manifest.json").write_text(json.dumps(meta))
    return root, side, assets


def test_actual_repository_config_resolver_and_class_paths_private(tmp_path):
    root, side, assets = fixture(tmp_path)
    policies = {"V15_EVAL_CACHE": "1", "V15_BOARD_BUDGET": "12000", "V18_CATCHUP": "0", "V15_INERT_SAMPLE_EVERY": "100"}
    executable, env = prepare(root, side, policies, "SIDE", 30, assets)
    receipt = check_config(executable, root, side, env)
    assert receipt["status"] == "ACTUAL_CONFIG_PRIVATE_PATHS_PASS"
    assert receipt["paths"]["Config.BASE_PATH"] == str(root.resolve())
    assert receipt["paths"]["TradierConfig.BASE_PATH"] == str(root.resolve())
    assert receipt["paths"]["per_sym_store.DB_PATH"] == str((side / "private_store/per_sym_store.db").resolve())
    assert all(env[key] == value for key, value in policies.items())
    assert env["SWITCH_PARITY_REGISTER"] == "0"
    assert "V12_NPZ_DIR" not in env
    assert not any(path.is_symlink() for path in root.rglob("*"))


def test_actual_class_path_mismatch_refused_not_merely_env_checked(tmp_path):
    root, side, assets = fixture(tmp_path, bad_config=True)
    executable, env = prepare(root, side, {}, "SIDE", 30, assets)
    with pytest.raises(ValueError, match="actual config isolation preflight failed"):
        check_config(executable, root, side, env)
    assert (side / "config_preflight_failure.json").exists()


def test_audited_outside_write_refused_and_receipt_preserved(tmp_path):
    outside = tmp_path / "live.txt"
    root, side, assets = fixture(tmp_path, f"from pathlib import Path\nPath({str(outside)!r}).write_text('bad')\n")
    executable, env = prepare(root, side, {}, "SIDE", 30, assets)
    check_config(executable, root, side, env)
    result = subprocess.run(command(executable, root, side, []), env=env, cwd=root, capture_output=True, text=True)
    assert result.returncode != 0
    assert not outside.exists()
    assert "outside private roots" in result.stderr
    assert (side / "audit/denied_writes.jsonl").read_text().strip()


def test_allowed_root_and_side_writes_and_second_side_interpreter(tmp_path):
    root, side, assets = fixture(tmp_path)
    executable, env = prepare(root, side, {}, "SIDE", 30, assets)
    other = tmp_path / "OTHER_LONG"
    other.mkdir()
    second, _ = prepare(root, other, {}, "SIDE", 30, assets)
    assert second == executable
    assert (root / ".venv/bin/python").is_file()
    assert not (root / ".venv").is_symlink()
    check_config(executable, root, side, env)
    payload = f"from pathlib import Path\nPath({str(root / 'own.txt')!r}).write_text('ok')\nPath({str(side / 'own.txt')!r}).write_text('ok')\n"
    (root / "v15_pilot.py").write_text(payload)
    result = subprocess.run(command(executable, root, side, []), env=env, cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_missing_npz_refused_before_config_or_pilot(tmp_path):
    root, side, assets = fixture(tmp_path)
    (root / "backtest_v8/indicators/SIDE.npz").unlink()
    with pytest.raises(ValueError, match="missing materialized frozen NPZ"):
        prepare(root, side, {}, "SIDE", 30, assets)


def test_foreign_npz_read_refused(tmp_path):
    outside = tmp_path / "foreign.npz"
    outside.write_bytes(b"not a fixture input")
    root, side, assets = fixture(tmp_path, f"from pathlib import Path\nPath({str(outside)!r}).read_bytes()\n")
    executable, env = prepare(root, side, {}, "SIDE", 30, assets)
    result = subprocess.run(command(executable, root, side, []), env=env, cwd=root, capture_output=True, text=True)
    assert result.returncode != 0
    assert "outside frozen root" in result.stderr


def test_aborted_staged_run_preserves_all_outputs(tmp_path, monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(repo / "tools"))
    spec = importlib.util.spec_from_file_location("isolation_review_bench", repo / "data/reports/nextgen_gate_artifact_repair/v15_npz_bench.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    run = tmp_path / "run"
    files = [run / "root/config.py", run / "SIDE/pilot.log", run / "SIDE/out/board.xlsx", run / "SIDE/model_resolution.json"]
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"original evidence")
    module.cleanup_aborted_run(run, {}, False)
    assert all(path.read_bytes() == b"original evidence" for path in files)
    assert (run / "ABORTED_RUN_RETAINED.json").exists()
