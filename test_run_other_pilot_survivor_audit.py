import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np


ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "other_pilot", ROOT / "tools" / "run_other_pilot_survivor_audit.py"
)
mod = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(mod)


def _metrics(alpha=2.0, tim=70.0, bh=25.0):
    return {
        "return_on_deployed_pct": bh + alpha,
        "bh_return_on_deployed_pct": bh,
        "deployed_alpha_vs_bh_or_cash_pp": alpha,
        "benchmark_floor_return_pct": max(0.0, bh),
        "honest_bh_multiple": (
            (bh + alpha) / bh if bh >= 20 else None
        ),
        "bh_magnitude_eligible": abs(bh) >= 20,
        "bh_ratio_eligible": bh >= 20,
        "exposure_weighted_tim_pct": tim,
        "fill_ratio": 1.0,
        "clamp_count": 0,
        "fill_count": 3,
        "exit_count": 2,
        "bars_flat_beyond_reclaim": 0,
        "insolvent": False,
        "entry_capacity_breach": False,
        "minimum_account_equity_usd": 9_000.0,
        "max_drawdown_account_pct": 10.0,
    }


def _fold(alpha=2.0, tim=70.0, bh=25.0, number=1):
    return {
        "fold": number,
        "validation_metrics": _metrics(alpha, tim, bh),
        "selected_curve": {"label": f"C{number}"},
        "causality": {"4h": {"source_timestamp_future_count": 0}},
    }


def test_fold_gate_uses_bh_or_cash_and_65_80_band():
    passing = mod.fold_gate(_fold(alpha=3, tim=65, bh=-30), 65, 80)
    assert passing["pass"]
    assert passing["benchmark_floor_pct"] == 0.0
    failing = mod.fold_gate(_fold(alpha=-1, tim=81, bh=25), 65, 80)
    assert not failing["pass"]
    assert "NOT_ABOVE_SIDE_BH_OR_CASH_ON_DEPLOYED_CAPITAL" in failing[
        "failures"
    ]
    assert "TIM_OUTSIDE_65_80" in failing["failures"]
    future = mod.fold_gate(
        _fold(alpha=3, tim=70, bh=25),
        65,
        80,
        {"4h": {"source_timestamp_future_count": 1}},
    )
    assert not future["pass"]
    assert "FUTURE_HTF" in future["failures"]


def test_npz_audit_fails_closed_without_parent_clock(tmp_path):
    ts = np.arange(1_000, dtype=np.int64) + 1_700_000_000
    np.savez(
        tmp_path / "VT.npz",
        timestamps=ts,
        synthetic_5m=np.ones(len(ts), dtype=np.uint8),
    )
    row = mod.audit_npz(
        "VT_LONG",
        tmp_path,
        {},
        1e9,
        datetime(2026, 7, 29, tzinfo=timezone.utc),
    )
    assert not row["valid"]
    assert "SYNTHETIC_PARENT_CLOCK_MISSING" in row["errors"]


def test_holdout_is_not_run_without_two_fold_discovery_survivor(
    monkeypatch, tmp_path
):
    calls = []

    def fake_run(command):
        calls.append(command)
        payload = {
            "manifest": {"npz_sha256": "abc"},
            "outer_folds": [
                _fold(alpha=2, tim=70, number=1),
                _fold(alpha=-1, tim=70, number=2),
            ],
        }
        return {"command": command, "returncode": 0}, payload

    monkeypatch.setattr(mod, "_run_profile", fake_run)
    args = SimpleNamespace(
        capacities=[4_000],
        exit_ns=[20],
        npz_dir=str(tmp_path),
        discovery_end="2026-01-01",
        random_curves=2,
        seed=7,
        tim_lo=65.0,
        tim_hi=80.0,
        tim_weight=4.0,
    )
    source = {
        "key": "NVDA_LONG",
        "symbol": "NVDA",
        "side": "LONG",
        "valid": True,
        "errors": [],
    }
    result = mod.screen_key("NVDA_LONG", source, args, tmp_path / "out")
    assert len(calls) == 1
    assert result["vector_holdout"] is None
    assert result["verdict"] == "NO_DISCOVERY_SURVIVOR_GRAY"


def test_vector_survivor_stays_gray_until_ordinary_engine_parity(
    monkeypatch, tmp_path
):
    calls = []

    def fake_run(command):
        calls.append(command)
        count = 2 if "--end" in command else 3
        payload = {
            "manifest": {"npz_sha256": "abc"},
            "outer_folds": [
                _fold(alpha=2 + i, tim=70, number=i + 1)
                for i in range(count)
            ],
        }
        return {"command": command, "returncode": 0}, payload

    monkeypatch.setattr(mod, "_run_profile", fake_run)
    args = SimpleNamespace(
        capacities=[4_000],
        exit_ns=[20],
        npz_dir=str(tmp_path),
        discovery_end="2026-01-01",
        random_curves=2,
        seed=7,
        tim_lo=65.0,
        tim_hi=80.0,
        tim_weight=4.0,
    )
    source = {
        "key": "NVDA_LONG",
        "symbol": "NVDA",
        "side": "LONG",
        "valid": True,
        "errors": [],
    }
    result = mod.screen_key("NVDA_LONG", source, args, tmp_path / "out")
    assert len(calls) == 2
    assert result["verdict"] == (
        "VECTOR_SURVIVOR_ORDINARY_PARITY_REQUIRED_GRAY"
    )
    assert result["ordinary_engine_parity"]["status"] == "REQUIRED"
    assert result["promotion_eligible"] is False
