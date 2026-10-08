import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import v15_causal_worker as W  # noqa: E402


def test_ablation_keys_refused_without_momentary():
    with pytest.raises(SystemExit):
        W._refuse_ablation(["ABLATION_DISABLE_HEDGE"], momentary=False)


def test_ablation_keys_allowed_only_with_momentary():
    W._refuse_ablation(["ABLATION_DISABLE_HEDGE"], momentary=True)


def test_non_ablation_keys_always_allowed():
    W._refuse_ablation(["WT_15M_BOUNCE_OPEN_ENABLED"], momentary=False)


def test_row_causal_sign_and_canonical_columns():
    res = {"trades": 10, "gain_pct": 5.0, "pool_sharpe": 0.3, "valid": True, "max_dd_pct": 2.0}
    row = W._row("XYZUSDT_LONG", 365, "without", "K", True, None, res, base_gain=8.0)
    assert row["causal_delta_gain"] == 3.0
    assert row["n_syms"] == 1 and row["years"] == 1.0
    assert row["avg_gain_trade"] == 0.5
    assert all(c in row for c in W.CANONICAL)


def test_merge_keeps_only_valid_without_rows(tmp_path, monkeypatch):
    d = tmp_path / "worker"
    d.mkdir()
    (d / "A_30d.csv").write_text(
        "role,key,symside,window_days,trades,gain_pct,valid,causal_delta_gain\n"
        "without,K1,A,30,5,1.0,True,2.0\n"
        "without,K1,A,30,5,1.0,False,9.0\n"
        "base,,A,30,5,1.0,True,\n")
    monkeypatch.setattr(W, "WORKER_DIR", d)
    monkeypatch.setattr(W, "CAUSAL_DIR", tmp_path)
    monkeypatch.setattr(W, "INDEX_PATH", tmp_path / "causal_index.json")
    summary = W.merge()
    assert summary["K1"]["n"] == 1 and summary["K1"]["mean_causal_delta_gain"] == 2.0
    assert json.loads((tmp_path / "causal_index.json").read_text())["keys"]["K1"]["share_helpful"] == 1.0
