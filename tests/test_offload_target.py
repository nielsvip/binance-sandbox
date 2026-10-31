"""A backup goes to the 5T drive or nowhere — never the Mac's own disk (USER 2026-10-31)."""
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("offload_t", ROOT / "tools" / "offload_target.py")
O = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = O
spec.loader.exec_module(O)


def test_an_unmounted_drive_gives_no_target(tmp_path, monkeypatch):
    monkeypatch.setattr(O, "mounted", lambda drive=None: False)
    path, why = O.healthy_target("h2h", drive=tmp_path / "WD25")
    assert path is None and "not mounted" in why


def test_a_stalled_drive_gives_no_target_and_never_hangs_the_caller(tmp_path, monkeypatch):
    monkeypatch.setattr(O, "mounted", lambda drive=None: True)
    def stall(*a, **k):
        raise subprocess.TimeoutExpired(cmd="probe", timeout=k.get("timeout"))
    monkeypatch.setattr(O.subprocess, "run", stall)
    path, why = O.healthy_target("h2h", drive=tmp_path)
    assert path is None and "did not answer" in why


def test_a_drive_near_its_floor_gives_no_target(tmp_path, monkeypatch):
    monkeypatch.setattr(O, "mounted", lambda drive=None: True)
    monkeypatch.setattr(O, "FLOOR_GB", 10 ** 9)   # any real disk is below a billion GB
    path, why = O.healthy_target("h2h", drive=tmp_path)
    assert path is None and "floor" in why


def test_a_healthy_drive_gives_a_path_on_the_drive(tmp_path, monkeypatch):
    monkeypatch.setattr(O, "mounted", lambda drive=None: True)
    monkeypatch.setattr(O, "FLOOR_GB", 0)
    path, why = O.healthy_target("h2h", drive=tmp_path)
    assert why is None and path == tmp_path / O.ROOT_ON_DRIVE / "h2h" and path.is_dir()
    assert not list(path.glob(".offload_probe_*")), "the write probe must clean up after itself"


def test_there_is_no_local_fallback_anywhere():
    src = (ROOT / "tools" / "offload_target.py").read_text()
    for local in ("Documents/binance/backups", "Path.home() / \"backups\"", "/tmp/"):
        assert local not in src, f"a fallback to {local} is how the Mac filled"


def test_mount_detection_does_not_confuse_similar_names(monkeypatch):
    table = "/dev/disk7s1 on /Volumes/WD25_OLD (apfs, local)\n/dev/disk3s5 on /System/Volumes/Data (apfs)\n"
    monkeypatch.setattr(O.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": table})())
    assert O.mounted(Path("/Volumes/WD25")) is False
