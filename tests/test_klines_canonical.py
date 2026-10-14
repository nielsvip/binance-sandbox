import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import klines_canonical as kc  # noqa: E402


def _k(t_ms, o, h, l, c, v, sec=900):
    return [t_ms, str(o), str(h), str(l), str(c), str(v), t_ms + sec * 1000 - 1, "0", 1, "0", "0", "0"]


def _row(t_ms, o, h, l, c, v):
    return {"timestamp": kc.iso(t_ms), "open": o, "high": h, "low": l, "close": c, "volume": v}


T0 = 1_790_000_100_000 // 900_000 * 900_000


def test_merge_closed_only_and_correction(tmp_path, monkeypatch):
    monkeypatch.setattr(kc, "WLOCK", tmp_path / "w.lock")
    monkeypatch.setattr(kc, "LOG", tmp_path / "log.jsonl")
    p = tmp_path / "XUSDC_15m.json"
    now = T0 + 3 * 900_000 + 10_000
    kl = [_k(T0, 1, 2, 0.5, 1.5, 10), _k(T0 + 900_000, 1.5, 2, 1, 1.8, 11), _k(T0 + 2 * 900_000, 1.8, 2, 1, 1.9, 12), _k(T0 + 3 * 900_000, 1.9, 2, 1.9, 1.95, 1)]
    a, c, rows = kc.merge_exchange(p, kl, now)
    assert (a, c) == (3, 0)
    assert len(rows) == 3  # forming bar (close_time >= now) never stored
    kl2 = [_k(T0 + 900_000, 1.5, 2, 1, 1.8, 11.5)]
    a, c, rows = kc.merge_exchange(p, kl2, now)
    assert (a, c) == (0, 1)
    assert rows[1]["volume"] == 11.5
    assert "canonical_correction" in (tmp_path / "log.jsonl").read_text()


def test_write_rows_grow_only(tmp_path):
    p = tmp_path / "f.json"
    rows = [_row(T0 + i * 900_000, 1, 1, 1, 1, 1) for i in range(5)]
    assert kc.write_rows(p, rows)[0]
    assert not kc.write_rows(p, rows[:4])[0]
    assert not kc.write_rows(p, rows[1:] + [_row(T0 + 9 * 900_000, 1, 1, 1, 1, 1)])[0]


def test_repair_file_corrects_inserts_keeps(tmp_path):
    store = tmp_path / "s.json"
    cache = tmp_path / "c.json"
    truth = [_row(T0 + i * 900_000, 10 + i, 11 + i, 9 + i, 10.5 + i, 100 + i) for i in range(6)]
    store.write_text(json.dumps(truth))
    bad = [dict(r) for r in truth[1:5]]
    bad[0]["volume"] = 9999.0  # USDT-perp style volume
    del bad[2]  # gap inside span
    bad.append(_row(T0 + 50 * 900_000, 1, 1, 1, 1, 1))  # a row the store does not hold -> kept
    cache.write_text(json.dumps(bad))
    new, corrected, inserted = kc.repair_file(cache, store)
    assert corrected == 1
    assert inserted == 2  # the gap + truth[5] after the cache's first bar; truth[0] (before the cache head) never prepended
    ts = [r["timestamp"] for r in new]
    assert truth[0]["timestamp"] not in ts
    assert new[0]["volume"] == truth[1]["volume"]
    assert ts[-1] == kc.iso(T0 + 50 * 900_000)
    assert len(new) == len(bad) + inserted


def test_pick_best_source_prefers_canonical(tmp_path, monkeypatch):
    import backtest_v8_precompute as bvp
    canon = tmp_path / "klines_canonical"
    legacy = tmp_path / "klines_cache"
    canon.mkdir()
    legacy.mkdir()
    n = 40
    good = [_row(T0 + i * 900_000, 1.0, 1.0, 1.0, 1.0, 1.0) for i in range(n)]
    longer = [_row(T0 - 100 * 900_000 + i * 900_000, 2.0, 2.0, 2.0, 2.0, 2.0) for i in range(n + 100)]
    (canon / "XUSDC_15m.json").write_text(json.dumps(good))
    (legacy / "XUSDC_15m.json").write_text(json.dumps(longer))
    monkeypatch.setattr(bvp, "CANONICAL_KLINES", canon)
    monkeypatch.delenv("NPZ_CANONICAL_SOURCE", raising=False)
    monkeypatch.delenv("NPZ_CANONICAL_REQUIRE_COMPLETE", raising=False)
    df, src = bvp._pick_best_source("XUSDC", "15m", [legacy], "crypto")
    assert src == "klines_cache"  # not complete yet (no _complete.json) -> legacy, one-time switch later
    (canon / "_complete.json").write_text(json.dumps({f"XUSDC_{t}": "x" for t in ("15m", "1h", "4h")}))
    df, src = bvp._pick_best_source("XUSDC", "15m", [legacy], "crypto")
    assert src == "klines_cache"  # D still missing
    (canon / "_complete.json").write_text(json.dumps({f"XUSDC_{t}": "x" for t in ("15m", "1h", "4h", "D")}))
    df, src = bvp._pick_best_source("XUSDC", "15m", [legacy], "crypto")
    assert src == "klines_canonical" and len(df) == n
    monkeypatch.setenv("NPZ_CANONICAL_SOURCE", "0")
    df, src = bvp._pick_best_source("XUSDC", "15m", [legacy], "crypto")
    assert src == "klines_cache"
    monkeypatch.delenv("NPZ_CANONICAL_SOURCE", raising=False)
    df, src = bvp._pick_best_source("XUSDC", "15m", [legacy], "tradier")
    assert src == "klines_cache"  # stocks never read the crypto canonical store
    df, src = bvp._pick_best_source("XUSDC", "1m", [legacy], "crypto")
    assert src is None or src == "klines_cache"


def test_klines_append_canonical_correction():
    import klines_append as ka
    existing = [{"timestamp": "2026-10-01T00:00:00.000000Z", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 999.0}]
    canon = [{"timestamp": "2026-10-01T00:00:00.000000Z", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0},
             {"timestamp": "2026-10-01T00:15:00.000000Z", "open": 1.5, "high": 2.0, "low": 1.0, "close": 1.8, "volume": 11.0}]
    out, n = ka.merge_klines(existing, canon)
    assert n == 1 and out[0]["volume"] == 999.0  # legacy: append-only, never corrects
    out, n = ka.merge_klines(existing, canon, correct=True)
    assert n == 2 and out[0]["volume"] == 10.0 and len(out) == 2


def test_mark_complete_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(kc, "WLOCK", tmp_path / "w.lock")
    kc.mark_complete(["AUSDT_15m"], store=tmp_path)
    first = json.loads((tmp_path / "_complete.json").read_text())
    kc.mark_complete(["AUSDT_15m", "AUSDT_1h"], store=tmp_path)
    cur = json.loads((tmp_path / "_complete.json").read_text())
    assert cur["AUSDT_15m"] == first["AUSDT_15m"] and "AUSDT_1h" in cur


def test_stocks_repair_correct_only(tmp_path):
    c15 = tmp_path / "AAA_15m.json"
    cd = tmp_path / "AAA_D.json"
    t = 1790000100 // 900 * 900
    c15.write_text(json.dumps([{"timestamp": kc.iso(t * 1000), "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 5.0},
                               {"timestamp": kc.iso((t + 900) * 1000), "open": 2.0, "high": 2.0, "low": 2.0, "close": 2.0, "volume": 6.0}]))
    cd.write_text(json.dumps([]))
    truth = {"window": ["2026-01-01", "2026-12-31"], "m15": [{"timestamp": t, "open": 1.0, "high": 1.5, "low": 0.9, "close": 1.2, "volume": 50},
                                                                 {"timestamp": t + 1800, "open": 3, "high": 3, "low": 3, "close": 3, "volume": 1}], "daily": []}
    plan = kc.stocks_repair_file(c15, cd, truth)
    rows, n = plan[c15]
    assert n == 1 and len(rows) == 2 and rows[0]["close"] == 1.2 and rows[1]["close"] == 2.0  # no insert of t+1800
