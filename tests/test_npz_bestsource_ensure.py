"""Best-complete-source selection + pilot-start ensure (USER 2026-10-12)."""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import backtest_v8_precompute as P
import v15_npz_ensure as E


def _klines(path, n, end_ts, step=900):
    rows = [
        {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(end_ts - (n - 1 - i) * step)),
         "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10.0}
        for i in range(n)
    ]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(rows))


def test_best_source_wins_no_union(tmp_path):
    now = time.time()
    a, b = tmp_path / "sA", tmp_path / "sB"
    _klines(a / "TST_15m.json", 500, now - 900)   # fresh + long
    _klines(b / "TST_15m.json", 100, now - 900)   # fresh + short
    df, src = P._pick_best_source("TST", "15m", [a, b], "crypto")
    assert src == "sA" and len(df) == 500  # single source, never merged


def test_fresh_beats_stale_long(tmp_path):
    now = time.time()
    a, b = tmp_path / "sA", tmp_path / "sB"
    _klines(a / "TST_15m.json", 2000, now - 30 * 86400)  # long but 30d stale
    _klines(b / "TST_15m.json", 500, now - 900)          # fresh
    df, src = P._pick_best_source("TST", "15m", [a, b], "crypto")
    assert src == "sB"


def test_legacy_union_flag_restores_merge(tmp_path, monkeypatch):
    now = time.time()
    a, b = tmp_path / "sA", tmp_path / "sB"
    _klines(a / "TST_15m.json", 100, now - 200 * 900)
    _klines(b / "TST_15m.json", 100, now - 900)
    monkeypatch.setenv("NPZ_SOURCE_UNION_LEGACY", "1")
    df, src = P._pick_best_source("TST", "15m", [a, b], "crypto")
    assert src == "union-legacy" and len(df) == 200


def test_ensure_sym_parsing_and_never_blocks():
    assert E.base_syms(["AAPL_LONG", "AVAXUSDC_SHORT", "X"]) == ["AAPL", "AVAXUSDC", "X"]
    assert E.ensure([], dry_run=True) is True
