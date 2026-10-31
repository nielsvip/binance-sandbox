"""A backup leaves its source only after a verified copy is on the 5T drive (USER 2026-10-31)."""
import importlib.util
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
spec = importlib.util.spec_from_file_location("off5t", ROOT / "tools" / "offload_to_5t.py")
X = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = X
spec.loader.exec_module(X)


def drive(tmp_path, monkeypatch, healthy=True):
    d = tmp_path / "WD25"
    d.mkdir()
    monkeypatch.setattr(X.OT, "healthy_target", lambda sub, need=0: ((d / "binance_backups" / sub), None) if healthy else (None, "not mounted"))
    return d


def old_file(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    t = time.time() - 3600
    os.utime(path, (t, t))
    return path


def test_a_verified_copy_lands_on_the_drive_and_only_then_is_the_source_removed(tmp_path, monkeypatch):
    d = drive(tmp_path, monkeypatch)
    src = old_file(tmp_path / "src" / "a.tar", b"x" * 5000)
    m = X.Mover("mac")
    assert m.move_local(src, Path("backups/a.tar"))
    dest = d / "binance_backups" / "mac" / "backups" / "a.tar"
    assert dest.read_bytes() == b"x" * 5000 and not src.exists() and m.moved == 1


def test_a_copy_that_does_not_verify_leaves_the_source_alone(tmp_path, monkeypatch):
    drive(tmp_path, monkeypatch)
    src = old_file(tmp_path / "src" / "b.tar", b"y" * 5000)
    monkeypatch.setattr(X, "sha256_local", lambda p, chunk=0: str(p))   # source and copy can never match
    m = X.Mover("mac")
    m.move_local(src, Path("backups/b.tar"))
    assert src.exists() and m.moved == 0 and m.kept == 1


def test_an_unhealthy_drive_moves_nothing_and_removes_nothing(tmp_path, monkeypatch):
    drive(tmp_path, monkeypatch, healthy=False)
    src = old_file(tmp_path / "src" / "c.tar", b"z" * 100)
    m = X.Mover("mac")
    assert m.move_local(src, Path("backups/c.tar")) is False
    assert src.exists() and m.moved == 0


def test_files_still_being_written_are_left_alone(tmp_path):
    fresh = tmp_path / "src" / "now.tar"
    fresh.parent.mkdir(parents=True)
    fresh.write_bytes(b"open")   # mtime = now, like a guard tar mid-append
    old = old_file(tmp_path / "src" / "old.tar", b"done")
    got = list(X.iter_local(tmp_path / "src", [], settle_s=900))
    assert got == [old]


def test_a_dry_run_changes_nothing(tmp_path, monkeypatch):
    d = drive(tmp_path, monkeypatch)
    src = old_file(tmp_path / "src" / "d.tar", b"q" * 10)
    X.Mover("mac", dry=True).move_local(src, Path("backups/d.tar"))
    assert src.exists() and not (d / "binance_backups").exists()


def test_patterns_select_only_matching_files(tmp_path):
    a = old_file(tmp_path / "s" / "before_h2h_crypto_1.db.zst", b"1")
    old_file(tmp_path / "s" / "keep_me.py", b"2")
    assert list(X.iter_local(tmp_path / "s", ["before_h2h_*.db.zst"], 0)) == [a]


def test_the_size_filter_leaves_small_per_edit_backups_local(tmp_path):
    big = old_file(tmp_path / "s" / "big.tar", b"b" * 2_000_000)
    old_file(tmp_path / "s" / "before_edit.py", b"tiny")
    assert list(X.iter_local(tmp_path / "s", [], 0, min_bytes=1_000_000)) == [big]
