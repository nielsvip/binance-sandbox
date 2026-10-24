"""Loop-stall file/parse fixes (2026-10-24 USER unlock+fix-all).

The event loop averaged 10.5 s blocks: get_current_price re-parsed up to 14
JSON files per symbol per call (180+ dumps in raw_decode), deteriorate did an
O(all positions) identity scan per call (188 dumps), and last_events.json
(2 MB, unchanged since Sep 30) was re-parsed every 60 s per process (50+
dumps). All three fixes return byte-identical values; these tests pin that.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import utils as _u  # noqa: E402


def test_cached_json_file_hit_miss_invalidate(tmp_path):
    p = tmp_path / "a.json"
    p.write_text(json.dumps({"x": 1}))
    first = _u._cached_json_file(p)
    assert first == {"x": 1}
    assert _u._cached_json_file(p) is first  # cache hit: same object, no re-parse
    p.write_text(json.dumps({"x": 2}))
    assert _u._cached_json_file(p) == {"x": 2}  # mtime+size changed -> re-read


def test_cached_json_file_missing_raises_like_open(tmp_path):
    try:
        _u._cached_json_file(tmp_path / "nope.json")
    except FileNotFoundError:
        return
    raise AssertionError("missing file must raise (callers rely on try/except)")


def test_deteriorate_key_passthrough_equals_scan():
    import ez_positions_service as eps

    cls = None
    for name in dir(eps):
        obj = getattr(eps, name)
        if isinstance(obj, type) and hasattr(obj, "deteriorate_reentry_amounts"):
            cls = obj
            break
    assert cls is not None
    key = "tst:TSTUSDT_LONG"
    pos = SimpleNamespace(symbol="TSTUSDT")

    def run(pass_key):
        stub = SimpleNamespace(
            positions_by_account={"tst": {key: pos}},
            reentry_data={key: {"reentry_amount": 1000.0}},
            save_reentry_data=lambda k: (_ for _ in ()).throw(AssertionError("no save in unit test")),
        )
        cls.deteriorate_reentry_amounts(stub, pos, 0.5, 10.0, pass_key)
        return stub.reentry_data[key]["reentry_amount"]

    via_key = run(key)
    via_scan = run(None)
    assert via_key == via_scan
    sps = float(eps.config.START_POSITION_SIZE)
    assert abs(via_key - (1000.0 - (1000.0 - 2 * sps / 10.0) * 0.5)) < 1e-9


def test_last_events_refresh_skips_unchanged(tmp_path, monkeypatch):
    import ez_positions_quick as eq

    data = tmp_path / "data"
    data.mkdir()
    f = data / "last_events.json"
    f.write_text(json.dumps({"AAA": {"3m": {}}}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(eq, "_last_events_cache", {}, raising=False)
    monkeypatch.setattr(eq, "_last_events_cache_time", 0.0, raising=False)
    monkeypatch.setattr(eq, "_last_events_cache_sig", None, raising=False)
    calls = []
    orig = eq.safe_json_loads

    def counting(raw):
        calls.append(1)
        return orig(raw)

    monkeypatch.setattr(eq, "safe_json_loads", counting)
    eq._refresh_last_events_cache()
    assert eq._last_events_cache == {"AAA": {"3m": {}}}
    assert len(calls) == 1
    eq._refresh_last_events_cache()
    assert len(calls) == 1  # unchanged file -> no re-parse
    f.write_text(json.dumps({"AAA": {"3m": {}}, "BBB": {}}))
    eq._refresh_last_events_cache()
    assert len(calls) == 2
    assert "BBB" in eq._last_events_cache
