import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import klines_ext_backup as M
from klines_ext_backup import merge_file


def _bar(ts, px):
    return {
        "timestamp": ts,
        "open": px,
        "high": px,
        "low": px,
        "close": px,
        "volume": 1.0,
    }


def _write(p, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rows))
    return p


def test_bigger_staged_extends(tmp_path):
    b = _write(tmp_path / "b.json", [_bar("2026-10-01T00:00:00", 1.0)])
    s = _write(
        tmp_path / "s.json",
        [_bar("2026-10-01T00:00:00", 1.0), _bar("2026-10-01T00:15:00", 2.0)],
    )
    assert merge_file(s, b)[0] == "extended"
    assert len(json.loads(b.read_text())) == 2


def test_smaller_staged_appends_newer_only(tmp_path):
    b = _write(
        tmp_path / "b.json",
        [_bar("2026-10-01T00:00:00", 1.0), _bar("2026-10-01T00:15:00", 2.0)],
    )
    s = _write(
        tmp_path / "s.json",
        [_bar("2026-10-01T00:15:00", 999.0), _bar("2026-10-01T00:30:00", 3.0)],
    )
    action, _ = merge_file(s, b)
    assert action == "appended-newer"
    rows = json.loads(b.read_text())
    assert len(rows) == 3
    assert [r for r in rows if r["timestamp"] == "2026-10-01T00:15:00"][0][
        "close"
    ] == 2.0


def test_empty_staged_never_wipes(tmp_path):
    b = _write(
        tmp_path / "b.json",
        [_bar("2026-10-01T00:00:00", 1.0), _bar("2026-10-01T00:15:00", 2.0)],
    )
    s = _write(tmp_path / "s.json", [])
    assert merge_file(s, b)[0] in ("up-to-date", "refused")
    assert len(json.loads(b.read_text())) == 2


def test_corrupt_staged_keeps_backup(tmp_path):
    b = _write(tmp_path / "b.json", [_bar("2026-10-01T00:00:00", 1.0)])
    s = tmp_path / "s.json"
    s.write_text("{corrupt")
    before = b.read_bytes()
    assert merge_file(s, b)[0] == "skip-corrupt-staged"
    assert b.read_bytes() == before


def test_established_backup_refuses_a_nearly_full_drive(tmp_path, monkeypatch):
    """USER 2026-10-16 ("make sure the backups on ext drive remain complete"): drive_ok used to return True
    unconditionally once klines_cache existed, so the nightly run proceeded with any free space and the merge died
    part-way through — a backup that silently stops being complete. E3T is at 95% (27 GB of 466 GB) with staging
    alone 12 GB, so the floor has to apply to an ESTABLISHED backup too, not just the first seeding."""
    import os as _os
    import klines_ext_backup as E
    (tmp_path / "klines_cache").mkdir(parents=True)

    class _ST:
        f_frsize = 1
        def __init__(self, free): self.f_bavail = free

    monkeypatch.setattr(E, "MIN_FREE_EXISTING_GB", 8.0)
    monkeypatch.setattr(_os, "statvfs", lambda p: _ST(int(2e9)))
    ok, free = E.drive_ok(tmp_path)
    assert ok is False and free == 2.0, (ok, free)
    monkeypatch.setattr(_os, "statvfs", lambda p: _ST(int(27e9)))
    ok, free = E.drive_ok(tmp_path)
    assert ok is True and free == 27.0, (ok, free)


def test_tmp_leftovers_are_swept(tmp_path):
    import klines_ext_backup as E
    d = tmp_path / "klines_cache" / "tradier"
    d.mkdir(parents=True)
    (d / "AAA_15m.json").write_text("[]")
    (d / "AAA_15m.json.tmp").write_text("x" * 1000)
    (d / "BBB_15m.json.tmp").write_text("y" * 2000)
    n, gb = E.sweep_tmp(tmp_path)
    assert n == 2 and gb >= 0
    assert (d / "AAA_15m.json").exists(), "a real backup file must never be swept"
    assert not list(d.glob("*.tmp"))


def test_state_is_flattened_not_nested(tmp_path, monkeypatch):
    """2026-10-16 on E3T: BACKUP_STATE.json read as {"updated_utc","files"} while being WRITTEN wrapped, so each run
    buried the previous signatures one level deeper and compared against the wrapper's own keys."""
    import json

    src = tmp_path / "stage"
    (src).mkdir()
    rows = [{"timestamp": "2026-10-12T13:30:00.000000Z", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]
    (src / "A_15m.json").write_text(json.dumps(rows))
    dst = tmp_path / "backup"
    (dst / "klines_cache").mkdir(parents=True)
    legacy = {"updated_utc": "old", "files": {"updated_utc": "older", "files": {"GONE_15m.json": "1:1"}, "A_15m.json": "9:9"}}
    (dst / "BACKUP_STATE.json").write_text(json.dumps(legacy))
    M.merge_tree(src, dst)
    st = json.loads((dst / "BACKUP_STATE.json").read_text())
    assert set(st.keys()) == {"updated_utc", "files"}
    assert "files" not in st["files"] and "updated_utc" not in st["files"]
    assert st["files"]["GONE_15m.json"] == "1:1", "signatures from older generations must survive the flatten"
    assert st["files"]["A_15m.json"] != "9:9", "the file just merged must carry its real signature"


def test_an_unchanged_file_is_skipped_by_its_signature(tmp_path):
    import json

    src = tmp_path / "stage"
    src.mkdir()
    rows = [{"timestamp": "2026-10-12T13:30:00.000000Z", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]
    (src / "A_15m.json").write_text(json.dumps(rows))
    dst = tmp_path / "backup"
    (dst / "klines_cache").mkdir(parents=True)
    assert M.merge_tree(src, dst)["seeded"] == 1
    assert M.merge_tree(src, dst)["up-to-date"] == 1, "a second run must not re-read an unchanged file"


def test_a_second_source_gets_its_own_subtree_and_state_keys(tmp_path):
    """The 3m/5m history that v15_npz_bench reads was backed up nowhere; one backup can only replace the others if it
    covers every source, and two sources must not collide in BACKUP_STATE.json."""
    import json

    src = tmp_path / "stage" / "data/klines_3m_hist_w2"
    src.mkdir(parents=True)
    rows = [{"timestamp": "2026-10-12T13:30:00.000000Z", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]
    (src / "A_3m.json").write_text(json.dumps(rows))
    kc = tmp_path / "stage" / "klines_cache"
    kc.mkdir(parents=True)
    (kc / "A_3m.json").write_text(json.dumps(rows))
    dst = tmp_path / "backup"
    (dst / "klines_cache").mkdir(parents=True)
    assert M.merge_tree(kc, dst, subtree="klines_cache")["seeded"] == 1
    assert M.merge_tree(src, dst, subtree="data/klines_3m_hist_w2")["seeded"] == 1
    assert (dst / "klines_cache" / "A_3m.json").exists()
    assert (dst / "data/klines_3m_hist_w2" / "A_3m.json").exists()
    st = json.loads((dst / "BACKUP_STATE.json").read_text())["files"]
    assert "A_3m.json" in st and "data/klines_3m_hist_w2/A_3m.json" in st, st
    assert M.merge_tree(src, dst, subtree="data/klines_3m_hist_w2")["up-to-date"] == 1


def test_the_source_table_lists_every_kline_source():
    assert [s for s, _ in M.SOURCES][0] == "klines_cache"
    assert "data/klines_3m_hist_w2" in [s for s, _ in M.SOURCES]
    assert "data/klines_5m_hist_w2" in [s for s, _ in M.SOURCES]


def test_staging_is_not_on_the_backup_drive():
    """E3T held klines_backup_s1/_stage at 12 GB beside the 12 GB backup — half the drive's kline footprint was a
    second copy of the backup, on a volume at 95%."""
    assert not str(M.STAGE).startswith(str(M.PRIMARY))
    assert not str(M.STAGE).startswith(str(M.MIRROR))
    assert not str(M.STAGE).startswith("/Volumes/")


def test_the_history_manifest_is_not_treated_as_a_kline_file(tmp_path):
    """data/klines_*_hist_w2 ships a manifest.json; it logged "ANOMALY manifest.json staged unparseable" every run."""
    import json

    src = tmp_path / "stage"
    src.mkdir()
    (src / "manifest.json").write_text(json.dumps({"built": "2026-03-30"}))
    rows = [{"timestamp": "2026-10-12T13:30:00.000000Z", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]
    (src / "A_5m.json").write_text(json.dumps(rows))
    dst = tmp_path / "backup"
    (dst / "klines_cache").mkdir(parents=True)
    stats = M.merge_tree(src, dst, subtree="data/klines_5m_hist_w2")
    assert stats["seeded"] == 1 and stats["skip-corrupt-staged"] == 0, stats
    assert "--exclude=manifest.json" in M.RSYNC_EXCLUDE


def test_a_detached_drive_is_refused_not_created(tmp_path, monkeypatch):
    """USER 2026-10-16: all three externals dropped off the bus mid-run. drive_ok used to mkdir the backup root, so it
    would have reported the BOOT disk's free space and rsynced 12 GB onto the internal volume. /Volumes/WD25 proves it
    happens: 1.2 GB written 2026-10-14 19:34 with the same st_dev as /."""
    gone = Path("/Volumes/DEFINITELY_NOT_MOUNTED_XYZ/klines_backup_s1")
    ok, why = M.is_ext_mount(gone)
    assert not ok and "detached" in why, why
    ok, free = M.drive_ok(gone)
    assert not ok, (ok, free)
    assert not gone.exists(), "a missing drive must never be created as a directory"


def test_a_leftover_mountpoint_on_the_boot_disk_is_refused(tmp_path, monkeypatch):
    """What /Volumes/WD25 actually is: a plain directory on the boot volume that every `[ -d ]`/exists() test passes."""
    vols = tmp_path / "Volumes"
    phantom = vols / "WD25" / "klines_backup_s1"
    phantom.mkdir(parents=True)
    monkeypatch.setattr(M, "VOLUMES", vols)
    ok, why = M.is_ext_mount(phantom)
    assert not ok and "BOOT DISK" in why, why
    ok, _ = M.drive_ok(phantom)
    assert not ok


def test_a_path_outside_volumes_is_not_second_guessed():
    ok, why = M.is_ext_mount(Path("/tmp/anything"))
    assert ok and why == "not a removable-volume path"
