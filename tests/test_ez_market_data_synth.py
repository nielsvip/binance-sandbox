"""Wall-clock kline synthesis in ez_market_data (USER 2026-10-22, TRUMPUSDC 1m/3m ages).

When mark-price ticks stall, the composer must carry the last price forward on
WALL CLOCK so minute buckets, _tick_ts and klines keep advancing. The 2026-10-22
defect re-stamped the STALE buf time (ages grew unbounded) and the strict sync
check then suppressed the symbol forever (one synth per outage).
"""
import time
import pytest
import ez_market_data as emd


def _seeded_market(monkeypatch):
    base = int(time.time()) - 3600
    hist_1m = {str(base + m * 60): 100.0 + m * 0.001 for m in range(60)}
    hist_3m = {str(base + t * 180): 100.0 + t * 0.003 for t in range(30)}
    monkeypatch.setattr(emd.KlineLoader, "load_history", staticmethod(lambda s, tf: hist_1m if tf == "1m" else (hist_3m if tf == "3m" else {})))
    m = emd.HighResMarketData()
    m.init_symbol_from_disk("TESTUSDC")
    return m


def test_wallclock_update_opens_new_buckets(monkeypatch):
    m = _seeded_market(monkeypatch)
    before = list(m.data["TESTUSDC"]["closes_1m"].keys())
    now = int(time.time())
    m.update("TESTUSDC", 101.5, float(now + 70))
    after = list(m.data["TESTUSDC"]["closes_1m"].keys())
    assert max(after) > max(before)
    assert m.data["TESTUSDC"]["last_tick_ts"] == float(now + 70)


def test_snapshot_tick_ts_is_wallclock(monkeypatch):
    m = _seeded_market(monkeypatch)
    now = float(int(time.time()))
    m.update("TESTUSDC", 102.0, now)
    snap = m.get_snapshot("TESTUSDC")
    assert snap is not None
    _c1, _c3, price, tick_ts = snap
    assert tick_ts == now
    assert price == 102.0
    assert _c1[-1] == 102.0


def test_timestamp_3m_only_when_computed():
    now = float(int(time.time()))
    full_1m = [100.0 + (i % 7) * 0.1 for i in range(60)]
    full_3m = [100.0 + (i % 5) * 0.2 for i in range(40)]
    res = emd.cpu_bound_calculation(full_1m, full_3m, 101.0, now)
    assert res is not None
    assert res["timestamp_3m"] == now
    thin = emd.cpu_bound_calculation(full_1m, [100.0] * 5, 101.0, now)
    assert thin is not None
    assert thin["timestamp_3m"] is None
    assert thin["k_3m"] == 50.0


def test_snapshot_needs_live_tick_first(monkeypatch):
    m = _seeded_market(monkeypatch)
    assert m.data["TESTUSDC"]["last_tick_ts"] == 0.0
    assert m.get_snapshot("TESTUSDC") is None


@pytest.mark.asyncio
async def test_wb_write_merges_and_counts(tmp_path):
    store = {"closes_1m": {1700000000 + m * 60: 50.0 for m in range(12)}, "closes_3m": {1700000000 + t * 180: 50.0 for t in range(12)}}
    n = await emd._wb_write("TESTUSDC", store, tmp_path)
    assert n == 2
    import orjson
    rows = orjson.loads((tmp_path / "TESTUSDC_1m.json").read_bytes())
    assert len(rows) == 12
    assert rows[-1]["close"] == 50.0


@pytest.mark.asyncio
async def test_wb_write_skips_thin_store(tmp_path):
    n = await emd._wb_write("TESTUSDC", {"closes_1m": {}, "closes_3m": None}, tmp_path)
    assert n == 0
    assert list(tmp_path.iterdir()) == []
