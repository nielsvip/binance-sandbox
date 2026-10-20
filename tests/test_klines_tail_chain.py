"""USER LAW 2026-10-20 (NPZ <= 15 min): incremental klines_canonical tail path. Hermetic: scratch stores, no network."""
import json
import os
import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))
os.environ.setdefault("KLINES_CANONICAL_WLOCK", "/tmp/test_klines_tail_chain.lock")
import klines_canonical as K  # noqa: E402
import klines_tail_apply as AP  # noqa: E402
import klines_tail_push as PU  # noqa: E402

T0 = 1_790_000_000_000 - (1_790_000_000_000 % 900_000)


def _row(i, base=100.0):
    return {"timestamp": K.iso(T0 + i * 900_000), "open": base + i, "high": base + i + 1, "low": base + i - 1, "close": base + i + 0.5, "volume": 10.0 + i}


def _mk(path, n):
    pathlib.Path(path).write_text(json.dumps([_row(i) for i in range(n)]))


def _kl(i, base=100.0):
    r = _row(i, base)
    t = T0 + i * 900_000
    return [t, str(r["open"]), str(r["high"]), str(r["low"]), str(r["close"]), str(r["volume"]), t + 899_999]


def test_fast_merge_is_byte_identical_to_full_merge(tmp_path):
    a, b = tmp_path / "A_15m.json", tmp_path / "B_15m.json"
    _mk(a, 40)
    _mk(b, 40)
    ks = [_kl(i) for i in (37, 38, 39, 40, 41)]
    now = T0 + 60 * 900_000
    assert K.merge_exchange_tail(a, ks, now) == (2, 0)
    assert K.merge_exchange(b, ks, now)[:2] == (2, 0)
    assert a.read_bytes() == b.read_bytes()
    assert K.merge_exchange_tail(a, ks, now) == (0, 0)


def test_fast_merge_correction_takes_full_path(tmp_path):
    a = tmp_path / "A_15m.json"
    _mk(a, 40)
    ks = [_kl(38, base=500.0), _kl(39), _kl(40)]
    assert K.merge_exchange_tail(a, ks, T0 + 60 * 900_000) == (1, 1)
    assert json.loads(a.read_text())[38]["open"] == 538.0


def test_apply_appends_grow_only_and_reports_gap(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    _mk(src / "OK_15m.json", 50)
    _mk(dst / "OK_15m.json", 47)
    _mk(src / "GAP_15m.json", 80)
    _mk(dst / "GAP_15m.json", 40)
    _mk(src / "AHEAD_15m.json", 30)
    _mk(dst / "AHEAD_15m.json", 60)
    files, state = PU.build(src, {}, {"15m"}, 12)
    assert set(files) == {"OK_15m.json", "GAP_15m.json", "AHEAD_15m.json"}
    res = AP.apply_bundle({"files": files}, dst)
    assert res["gap"] == ["GAP_15m.json"] and not res["errors"]
    assert len(json.loads((dst / "OK_15m.json").read_text())) == 50
    assert len(json.loads((dst / "GAP_15m.json").read_text())) == 40
    assert len(json.loads((dst / "AHEAD_15m.json").read_text())) == 60
    again = AP.apply_bundle({"files": files}, dst)
    assert again["appended_rows"] == 0


def test_push_state_only_advances_for_changed_files(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _mk(src / "X_15m.json", 30)
    files, st = PU.build(src, {}, {"15m"}, 12)
    assert list(files) == ["X_15m.json"]
    files2, _ = PU.build(src, st, {"15m"}, 12)
    assert files2 == {}
    (src / "X_15m.json").write_text(json.dumps([_row(i) for i in range(31)]))
    files3, _ = PU.build(src, st, {"15m"}, 12)
    assert list(files3) == ["X_15m.json"]
