"""klines_guard — 400d no-delete security: span, guarded_write, tripwire restore."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import klines_guard as K


def _rows(n, start="2025-01-01T00:00:00.000000Z"):
    import datetime
    t0 = datetime.datetime.fromisoformat(start.replace("Z", "+00:00"))
    return [{"timestamp": (t0 + datetime.timedelta(minutes=15 * i)).strftime("%Y-%m-%dT%H:%M:%S.000000Z"), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1} for i in range(n)]


def test_span_of():
    assert K.span_of(_rows(38500)) > 400
    assert K.span_of(_rows(100)) < 2
    assert K.span_of([]) == 0.0


def test_guarded_write(tmp_path):
    p = tmp_path / "A_15m.json"
    ok, _ = K.guarded_write(p, _rows(38500))
    assert ok and len(json.loads(p.read_text())) == 38500
    ok, why = K.guarded_write(p, _rows(1780))
    assert not ok and "REFUSED" in why
    assert len(json.loads(p.read_text())) == 38500
    ok, _ = K.guarded_write(p, _rows(39000))
    assert ok
    q = tmp_path / "B_15m.json"
    ok, _ = K.guarded_write(q, _rows(100))
    assert ok
    ok, _ = K.guarded_write(q, _rows(200))
    assert ok


def test_tripwire_restore(tmp_path, monkeypatch):
    import os
    bt = tmp_path / "klines_cache_backtest"
    bt.mkdir()
    (bt / "A_15m.json").write_text(json.dumps(_rows(38500)))
    monkeypatch.setattr(K, "ROOT", tmp_path)
    monkeypatch.setattr(K, "TARGETS", [bt])
    monkeypatch.setattr(K, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(K, "SNAP", bt / ".guard_snap")
    monkeypatch.setattr(K, "ALERT", tmp_path / "alert.json")
    r = K.tripwire(restore=True)
    assert r["shrunk"] == 0 and r["files"] == 1
    tmp = bt / "A_15m.json.tmp"
    tmp.write_text(json.dumps(_rows(1780)))
    os.replace(tmp, bt / "A_15m.json")
    r = K.tripwire(restore=True)
    assert r["shrunk"] == 1 and r["restored"] == 1
    assert len(json.loads((bt / "A_15m.json").read_text())) == 38500
    (bt / "A_15m.json").write_text(json.dumps(_rows(100)))
    r = K.tripwire(restore=True)
    assert r["shrunk"] == 1 and r["restored"] == 1
    assert len(json.loads((bt / "A_15m.json").read_text())) == 38500
    assert json.loads((tmp_path / "alert.json").read_text())["shrunk"][0]["file"].endswith("A_15m.json")
    r = K.tripwire(restore=False)
    assert r["shrunk"] == 0


def _snapenv(tmp_path, monkeypatch):
    bt = tmp_path / "klines_cache_backtest"
    bt.mkdir()
    monkeypatch.setattr(K, "ROOT", tmp_path)
    monkeypatch.setattr(K, "TARGETS", [bt])
    monkeypatch.setattr(K, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(K, "SNAP", bt / ".guard_snap")
    monkeypatch.setattr(K, "ALERT", tmp_path / "alert.json")
    return bt


def test_snapshot_is_compressed(tmp_path, monkeypatch):
    """USER 2026-10-16 "not multiple klines backups that consume terabytes": .guard_snap was a 15 GB RAW second copy
    of the 14 GB store even though the module's own docstring promised a hardlink snapshot."""
    bt = _snapenv(tmp_path, monkeypatch)
    (bt / "A_15m.json").write_text(json.dumps(_rows(38500)))
    K.tripwire(restore=True)
    gz = bt / ".guard_snap" / "A_15m.json.gz"
    assert gz.exists() and not (bt / ".guard_snap" / "A_15m.json").exists()
    assert gz.stat().st_size < (bt / "A_15m.json").stat().st_size / 3


def test_snapshot_skips_an_unchanged_file_by_one_stat(tmp_path, monkeypatch):
    bt = _snapenv(tmp_path, monkeypatch)
    (bt / "A_15m.json").write_text(json.dumps(_rows(38500)))
    K.tripwire(restore=True)
    gz = bt / ".guard_snap" / "A_15m.json.gz"
    first = gz.stat().st_mtime_ns
    K.tripwire(restore=True)
    assert gz.stat().st_mtime_ns == first, "an unchanged file must not be recompressed every minute"


def test_a_legacy_raw_snapshot_still_restores_then_is_dropped(tmp_path, monkeypatch):
    """The 2602 raw snapshots already on S1 must keep protecting their files until the gz one exists."""
    bt = _snapenv(tmp_path, monkeypatch)
    (bt / "A_15m.json").write_text(json.dumps(_rows(38500)))
    snap = bt / ".guard_snap"
    snap.mkdir()
    (snap / "A_15m.json").write_text(json.dumps(_rows(38500)))
    K.scan()
    K.STATE.write_text(json.dumps(K.scan()))
    (bt / "A_15m.json").write_text(json.dumps(_rows(100)))
    r = K.tripwire(restore=True)
    assert r["restored"] == 1 and len(json.loads((bt / "A_15m.json").read_text())) == 38500
    K.tripwire(restore=True)
    assert (snap / "A_15m.json.gz").exists() and not (snap / "A_15m.json").exists()


def test_prune_drops_inert_staging_snapshots_only(tmp_path, monkeypatch):
    """2026-10-16: 11132 raw snapshots (3.19 GB) of gateway/ and consolidated_klines/ — dirs dropped from protection
    on 2026-10-13 — sat in .guard_snap forever. The tripwire never scans or restores them, so they were dead weight.
    A snapshot whose source is GONE is the one case worth keeping."""
    bt = _snapenv(tmp_path, monkeypatch)
    (bt / "A_15m.json").write_text(json.dumps(_rows(38500)))
    (bt / "gateway").mkdir()
    (bt / "gateway" / "A_15m.json").write_text(json.dumps(_rows(100)))
    snap = bt / ".guard_snap"
    (snap / "gateway").mkdir(parents=True)
    (snap / "gateway" / "A_15m.json").write_text(json.dumps(_rows(100)))
    (snap / "DELETED_15m.json").write_text(json.dumps(_rows(38500)))
    K.tripwire(restore=True)
    assert not (snap / "gateway" / "A_15m.json").exists(), "a staging copy the guard never uses must go"
    assert (snap / "DELETED_15m.json").exists(), "the last copy of a vanished file must stay"
    assert (snap / "A_15m.json.gz").exists()


def test_a_raw_leftover_beside_a_current_gz_is_dropped(tmp_path, monkeypatch):
    """Observed live 2026-10-16: a guard run started before the deploy kept writing RAW snapshots, so .guard_snap held
    both forms (9.53 GB raw + 1.61 GB gz). The writer only drops the raw when it rewrites the gz."""
    bt = _snapenv(tmp_path, monkeypatch)
    (bt / "A_15m.json").write_text(json.dumps(_rows(38500)))
    K.tripwire(restore=True)
    snap = bt / ".guard_snap"
    assert (snap / "A_15m.json.gz").exists()
    (snap / "A_15m.json").write_text(json.dumps(_rows(38500)))
    K.tripwire(restore=True)
    assert not (snap / "A_15m.json").exists() and (snap / "A_15m.json.gz").exists()
