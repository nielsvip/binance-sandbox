"""USER 2026-10-15 (06.2): pilot NPZ failures captured as {npz sha, session count,
timestamp} in the failure ledger — capture only, never gates a launch."""

import json
import os
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from tools import v15_dispatch_job as D


def _mk_npz(path, days=30, per_day=32):
    base = 1_704_067_200_000  # ms = 2024-01-01T00:00:00Z (32x30m = 16h/day stays inside one UTC day)
    ts = []
    for d in range(days):
        ts += [base + d * 86400_000 + i * 1800_000 for i in range(per_day)]
    np.savez(path, timestamps=np.asarray(ts, dtype=np.float64), close=np.ones(len(ts)))


def test_identity_stock_sessions(tmp_path, monkeypatch):
    p = tmp_path / "AAA.npz"
    _mk_npz(p)
    rec = D._npz_identity("AAA", str(p))
    assert rec["bars"] == 30 * 32 and rec["sessions"] == 30 and len(rec["sha"]) == 32 and rec["last_bar"].endswith("Z")


def test_identity_crypto_has_no_sessions(tmp_path):
    p = tmp_path / "ADAUSDC.npz"
    _mk_npz(p, days=40)
    rec = D._npz_identity("ADAUSDC", str(p))
    assert rec["sessions"] is None and rec["bars"] == 40 * 32 and len(rec["sha"]) == 32


def test_identity_missing_file_never_raises(tmp_path):
    rec = D._npz_identity("ZZZ", str(tmp_path / "nope.npz"))
    assert rec["sha"] is None and rec["sessions"] is None and rec["bars"] is None


def test_npz_failure_cli_ledgers_only(tmp_path, monkeypatch):
    p = tmp_path / "GOOGL.npz"
    _mk_npz(p, days=20)
    lp = tmp_path / "fails.jsonl"
    monkeypatch.setenv("V15_NPZ_FAIL_LEDGER", str(lp))
    monkeypatch.setattr(sys, "argv", ["v15_dispatch_job.py", "--symbol", "GOOGL", "--npz-failure", "--npz", str(p), "--error", "NPZ has only 20 distinct stock sessions (need 30)"])
    assert D.main() == 0
    row = json.loads(lp.read_text().strip())
    assert row["event"] == "pilot-npz-failure" and row["sessions"] == 20 and len(row["sha"]) == 32 and row["ts"].endswith("Z") and "20 distinct" in row["error"]


def test_fast_push_local_ledgers_push(tmp_path, monkeypatch):
    ind = tmp_path / "backtest_v8" / "indicators"
    ind.mkdir(parents=True)
    _mk_npz(ind / "AAA.npz")
    monkeypatch.setattr(D, "ROOT", tmp_path)
    monkeypatch.setenv("V15_SCHED_RUN_STALE", "1")  # USER LAW 2026-10-20: the 2024 fixture NPZ is stale; this test is about the ledger, not the verdict
    monkeypatch.setattr(D, "tail_refresh", lambda symbol: {"tail": "current", "tail_s": 0.0})
    monkeypatch.setenv("V15_NPZ_FAIL_LEDGER", str(tmp_path / "fails.jsonl"))
    res = D.fast_push("AAA", "")
    assert res["ok"] is True and res["action"] == "fast30d-local"
    row = json.loads((tmp_path / "fails.jsonl").read_text().strip())
    assert row["event"] == "push" and row["sessions"] == 30 and len(row["sha"]) == 32


def test_stale_npz_refused_after_refresh(tmp_path, monkeypatch):
    """USER LAW 2026-10-20: a launch whose NPZ is still > V15_NPZ_MAX_AGE_MIN behind after the JIT refresh is REFUSED (kind retry, never a rebuild)."""
    ind = tmp_path / "backtest_v8" / "indicators"
    ind.mkdir(parents=True)
    _mk_npz(ind / "AAAUSDC.npz")
    monkeypatch.setattr(D, "ROOT", tmp_path)
    monkeypatch.delenv("V15_SCHED_RUN_STALE", raising=False)
    monkeypatch.setenv("V15_TAIL_WAIT_S", "0")
    monkeypatch.setenv("V15_DISPATCH_LOG", str(tmp_path / "d.log"))
    monkeypatch.setattr(D, "LOG", tmp_path / "d.log")
    monkeypatch.setattr(D, "tail_refresh", lambda symbol: {"tail": "current", "tail_s": 0.0})
    monkeypatch.setenv("V15_NPZ_FAIL_LEDGER", str(tmp_path / "fails.jsonl"))
    res = D.fast_push("AAAUSDC", "")
    assert res["ok"] is False and res["action"] == "stale-npz" and res["npz_age_min"] > 20
    ok, kind, _why, may_rebuild = D.prep_disposition(res)
    assert ok is False and kind == "retry" and may_rebuild is False
    assert '"event": "tail-verified"' in (tmp_path / "d.log").read_text()
