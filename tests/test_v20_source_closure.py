from pathlib import Path

import pytest

from tools.v20_candidate_refresh import source_files


def put(root, name, text=""):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_canonical_packages_and_transitive_namespace_imports_exclude_archives(tmp_path):
    put(tmp_path, "entry.py", "from vec_decisions import exits\n")
    put(tmp_path, "tools/train.py", "import vec_paths.filters\n")
    put(tmp_path, "vec_paths/__init__.py")
    put(tmp_path, "vec_paths/filters.py", "import deeper.rules\n")
    put(tmp_path, "vec_decisions/exits.py")
    put(tmp_path, "deeper/rules.py")
    archived = put(tmp_path, "projects/old/code.py")
    archived.unlink()
    archived.symlink_to(tmp_path / "missing.py")
    put(tmp_path, "archive/legacy.py", "import missing_dependency\n")
    names = {path.relative_to(tmp_path).as_posix() for path in source_files(tmp_path)}
    assert names == {"entry.py", "tools/train.py", "vec_paths/__init__.py", "vec_paths/filters.py", "vec_decisions/exits.py", "deeper/rules.py"}


def test_real_source_symlink_still_fails_closed(tmp_path):
    put(tmp_path, "tools/train.py").unlink()
    (tmp_path / "tools/train.py").symlink_to(tmp_path / "missing.py")
    with pytest.raises(ValueError, match="symlink"):
        source_files(tmp_path)


def test_imported_package_symlink_cannot_escape_snapshot(tmp_path):
    put(tmp_path, "entry.py", "import external.rules\n")
    (tmp_path / "external").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        source_files(tmp_path)
