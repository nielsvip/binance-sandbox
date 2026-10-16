"""USER 2026-10-13 pair currency: a side calculated on an OLD NPZ is not done. When its sibling
re-queues on the current NPZ, the stale side flips back to need30 so both sides of a tradeable
symbol are calculated on the same data (ONEUSDT_LONG stayed on the Oct-10 NPZ while SHORT ran)."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import v15_fleet_scheduler as S


def _manifest(d, ss, md5, age_s=0.0):
    import time

    p = d / f"{ss}_bh1_gain2_t10_30d_matrix_manifest.json"
    p.write_text(json.dumps({"symside": ss, "npz_md5": md5}))
    ts = time.time() - age_s
    os.utime(p, (ts, ts))
    return p


def test_pair_stale_sibling_flips_only_on_npz_mismatch(tmp_path):
    _manifest(tmp_path, "ONEUSDT_LONG", "oldmd5")
    st = {"LONG": ("terminal_ok", None), "SHORT": ("need30", 1)}
    assert S._pair_stale_sibling("ONEUSDT", st, str(tmp_path), "newmd5") == "LONG"
    assert S._pair_stale_sibling("ONEUSDT", st, str(tmp_path), "oldmd5") is None  # same data: truly done


def test_launched_actions_collapses_pure_30d_pair(monkeypatch):
    pair = [{"sym": "SNDK", "side": "LONG", "window": "30D"}, {"sym": "SNDK", "side": "SHORT", "window": "30D"}]
    monkeypatch.delenv("V15_PAIR_MODE", raising=False)
    assert S._launched_actions(pair) == pair[:1]  # pilot completes the pair in-process
    monkeypatch.setenv("V15_PAIR_MODE", "0")
    assert S._launched_actions(pair) == pair  # kill switch: one process per side
    mixed = [{"sym": "SNDK", "side": "LONG", "window": "30D"}, {"sym": "SNDK", "side": "SHORT", "window": "365D"}]
    monkeypatch.delenv("V15_PAIR_MODE", raising=False)
    assert S._launched_actions(mixed) == mixed  # chain windows never collapsed


def test_pair_stale_sibling_fail_open_and_guards(tmp_path):
    st = {"LONG": ("terminal_ok", None), "SHORT": ("need30", 1)}
    assert S._pair_stale_sibling("ONEUSDT", st, str(tmp_path), "newmd5") is None  # no manifest: fail open
    running = {"LONG": ("running", None), "SHORT": ("need30", 1)}
    assert S._pair_stale_sibling("ONEUSDT", running, str(tmp_path), "newmd5") is None
    both = {"LONG": ("need30", 1), "SHORT": ("need30", 1)}
    assert S._pair_stale_sibling("ONEUSDT", both, str(tmp_path), "newmd5") is None  # nothing stale-terminal
    fresh = {"LONG": ("terminal_fresh", None), "SHORT": ("need30", 1)}
    _manifest(tmp_path, "ONEUSDT_LONG", "oldmd5")
    assert S._pair_stale_sibling("ONEUSDT", fresh, str(tmp_path), "newmd5") == "LONG"
