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
