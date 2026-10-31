"""The board guard's pre-change tars never land on the Mac disk or fill a server (USER 2026-10-31)."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


def load():
    spec = importlib.util.spec_from_file_location("bntg", ROOT / "tools" / "board_name_truth_guard.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


def test_on_the_mac_the_tar_goes_to_the_5t_drive(tmp_path, monkeypatch):
    G = load()
    import offload_target
    monkeypatch.setattr(G.sys, "platform", "darwin")
    monkeypatch.setattr(offload_target, "healthy_target", lambda sub, need=0: (tmp_path / "WD25" / sub, None))
    assert G.backup_dir() == tmp_path / "WD25" / "board_name_truth"


def test_on_the_mac_an_unavailable_drive_means_no_change_at_all(monkeypatch):
    G = load()
    import offload_target
    monkeypatch.setattr(G.sys, "platform", "darwin")
    monkeypatch.setattr(offload_target, "healthy_target", lambda sub, need=0: (None, "not mounted"))
    try:
        G.backup_dir()
        raise AssertionError("must refuse")
    except G.BackupUnavailable as exc:
        assert "not mounted" in str(exc)


def test_a_run_with_no_backup_target_changes_nothing_and_exits_cleanly(tmp_path, monkeypatch, capsys):
    G = load()
    monkeypatch.setattr(G, "backup_dir", lambda: (_ for _ in ()).throw(G.BackupUnavailable("drive gone")))
    built = []
    monkeypatch.setattr(G, "Guard", lambda *a, **k: built.append(1))
    assert G.main(["--root", str(tmp_path), "--state", str(tmp_path / "s.json")]) == 0
    assert built == [], "the Guard must not even be constructed"
    assert "changing NOTHING" in capsys.readouterr().out


def test_on_a_server_the_spool_refuses_below_the_floor(tmp_path, monkeypatch):
    G = load()
    monkeypatch.setattr(G.sys, "platform", "linux")
    monkeypatch.setattr(G.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(G, "SERVER_FLOOR_GB", 10 ** 9)
    try:
        G.backup_dir()
        raise AssertionError("must refuse below the floor")
    except G.BackupUnavailable as exc:
        assert "fall below" in str(exc)


def test_on_a_server_the_spool_refuses_past_its_cap(tmp_path, monkeypatch):
    G = load()
    monkeypatch.setattr(G.sys, "platform", "linux")
    monkeypatch.setattr(G.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(G, "SERVER_FLOOR_GB", 0)
    monkeypatch.setattr(G, "SPOOL_CAP_GB", 0)
    spool = tmp_path / "backups" / "board_name_truth_spool"
    spool.mkdir(parents=True)
    (spool / "x.tar").write_bytes(b"1" * 10)
    try:
        G.backup_dir()
        raise AssertionError("must refuse past the cap")
    except G.BackupUnavailable as exc:
        assert "cap" in str(exc)


def test_the_guard_never_writes_to_the_old_local_backups_dir():
    src = (ROOT / "tools" / "board_name_truth_guard.py").read_text()
    assert 'root / "backups" / "board_name_truth"' not in src
