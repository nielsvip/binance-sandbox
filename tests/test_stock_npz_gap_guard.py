"""BIBLE §109 (2026-10-14): stock NPZ gap guard — refuse/flag < 30 sessions or > 5-day gap in the last 60 days."""
import json
import os
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import stock_npz_gap_guard as G


def _rth_ts(days):
    out = []
    for d in days:
        start = pd.Timestamp(d, tz="America/New_York") + pd.Timedelta(hours=9, minutes=30)
        out.extend(int(t.timestamp()) for t in pd.date_range(start, periods=26, freq="15min"))
    return np.array(out, dtype=np.int64)


def _sessions(end="2026-10-14", n=60):
    return list(pd.bdate_range(end=end, periods=n).date)


@pytest.fixture(autouse=True)
def _alert_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "ALERT_DIR", tmp_path / "alerts")
    monkeypatch.delenv("STOCK_NPZ_GAP_GUARD", raising=False)
    yield tmp_path / "alerts"


def test_complete_window_passes(_alert_dir):
    ok, info = G.gate("AAPL", _rth_ts(_sessions()), "test")
    assert ok and info["ok"] and info["sessions"] >= 40 and info["max_gap_days"] <= 4
    assert not (_alert_dir / "stock_npz_gap_AAPL.json").exists()


def test_month_hole_refused_and_alerted(_alert_dir):
    days = [d for d in _sessions() if not (pd.Timestamp("2026-08-27").date() <= d <= pd.Timestamp("2026-09-23").date())]
    ok, info = G.gate("GOOGL", _rth_ts(days), "test")
    assert not ok and info["max_gap_days"] > 5
    rec = json.loads((_alert_dir / "stock_npz_gap_GOOGL.json").read_text())
    assert rec["refused"] is True and rec["symbol"] == "GOOGL" and rec["gaps"]
    assert (_alert_dir / "stock_npz_gap.jsonl").read_text().count("GOOGL") == 1


def test_one_row_stub_refused(_alert_dir):
    ok, info = G.gate("ALMU", np.array([1791898200], dtype=np.int64), "test")
    assert not ok and info["sessions"] == 1


def test_short_history_refused_even_without_gap():
    ok, info = G.gate("NEWCO", _rth_ts(_sessions(n=20)), "test")
    assert not ok and "sessions" in info["reason"]


def test_holiday_weekend_gap_allowed():
    days = [d for d in _sessions() if d != pd.Timestamp("2026-09-07").date()]  # Labor Day: Fri -> Tue = 4 days
    ok, info = G.gate("MSFT", _rth_ts(days), "test")
    assert ok and info["max_gap_days"] == 4


def test_env_off_flags_but_allows(_alert_dir, monkeypatch):
    monkeypatch.setenv("STOCK_NPZ_GAP_GUARD", "0")
    ok, _ = G.gate("NVDA", np.array([1791898200], dtype=np.int64), "test")
    assert ok
    assert json.loads((_alert_dir / "stock_npz_gap_NVDA.json").read_text())["refused"] is False


def test_gate_npz_reads_file(tmp_path):
    p = tmp_path / "PLTR.npz"
    np.savez(p, timestamps=_rth_ts(_sessions(n=25)), close=np.ones(25 * 26, dtype=np.float32))
    ok, info = G.gate_npz("PLTR", p, "test")
    assert not ok and info["sessions"] == 25


def test_builder_and_dispatch_are_wired():
    root = Path(__file__).resolve().parents[1]
    src = (root / "backtest_v8_precompute.py").read_text()
    assert "from tools.stock_npz_gap_guard import gate as _gap_gate" in src and "SKIP save — STOCK_NPZ_GAP" in src
    disp = (root / "tools" / "v15_dispatch_job.py").read_text()
    assert "gate_npz(symbol, live, \"v15_dispatch_job.fast_push\")" in disp and '"gap-refused"' in disp
    gen = (root / "npz_live_generator.py").read_text()
    assert "NOT creating a 1-row stock stub" in gen
