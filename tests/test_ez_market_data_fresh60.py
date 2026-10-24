"""1m/3m never more than ~1m old (USER 2026-10-24, DUSKUSDT 3m 0/10 vs real 74/80).

Flat wall-clock synthesis kept opening identical minute buckets, so stoch
collapsed to 0/0 mid-throttle. The stored history must stay real: synthetic
passes overwrite the forming bar only, and the REST loop owns real buckets.
"""
import time
import ez_market_data as emd


def _seeded(monkeypatch, n1=60, n3=40):
    base = int(time.time()) - 7200
    h1 = {str(base + m * 60): 100.0 + (m % 7) * 0.1 for m in range(n1)}
    h3 = {str(base + t * 180): 100.0 + (t % 5) * 0.2 for t in range(n3)}
    monkeypatch.setattr(emd.KlineLoader, "load_history", staticmethod(lambda s, tf: h1 if tf == "1m" else (h3 if tf == "3m" else {})))
    m = emd.HighResMarketData()
    m.init_symbol_from_disk("T1USDC")
    return m


def test_update_synthetic_never_opens_buckets(monkeypatch):
    m = _seeded(monkeypatch)
    k1 = max(m.data["T1USDC"]["closes_1m"])
    k3 = max(m.data["T1USDC"]["closes_3m"])
    for _ in range(5):
        m.update_synthetic("T1USDC", 101.0, float(k1 + 600))
    assert max(m.data["T1USDC"]["closes_1m"]) == k1
    assert max(m.data["T1USDC"]["closes_3m"]) == k3
    assert m.data["T1USDC"]["last_tick_ts"] == float(k1 + 600)


def test_update_still_opens_buckets_for_real_ticks(monkeypatch):
    m = _seeded(monkeypatch)
    k1 = max(m.data["T1USDC"]["closes_1m"])
    m.update("T1USDC", 102.0, float(k1 + 70))
    assert max(m.data["T1USDC"]["closes_1m"]) > k1


def test_parse_rest_klines():
    rows = [[1700000000000 + i * 60000, "1", "1", "1", str(50.0 + i), "0", 0, "0", 0, "0", "0", "0"] for i in range(3)]
    bars = emd.parse_rest_klines(rows)
    assert bars == {1700000000: 50.0, 1700000060: 51.0, 1700000120: 52.0}
    assert emd.parse_rest_klines(None) == {}
    assert emd.parse_rest_klines([["bad"]]) == {}


def test_apply_rest_bars_replaces_flat_and_caps_length(monkeypatch):
    m = _seeded(monkeypatch)
    for k in m.data["T1USDC"]["closes_1m"]:
        m.data["T1USDC"]["closes_1m"][k] = 99.0
    bars = {int(time.time()) - 59 * 60 + i * 60: 100.0 + i * 0.01 for i in range(60)}
    n = m.apply_rest_bars("T1USDC", "1m", bars)
    assert n == 60
    assert len(m.data["T1USDC"]["closes_1m"]) <= 180
    assert abs(m.data["T1USDC"]["last_price"] - (100.0 + 59 * 0.01)) < 1e-9
