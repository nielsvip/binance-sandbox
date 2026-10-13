"""STORM GUARD 2026-10-13: per_sym JSON views (58-109MB, rewritten ~1/min)
must be parsed at most ONCE per file revision no matter how many sym_sides
the live loop requests. Before the fix, every uncached sym_side under a
fresh revision re-parsed the whole file; the live _cfg hot path did hundreds
of 100MB parses per cycle and froze the tradier event loops for minutes
(zero fills, BROKER_UNREACHABLE_ORDERS, 120s+ ORDER_STALL_SKIP — proven via
a live faulthandler stack ending in json.loads). Same data + same freshness,
only redundant parses gone; retained cache stays bounded to requested
sym_sides (box is RAM-pressed, no full-dict retention)."""
import json
import os
import time

from json import loads as _real_loads

import per_sym_store as ps


def _make_file(tmp_path, n=30):
    p = tmp_path / "active_config.json"
    p.write_text(json.dumps({f"S{i:03d}_LONG": {"overrides": {f"K{i}": i}} for i in range(n)}))
    return p


def _counter(monkeypatch):
    calls = {"n": 0}
    real_loads = json.loads

    def counting(s, *a, **k):
        if isinstance(s, str) and "S000_LONG" in s:
            calls["n"] += 1
        return real_loads(s, *a, **k)

    monkeypatch.setattr(ps.json, "loads", counting)
    return calls


def test_prime_parses_once_then_entries_are_free(tmp_path, monkeypatch):
    ps._JSON_FALLBACK_CACHE.clear()
    p = _make_file(tmp_path)
    calls = _counter(monkeypatch)
    want = {f"S{i:03d}_LONG" for i in range(20)}
    assert ps._prime_one_file(p, want) == 1
    assert calls["n"] == 1
    for s in want:
        assert ps._json_fallback_entry(p, s)["overrides"] == {f"K{int(s[1:4])}": int(s[1:4])}
    assert calls["n"] == 1
    # repeat prime under same revision: zero parses
    assert ps._prime_one_file(p, want) == 0
    assert calls["n"] == 1


def test_prime_picks_up_rewrite_once(tmp_path, monkeypatch):
    ps._JSON_FALLBACK_CACHE.clear()
    p = _make_file(tmp_path)
    calls = _counter(monkeypatch)
    want = {f"S{i:03d}_LONG" for i in range(10)}
    ps._prime_one_file(p, want)
    assert calls["n"] == 1
    time.sleep(0.02)
    data = _real_loads(p.read_text())
    data["S000_LONG"] = {"overrides": {"K0": 999}}
    p.write_text(json.dumps(data))
    os.utime(p, None)
    assert ps._prime_one_file(p, want) == 1
    assert calls["n"] == 2
    assert ps._json_fallback_entry(p, "S000_LONG")["overrides"] == {"K0": 999}
    assert calls["n"] == 2


def test_missing_symbol_negative_cached(tmp_path, monkeypatch):
    ps._JSON_FALLBACK_CACHE.clear()
    p = _make_file(tmp_path)
    calls = _counter(monkeypatch)
    assert ps._json_fallback_entry(p, "NOPE_LONG") is None
    assert calls["n"] == 1
    assert ps._json_fallback_entry(p, "NOPE_LONG") is None
    assert calls["n"] == 1


def test_absent_survives_rewrite_within_ttl(tmp_path, monkeypatch):
    ps._JSON_FALLBACK_CACHE.clear()
    ps._JSON_ABSENT_CACHE.clear()
    p = _make_file(tmp_path)
    calls = _counter(monkeypatch)
    assert ps._json_fallback_entry(p, "NOPE_LONG") is None
    assert calls["n"] == 1
    # rewrite (fresh sig): absent-TTL must avoid a second full parse
    time.sleep(0.02)
    p.write_text(p.read_text())
    os.utime(p, None)
    assert ps._json_fallback_entry(p, "NOPE_LONG") is None
    assert calls["n"] == 1
    # a present sym still parses once on the fresh sig, then is cached
    assert ps._json_fallback_entry(p, "S001_LONG")["overrides"] == {"K1": 1}
    assert calls["n"] == 2
    assert ps._json_fallback_entry(p, "S001_LONG")["overrides"] == {"K1": 1}
    assert calls["n"] == 2


def test_venue_file_order(tmp_path):
    assert ps._json_file_order("AAPL_LONG")[0] == ps.STOCKS_JSON
    assert ps._json_file_order("AAPL_SHORT")[-1] == ps.CRYPTO_JSON
    assert ps._json_file_order("BTCUSDC_LONG")[0] == ps.CRYPTO_JSON
