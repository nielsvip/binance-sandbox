"""USER 2026-10-12: 4-day fresh-skip — never re-calculate a sym_side with a completed 30d matrix newer than 4 days."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import v15_fleet_scheduler as S


def _touch(path, age_days, now):
    with open(path, "w") as f:
        f.write("x")
    ts = now - age_days * 86400.0
    os.utime(path, (ts, ts))


def test_fresh_computed_sides(tmp_path, monkeypatch):
    now = time.time()
    monkeypatch.delenv("V15_FRESH_SKIP_DAYS", raising=False)
    _touch(tmp_path / "AAPL_LONG_bh1_gain2_30d_matrix.xlsx", 1.0, now)
    _touch(tmp_path / "AAPL_SHORT_bh1_gain2_30d_matrix.xlsx", 5.0, now)
    _touch(tmp_path / "MSFT_LONG_30d_matrix.xlsx", 3.99, now)
    _touch(tmp_path / "MSFT_SHORT_30d_matrix.xlsx", 4.01, now)
    _touch(tmp_path / "OLD_LONG_bh1_gain2_30d_matrix.xlsx.bak", 0.5, now)
    _touch(tmp_path / "OLD_SHORT_bh1_gain2_30d_matrix.html", 0.5, now)
    out = S._fresh_computed_sides(str(tmp_path), now=now)
    assert set(out) == {"AAPL_LONG", "MSFT_LONG"}


def test_fresh_newest_wins_and_missing_dir_open(tmp_path):
    now = time.time()
    d = tmp_path / "m"
    d.mkdir()
    _touch(d / "NVDA_LONG_a_30d_matrix.xlsx", 6.0, now)
    _touch(d / "NVDA_LONG_b_30d_matrix.xlsx", 2.0, now)
    out = S._fresh_computed_sides(str(d), now=now)
    assert set(out) == {"NVDA_LONG"}
    assert S._fresh_computed_sides(str(tmp_path / "nope"), now=now) == {}


def test_fresh_env_override_disables(tmp_path, monkeypatch):
    now = time.time()
    _touch(tmp_path / "AMD_LONG_bh1_gain2_30d_matrix.xlsx", 0.5, now)
    monkeypatch.setenv("V15_FRESH_SKIP_DAYS", "0")
    assert S._fresh_computed_sides(str(tmp_path), now=now) == {}
