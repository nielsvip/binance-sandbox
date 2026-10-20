"""Archive+clip collateral for tools/klines_3m_archive.py.

Contract under test (USER 2026-10-20):
  * whenever an ext drive is connected, the latest 3m bars are APPENDED
    to every symbol's archive copy (archive only ever grows);
  * local 3m files are clipped to KEEP (1800) only AFTER the archive
    holds those bars;
  * with no drive connected nothing is clipped and nothing is lost;
  * staleness/maxed markers never gate the append — no exceptions.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.klines_3m_archive import append_newer, clip_local, is_synthetic, load_list, run  # noqa: E402


def _bar(ts, close=1.0):
    return {"timestamp": ts, "open": 1.0, "high": 1.0, "low": 1.0,
            "close": close, "volume": 1.0}


def _synthetic(ts, price=2.0):
    """Mark-price placeholder as written by ez_market_data.klines_cache_writeback."""
    return {"timestamp": ts, "open": price, "high": price, "low": price,
            "close": price, "volume": 0}


def _write(p: Path, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rows))


def test_append_appends_only_newer_and_keeps_archive_values(tmp_path):
    arch = tmp_path / "a.json"
    _write(arch, [_bar("2026-10-20T18:00:00.000000Z", close=5.0),
                  _bar("2026-10-20T18:03:00.000000Z", close=6.0)])
    src = [_bar("2026-10-20T18:03:00.000000Z", close=999.0),
           _bar("2026-10-20T18:06:00.000000Z", close=7.0)]
    added, total, conflicts = append_newer(arch, src)
    assert added == 1 and total == 3 and conflicts == 1
    rows = load_list(arch)
    assert [r["timestamp"][:19] for r in rows] == [
        "2026-10-20T18:00:00", "2026-10-20T18:03:00", "2026-10-20T18:06:00"]
    assert rows[1]["close"] == 6.0  # archive value wins, never overwritten


def test_archive_never_shrinks(tmp_path):
    arch = tmp_path / "a.json"
    bars = [_bar(f"2026-10-20T18:{m:02d}:00.000000Z") for m in (0, 3, 6, 9)]
    _write(arch, bars)
    added, total, _ = append_newer(arch, bars[:1])  # truncated source
    assert added == 0 and total == 4
    assert len(load_list(arch)) == 4


def test_clip_keeps_newest_keep(tmp_path):
    local = tmp_path / "l.json"
    _write(local, [_bar(f"2026-10-20T18:{m:02d}:00.000000Z") for m in range(0, 30, 3)])
    before, after = clip_local(local, keep=4)
    assert (before, after) == (10, 4)
    rows = load_list(local)
    assert rows[-1]["timestamp"][:19] == "2026-10-20T18:27:00"
    before2, after2 = clip_local(local, keep=4)  # already at keep: no-op
    assert (before2, after2) == (4, 4)


def test_run_appends_and_clips_only_with_drive(tmp_path):
    repo = tmp_path / "repo"
    cache = repo / "klines_cache"
    cache.mkdir(parents=True)
    stale_ts = "2026-10-19T00:00:00.000000Z"  # a day old: still archived, no exceptions
    _write(cache / "FOOUSDT_3m.json",
           [_bar(stale_ts), _bar("2026-10-19T00:03:00.000000Z")])
    drive = tmp_path / "EXT"
    drive.mkdir()
    stats = run(repo, drives=[drive], keep=1)
    assert stats["drives"] == [str(drive)]
    assert stats["archived_files"] == 1 and stats["clipped_files"] == 1
    arch_rows = load_list(drive / "binance_archive" / "klines_archive" / "FOOUSDT_3m.json")
    assert len(arch_rows) == 2
    assert len(load_list(cache / "FOOUSDT_3m.json")) == 1  # clipped to keep=1


def test_synthetic_bars_never_enter_archive_but_real_replaces_them(tmp_path):
    arch = tmp_path / "a.json"
    _write(arch, [_bar("2026-10-20T18:00:00.000000Z")])
    added, total, _ = append_newer(
        arch, [_synthetic("2026-10-20T18:03:00.000000Z")])
    assert (added, total) == (0, 1)  # placeholder skipped, not frozen as truth
    assert is_synthetic(_synthetic("2026-10-20T18:03:00.000000Z"))
    assert not is_synthetic(_bar("2026-10-20T18:03:00.000000Z"))
    # archive holding a synthetic (seeded by other means) is healed by the real bar
    _write(arch, [_synthetic("2026-10-20T18:03:00.000000Z")])
    added, total, conflicts = append_newer(
        arch, [_bar("2026-10-20T18:03:00.000000Z", close=9.0)])
    assert (added, total, conflicts) == (1, 1, 0)
    assert load_list(arch)[0]["close"] == 9.0
    # a real archived bar is never overwritten by a later synthetic
    added, total, _ = append_newer(
        arch, [_synthetic("2026-10-20T18:03:00.000000Z", price=3.0)])
    assert (added, total) == (0, 1)
    assert load_list(arch)[0]["close"] == 9.0


def test_run_without_drive_clips_nothing(tmp_path):
    repo = tmp_path / "repo"
    cache = repo / "klines_cache"
    cache.mkdir(parents=True)
    local = cache / "BARUSDT_3m.json"
    _write(local, [_bar(f"2026-10-20T18:{m:02d}:00.000000Z") for m in range(0, 30, 3)])
    stats = run(repo, drives=[], keep=4)
    assert stats["drives"] == [] and stats["clipped_files"] == 0
    assert len(load_list(local)) == 10  # untouched until a drive confirms the append
