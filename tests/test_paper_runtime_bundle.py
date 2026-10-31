import json
import os
from pathlib import Path

from tools.paper_runtime_bundle import build_bundle


def _write(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_bundle_is_content_addressed_read_only_and_excludes_data(tmp_path):
    source = tmp_path / "src"
    _write(source, "backtest_v12_engine.py", "ENGINE = 1\n")
    _write(source, "config.py", "CONFIG = 1\n")
    _write(source, "tools/helper.py", "HELPER = 1\n")
    _write(source, "vec_paths/core.py", "PATHS = 1\n")
    _write(source, "vec_decisions/core.py", "DECISIONS = 1\n")
    _write(source, "live_twins/core.py", "TWINS = 1\n")
    _write(source, "v15_decisions/core.py", "V15 = 1\n")
    _write(source, "data/ignored.py", "SECRET = 1\n")
    _write(source, "backups/ignored.py", "OLD = 1\n")
    _write(source, "tests/ignored.py", "TEST = 1\n")
    runtime_parent = tmp_path / "runtime"

    first = build_bundle(source, runtime_parent)
    second = build_bundle(source, runtime_parent)

    assert first["runtime_sha256"] == second["runtime_sha256"]
    assert first["reused"] is False
    assert second["reused"] is True
    runtime = Path(first["runtime_root"])
    manifest = json.loads((runtime / "runtime_manifest.json").read_text())
    names = {row["path"] for row in manifest["files"]}
    assert names == {
        "backtest_v12_engine.py", "config.py", "tools/helper.py",
        "vec_paths/core.py", "vec_decisions/core.py", "live_twins/core.py",
        "v15_decisions/core.py",
    }
    assert not (runtime / "data").exists()
    assert not (runtime / "backups").exists()
    assert not (runtime / "tests").exists()
    assert os.stat(runtime).st_mode & 0o222 == 0
    assert all(os.stat(runtime / name).st_mode & 0o222 == 0 for name in names)


def test_bundle_changes_identity_when_source_changes(tmp_path):
    source = tmp_path / "src"
    engine = _write(source, "backtest_v12_engine.py", "ENGINE = 1\n")
    parent = tmp_path / "runtime"
    first = build_bundle(source, parent)
    engine.write_text("ENGINE = 2\n", encoding="utf-8")
    second = build_bundle(source, parent)
    assert first["runtime_sha256"] != second["runtime_sha256"]
