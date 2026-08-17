import numpy as np
import pytest

from tools.chronological_vector_search import DEFAULT_MSTR_SHORT_RECIPE, candidate_admission, simulate


def test_chronological_simulator_emits_full_stateful_ledger_and_later_reentry():
    n = 8
    ts = np.arange(n, dtype=np.int64) * 300 + 1_700_000_000
    data = {
        "timestamps": ts,
        "close": np.array([100, 99, 98, 97, 96, 95, 94, 93], dtype=float),
        "atr_5m": np.ones(n),
        "wt1_15m": np.array([0, 0, 1, -1, -1, -1, -1, -1], dtype=float),
        "wt2_15m": np.zeros(n),
        "wt1_5m": np.zeros(n), "wt2_5m": np.zeros(n),
        "wt1_1h": -np.ones(n), "wt2_1h": np.zeros(n),
        "wt1_4h": -np.ones(n), "wt2_4h": np.zeros(n),
        "dc_high_5m": np.full(n, 110.0), "dc_low_5m": np.full(n, 90.0),
        "dc_basis_5m": np.full(n, 100.0), "bb_pct_b_5m": np.full(n, .5),
        "lrL_pct_b_D": np.full(n, .75), "lrL_pct_b_4h": np.full(n, .75),
        "lrL_pct_b_1h": np.full(n, .75),
        "lrL_slope_D": np.zeros(n), "lrL_slope_4h": np.zeros(n), "lrL_slope_1h": np.zeros(n),
    }
    for tf in ("15m", "1h", "4h", "D"):
        data[f"timestamp_{tf}"] = np.zeros(n, dtype=np.int64)
    recipe = {**DEFAULT_MSTR_SHORT_RECIPE, "REENTRY_B12_WT_MOM_ENABLED": True,
              "REENTRY_COOLDOWN_BARS": 1}
    result = simulate(data, recipe, symbol="TEST", side="SHORT")

    required = {"ts", "type", "side", "qty", "price", "reason", "pnl_pct",
                "position_qty_after", "override_fingerprint"}
    assert result["evidence"] == "VECTOR_RESEARCH_NOT_EXACT_V8"
    assert all(required <= set(event) for event in result["events"])
    assert any(event["type"] == "CLOSE" and event["reason"] == "MTF_WT_DIRECT_EXIT_15m"
               for event in result["events"])
    assert any(event["type"] == "REENTRY" for event in result["events"])
    assert result["causal_audit"]["no_future_completed_htf"]
    assert result["reentry_reclaim_audit"]["status"] == "PASS"
    assert result["metrics"]["closes_per_month"] > 0
    with pytest.raises(ValueError, match="exact integer"):
        simulate(data, {**recipe, "WHOLE_SHARE_UNITS": 1.5}, symbol="TEST", side="SHORT")


def test_candidate_admission_requires_activity_tim_and_capacity():
    metrics = {"closes_per_month": 0.5, "tim_pct": 100.0,
               "max_marked_notional_usd": 10001.0, "capacity_usd": 10000.0,
               "marked_equity_max_drawdown_pct": 50.1}
    result = candidate_admission(metrics, "E1_4OF5")
    assert result["status"] == "REJECTED"
    assert set(result["reasons"]) == {
        "CLOSES_PER_MONTH_LT_10", "TIM_OUTSIDE_20_75_PCT",
        "MARKED_NOTIONAL_EXCEEDS_CAPACITY", "MARKED_DRAWDOWN_GT_50_PCT",
    }
